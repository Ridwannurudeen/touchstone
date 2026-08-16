"""Alerting is where secrets leak, so most of these tests are about the credential.

An alert exists to carry information out of the system at the exact moment something has
gone wrong — which is also the moment stack traces get attached and payloads get pasted
into issues. The assertions below are therefore mostly negative: what must *not* be in the
URL, the body, the exception, or the repr.
"""

from __future__ import annotations

import io
import urllib.error
from urllib.parse import urlsplit

import pytest

from touchstone.alerts import (
    WEBHOOK_TOKEN_ENV,
    WEBHOOK_URL_ENV,
    AlertError,
    Event,
    Severity,
    Webhook,
    build,
    fingerprint,
    render,
    send,
    webhook_from_env,
)
from touchstone.signing import strict_json_loads


TOKEN = "s3cret-do-not-leak-me"
URL = "https://alerts.example.invalid/hook/abc"
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
AT = "2026-08-15T09:00:00Z"


def env(**changes: str) -> dict[str, str]:
    value = {WEBHOOK_URL_ENV: URL, WEBHOOK_TOKEN_ENV: TOKEN}
    value.update(changes)
    return value


def alert(**changes: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "event": Event.HEARTBEAT_STALE,
        "severity": Severity.CRITICAL,
        "asset_key": ASSET,
        "observed_at": AT,
    }
    arguments.update(changes)
    return build(**arguments)


class Recorder:
    """Captures the request instead of sending it, so the wire form can be asserted."""

    def __init__(self, status: int = 204) -> None:
        self.status = status
        self.request = None

    def open(self, request, timeout=None):  # noqa: A003 - urllib's opener protocol
        self.request = request
        self.timeout = timeout
        return _Response(self.status)


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self._body = io.BytesIO(b"")

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_credential_travels_only_in_the_authorization_header() -> None:
    """The one place it is allowed to be, and nowhere else in the request."""
    recorder = Recorder()

    send(alert(), webhook_from_env(env()), opener=recorder)

    request = recorder.request
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in request.full_url
    assert TOKEN not in request.data.decode()
    assert TOKEN not in urlsplit(request.full_url).query


def test_the_credential_is_not_in_the_body_of_any_alert() -> None:
    body = alert()

    assert TOKEN not in render(body)
    assert TOKEN not in str(body)


def test_a_webhook_does_not_print_its_credential() -> None:
    """A repr reaches logs, debuggers and exception context without anyone choosing it."""
    hook = webhook_from_env(env())

    assert TOKEN not in repr(hook)
    assert "redacted" in repr(hook)
    assert TOKEN not in f"{hook}"


def test_a_failing_endpoint_does_not_leak_the_credential_or_its_body() -> None:
    """A failing endpoint's response body is vendor- or attacker-controlled text."""

    class Failing:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                URL, 500, "Server Error", {}, io.BytesIO(b"secret internal detail")
            )

    with pytest.raises(AlertError) as raised:
        send(alert(), webhook_from_env(env()), opener=Failing())

    message = str(raised.value)
    assert "500" in message
    assert TOKEN not in message
    assert "secret internal detail" not in message


def test_a_transport_failure_reports_a_type_not_a_message() -> None:
    """An OS error message can carry a resolved host or a proxy URL."""

    class Broken:
        def open(self, request, timeout=None):
            raise urllib.error.URLError(OSError(13, f"denied for {TOKEN}"))

    with pytest.raises(AlertError) as raised:
        send(alert(), webhook_from_env(env()), opener=Broken())

    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://alerts.example.invalid/hook", "absolute HTTPS"),
        ("https://user:pass@alerts.example.invalid/hook", "credentials"),
        ("https://alerts.example.invalid/hook?token=abc", "query or fragment"),
        ("https://alerts.example.invalid/hook#tok", "query or fragment"),
        ("https:///hook", "absolute HTTPS"),
    ],
)
def test_an_ambiguous_endpoint_is_refused(url: str, reason: str) -> None:
    """A query string is where a webhook secret is usually smuggled, and proxies log it."""
    with pytest.raises(AlertError, match=reason):
        webhook_from_env(env(**{WEBHOOK_URL_ENV: url}))


@pytest.mark.parametrize("missing", [WEBHOOK_URL_ENV, WEBHOOK_TOKEN_ENV])
def test_an_unconfigured_webhook_is_refused(missing: str) -> None:
    incomplete = env()
    del incomplete[missing]

    with pytest.raises(AlertError, match=missing):
        webhook_from_env(incomplete)


def test_redirects_are_not_followed() -> None:
    """Following one would re-send the Authorization header to a host the response chose."""
    from touchstone.alerts import _NoRedirect

    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, URL) is None


def test_an_alert_carries_codes_rather_than_prose() -> None:
    body = alert(detail_code="EXPIRED", incident_hash="ab" * 32)

    assert body["event"] == "HEARTBEAT_STALE"
    assert body["severity"] == "CRITICAL"
    assert body["detail_code"] == "EXPIRED"
    assert strict_json_loads(render(body)) == body


@pytest.mark.parametrize(
    "detail", ["the feed returned 403", "lower", "with space", "with-dash", ""]
)
def test_free_text_cannot_reach_the_detail_field(detail: str) -> None:
    """A free-text detail is how exception text and source URLs arrive at a third party."""
    with pytest.raises(AlertError, match="detail_code"):
        alert(detail_code=detail)


@pytest.mark.parametrize("digest", ["ab" * 31, "AB" * 32, "zz" * 32, "not-a-digest"])
def test_an_incident_reference_must_be_a_digest(digest: str) -> None:
    with pytest.raises(AlertError, match="incident_hash"):
        alert(incident_hash=digest)


def test_an_alert_must_name_its_asset_and_an_instant() -> None:
    with pytest.raises(AlertError, match="asset"):
        alert(asset_key="")
    with pytest.raises(AlertError, match="observed_at"):
        alert(observed_at="2026-08-15T09:00:00+00:00")


def test_event_and_severity_must_be_the_declared_enumerations() -> None:
    """Strings would let a typo become a new event code nobody can grep for."""
    with pytest.raises(AlertError, match="enumerations"):
        alert(event="HEARTBEAT_STALE")
    with pytest.raises(AlertError, match="enumerations"):
        alert(severity="CRITICAL")


def test_the_same_condition_has_one_fingerprint_regardless_of_when_it_was_seen() -> (
    None
):
    """An alert that fires on every check is an alert that gets muted."""
    first = alert(observed_at="2026-08-15T09:00:00Z")
    later = alert(observed_at="2026-08-15T09:05:00Z")

    assert fingerprint(first) == fingerprint(later)
    assert fingerprint(first) != fingerprint(alert(event=Event.EPOCH_MISSED))
    assert TOKEN not in fingerprint(first)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "10"])
def test_a_send_without_a_real_timeout_is_refused(timeout: object) -> None:
    """An unbounded alert send is a hang at the moment something is already wrong."""
    with pytest.raises(ValueError, match="timeout"):
        send(alert(), webhook_from_env(env()), timeout=timeout, opener=Recorder())


def test_every_operations_trigger_has_a_stable_code() -> None:
    """The document commits to these; codes rather than prose so a year can be counted."""
    assert {event.value for event in Event} == {
        "HEARTBEAT_STALE",
        "RESTART_FAILED",
        "EPOCH_MISSED",
        "PUBLICATION_UNRESOLVED",
        "VERIFICATION_FAILED",
        "PUBLISHER_STATE_UNEXPECTED",
        "GAS_RUNWAY_SHORT",
        "BACKUP_MISSING",
        "RESTORE_REHEARSAL_FAILED",
        "RECOVERED",
    }


def test_a_recovery_alert_exists_so_a_cleared_condition_is_reported() -> None:
    body = alert(event=Event.RECOVERED, severity=Severity.INFO)

    assert body["event"] == "RECOVERED"


def test_a_webhook_constructed_directly_still_hides_its_token() -> None:
    assert TOKEN not in repr(Webhook(url=URL, token=TOKEN))


def test_a_hand_made_webhook_with_the_credential_in_its_url_is_refused() -> None:
    """`Webhook` is public and `send` is where bytes actually leave the process.

    Validating only at construction meant a webhook built directly went straight out with
    the credential in its path, past the validator that exists to refuse exactly that.
    """
    recorder = Recorder()

    with pytest.raises(AlertError, match="URL contains the credential"):
        send(
            alert(),
            Webhook(url=f"https://alerts.invalid/{TOKEN}", token=TOKEN),
            opener=recorder,
        )

    assert recorder.request is None, "the request was built and sent anyway"


def test_a_body_that_is_not_the_declared_shape_is_refused() -> None:
    """`send` accepted any mapping, so containment held only for bodies build() made."""
    recorder = Recorder()

    with pytest.raises(AlertError, match="exactly the fields"):
        send({"anything": TOKEN}, webhook_from_env(env()), opener=recorder)

    assert recorder.request is None


def test_a_malformed_url_is_this_modules_refusal() -> None:
    """An unterminated IPv6 literal makes urlsplit raise a bare ValueError.

    A caller catching AlertError saw a crash instead. Only inputs that genuinely fail to
    parse are listed here — a hostname containing a space parses perfectly well and is
    refused later by the connection, which is a different failure with a different owner.
    """
    with pytest.raises(AlertError, match="cannot be parsed"):
        webhook_from_env(env(**{WEBHOOK_URL_ENV: "https://["}))


def test_a_hand_made_webhook_over_plaintext_http_is_refused() -> None:
    """`Webhook` is public, so validating only at construction validated nothing.

    A caller could build one directly and send the bearer credential over plaintext HTTP
    to whatever host it named. The endpoint rules now run again where the bytes leave.
    """
    recorder = Recorder()

    with pytest.raises(AlertError, match="absolute HTTPS"):
        send(alert(), Webhook(url="http://alerts.invalid/hook", token=TOKEN),
             opener=recorder)

    assert recorder.request is None, "a plaintext request was built and sent"


def test_a_hand_made_webhook_with_a_malformed_url_is_this_modules_refusal() -> None:
    with pytest.raises(AlertError, match="cannot be parsed"):
        send(alert(), Webhook(url="https://[", token=TOKEN), opener=Recorder())
