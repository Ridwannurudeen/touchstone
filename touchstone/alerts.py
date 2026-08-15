"""One webhook, and a credential that appears in exactly one place.

Alerting is where secrets leak, because an alert's whole purpose is to carry information out
of the system at the moment something has gone wrong — which is also the moment stack traces
get attached, URLs get logged, and payloads get pasted into issues. So the rule here is
narrow and absolute: the credential is read from the environment, sent only in an
``Authorization`` header, and never enters a URL, a body, an exception message, a repr, a
subprocess argument, or any durable record.

The body is deliberately poor in detail. It carries an asset, a severity, a stable event
code and hashes — never arbitrary exception text and never source URLs. An alert is a
signal to go and look, not a transport for evidence, and prose assembled from exceptions is
how a source URL or a file path ends up in a third-party service.

What this does not promise: guaranteed delivery, retry until success, paging escalation, or
failover between providers. It sends one request and reports honestly whether it left.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from touchstone.quantities import finite_positive
from touchstone.signing import canonical_json_bytes


ALERT_VERSION = "touchstone.alert.v1"
WEBHOOK_URL_ENV = "TOUCHSTONE_ALERT_WEBHOOK_URL"
WEBHOOK_TOKEN_ENV = "TOUCHSTONE_ALERT_TOKEN"

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 65_536


class Severity(str, Enum):
    """How much of someone's night this is worth."""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class Event(str, Enum):
    """Stable codes, so an operator can grep a year of alerts for one condition.

    Codes rather than prose because prose gets edited, and an alert whose text changed
    between releases cannot be counted. Every trigger the operations document commits to
    appears here exactly once.
    """

    HEARTBEAT_STALE = "HEARTBEAT_STALE"
    RESTART_FAILED = "RESTART_FAILED"
    EPOCH_MISSED = "EPOCH_MISSED"
    PUBLICATION_UNRESOLVED = "PUBLICATION_UNRESOLVED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PUBLISHER_STATE_UNEXPECTED = "PUBLISHER_STATE_UNEXPECTED"
    GAS_RUNWAY_SHORT = "GAS_RUNWAY_SHORT"
    BACKUP_MISSING = "BACKUP_MISSING"
    RESTORE_REHEARSAL_FAILED = "RESTORE_REHEARSAL_FAILED"
    RECOVERED = "RECOVERED"


class AlertError(RuntimeError):
    """An alert could not be built or could not be sent."""


@dataclass(frozen=True, slots=True)
class Webhook:
    """A validated endpoint and the header name its credential travels in."""

    url: str
    token: str

    def __repr__(self) -> str:
        # The default dataclass repr would print the token, and a repr reaches logs,
        # debuggers and exception context without anyone choosing to put it there.
        return f"Webhook(url={self.url!r}, token=<redacted>)"


def webhook_from_env(environ: Mapping[str, str] | None = None) -> Webhook:
    """Read and validate the endpoint and credential, refusing anything ambiguous."""
    source = os.environ if environ is None else environ
    url = source.get(WEBHOOK_URL_ENV)
    token = source.get(WEBHOOK_TOKEN_ENV)
    if not url:
        raise AlertError(f"{WEBHOOK_URL_ENV} is not set")
    if not token:
        raise AlertError(f"{WEBHOOK_TOKEN_ENV} is not set")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AlertError("the webhook URL must be absolute HTTPS")
    if parsed.username or parsed.password:
        raise AlertError("the webhook URL must not carry credentials")
    if parsed.query or parsed.fragment:
        # A query is where a webhook secret is usually smuggled, and query strings are
        # logged by every proxy in the path. Refusing the shape refuses the habit.
        raise AlertError("the webhook URL must not carry a query or fragment")
    return Webhook(url=url, token=token)


def build(
    *,
    event: Event,
    severity: Severity,
    asset_key: str,
    observed_at: str,
    detail_code: str | None = None,
    incident_hash: str | None = None,
) -> dict[str, object]:
    """Build the exact alert body. Nothing here is assembled from an exception."""
    if not isinstance(event, Event) or not isinstance(severity, Severity):
        raise AlertError("event and severity must be the declared enumerations")
    if not isinstance(asset_key, str) or not asset_key:
        raise AlertError("an alert must name its asset")
    if not isinstance(observed_at, str) or not observed_at.endswith("Z"):
        raise AlertError("observed_at must be a normalized UTC timestamp")
    if detail_code is not None and not _is_code(detail_code):
        # A "detail" free-text field is how exception text arrives, so it is a code.
        raise AlertError("detail_code must be uppercase A-Z, digits and underscores")
    if incident_hash is not None and not _is_digest(incident_hash):
        raise AlertError("incident_hash must be 64 lowercase hexadecimal characters")
    return {
        "asset_key": asset_key,
        "detail_code": detail_code,
        "event": event.value,
        "incident_hash": incident_hash,
        "observed_at": observed_at,
        "severity": severity.value,
        "version": ALERT_VERSION,
    }


def fingerprint(body: Mapping[str, object]) -> str:
    """A non-secret identity for one condition, for deduplication between checks.

    Deliberately excludes the timestamp: the same condition observed a minute later is the
    same condition, and an alert that fires every check is an alert that gets muted.
    """
    import hashlib  # noqa: PLC0415 - local, this is the only use in the module

    material = {
        key: body[key] for key in ("asset_key", "detail_code", "event", "severity")
    }
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


def send(
    body: Mapping[str, object],
    webhook: Webhook,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: object | None = None,
) -> int:
    """Send one alert. Returns the HTTP status; raises for anything that stopped it.

    Redirects are refused rather than followed. A redirect would re-send the
    ``Authorization`` header to whatever host the response named, which turns one
    compromised or misconfigured endpoint into credential disclosure.
    """
    seconds = finite_positive(timeout, "timeout")
    payload = canonical_json_bytes(dict(body))
    request = urllib.request.Request(
        webhook.url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {webhook.token}",
            "Content-Type": "application/json",
            "User-Agent": "touchstone/0.1.0",
        },
    )
    client = urllib.request.build_opener(_NoRedirect()) if opener is None else opener
    try:
        with client.open(request, timeout=seconds) as response:
            response.read(MAX_RESPONSE_BYTES)
            return int(response.status)
    except urllib.error.HTTPError as error:
        error.close()
        # The status, not the body. A failing endpoint's response body is attacker- or
        # vendor-controlled text that would otherwise be logged verbatim.
        raise AlertError(f"the webhook answered HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AlertError(f"the webhook could not be reached: {type(error).__name__}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _is_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(character in _CODE_ALPHABET for character in value)
    )


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


_CODE_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def render(body: Mapping[str, object]) -> str:
    """A one-line operator-readable form, safe to print into a supervisor journal."""
    return json.dumps(dict(body), sort_keys=True, separators=(",", ":"))
