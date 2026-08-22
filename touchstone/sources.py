"""Allowlisted source retrieval with bounded, content-addressed evidence storage."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import re

from touchstone.evidence import EvidenceStore
from touchstone.quantities import finite_positive, utc_instant


DEFAULT_TIMEOUT_SECONDS = 10.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MISSING_CONTENT_TYPE = "<missing>"
_SEC_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})
_CONTACT_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """One exact, approved source endpoint and its retrieval limits."""

    source_id: str
    url: str
    expected_mime: str
    authority_class: str
    cadence: str
    max_bytes: int
    # The freshness this project declares for the source, and the unit it is counted in.
    # Project-declared, not issuer-published: the holdings manifest says its 40 days is
    # provisional and not derived from an observed cadence, and calling that the issuer's
    # own policy would claim a basis the evidence does not have.
    #
    # `manifests/sources/*.json` declared these and nothing read them, so a candidate could
    # name any grace period it liked -- a NAV freshness control with a 999-business-day
    # window was accepted while the manifest declared zero. The manifest is the authority;
    # `test_sources` asserts these stay equal to it.
    grace_period: int
    grace_unit: str
    redirect_aliases: tuple[str, ...] = ()


USTB_SOURCES = (
    SourceManifest(
        source_id="superstate-ustb-nav-daily",
        url="https://api.superstate.com/v1/funds/1/nav-daily",
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="business-daily",
        max_bytes=262_144,
        grace_period=0,
        grace_unit="business_days",
    ),
    SourceManifest(
        source_id="superstate-ustb-yield",
        url="https://api.superstate.com/v1/funds/1/yield",
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="business-daily",
        max_bytes=4_096,
        grace_period=2,
        grace_unit="business_days",
    ),
    SourceManifest(
        source_id="superstate-ustb-holdings",
        url="https://api.superstate.com/v2/funds/1/holdings",
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="periodic",
        max_bytes=16_384,
        # Provisional, per the manifest: the true cadence has not been observed long enough
        # to derive one.
        grace_period=40,
        grace_unit="calendar_days",
    ),
)
USTB_SOURCE_BY_ID: Mapping[str, SourceManifest] = MappingProxyType(
    {source.source_id: source for source in USTB_SOURCES}
)

FOBXX_SOURCES = (
    SourceManifest(
        source_id="sec-edgar-fobxx-submissions",
        url="https://data.sec.gov/submissions/CIK0001786958.json",
        expected_mime="application/json",
        authority_class="regulator-filing",
        cadence="updated-as-filed",
        max_bytes=8_388_608,
        grace_period=10,
        grace_unit="business_days",
    ),
    SourceManifest(
        source_id="sec-edgar-fobxx-nmfp3",
        url="https://www.sec.gov/Archives/edgar/data/1786958/000207169126017542/primary_doc.xml",
        expected_mime="text/xml",
        authority_class="regulator-filing",
        cadence="monthly",
        max_bytes=4_194_304,
        grace_period=10,
        grace_unit="business_days",
    ),
)
FOBXX_SOURCE_BY_ID: Mapping[str, SourceManifest] = MappingProxyType(
    {source.source_id: source for source in FOBXX_SOURCES}
)


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Uninterpreted response returned by a transport."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    """Injectable HTTP GET boundary used by the source fetcher."""

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        """Retrieve one URL without following redirects."""
        ...


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Stored artifact identity and response metadata."""

    source_id: str
    source_url: str
    retrieved_at: datetime
    content_type: str
    byte_size: int
    evidence_sha256: str
    redirect_count: int


class SourceUnavailable(RuntimeError):
    """Evidence could not be retrieved. Says nothing about the asset.

    Lives here rather than in the service script because the epoch producer is a package
    module and must be able to raise it. A package importing from `scripts/` to reach an
    exception type is a layering inversion, and the alternative — the producer raising a
    retrieval error the service reads as an epoch failure — would record a source outage as
    a finding about the issuer.
    """


class SourceFetchError(RuntimeError):
    """Base class for typed source retrieval failures."""


class SourcePolicyError(SourceFetchError):
    """Raised when a request violates the source allowlist or redirect policy."""


class SourceTransportError(SourceFetchError):
    """Raised when the HTTP transport cannot complete a request."""


class SourceResponseError(SourceFetchError):
    """Raised when an HTTP response cannot be accepted."""


class SourceTooLargeError(SourceResponseError):
    """Raised when a response exceeds its source-specific byte cap."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class LiveTransport:
    """Stdlib HTTPS transport that exposes redirects to the policy layer."""

    def __init__(self, *, user_agent: str | None = None) -> None:
        self.user_agent = user_agent

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        hostname = urlsplit(url).hostname
        if hostname in _SEC_HOSTS:
            if (
                not isinstance(self.user_agent, str)
                or not self.user_agent.strip()
                or _CONTACT_EMAIL.search(self.user_agent) is None
            ):
                raise SourcePolicyError(
                    "SEC retrieval requires an identifying User-Agent with a contact "
                    "email address"
                )
            user_agent = self.user_agent.strip()
        else:
            user_agent = (
                self.user_agent.strip() if self.user_agent else "touchstone/0.1.0"
            )
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": user_agent,
            },
        )
        opener = build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout) as http_response:
                return _read_http_response(http_response, max_bytes=max_bytes)
        except HTTPError as error:
            try:
                return _read_http_response(error, max_bytes=max_bytes)
            finally:
                error.close()
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            raise SourceTransportError(
                f"transport failed for {url}: {error}"
            ) from error


def fetch_source(
    source_id: str,
    *,
    store: EvidenceStore,
    transport: Transport,
    url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retrieved_at: datetime | None = None,
    manifest: SourceManifest | None = None,
) -> FetchResult:
    """Fetch one allowlisted source and store its exact response bytes.

    ``manifest`` lets an asset other than USTB use this path. It exists because the
    alternative was tried and was worse: when the engine became multi-asset, anything the
    USTB map did not name was fetched by a second, simpler routine that checked only the
    status code and the byte cap. That route skipped HTTPS enforcement, the exact-URL
    allowlist, the single-redirect and same-host policy, the declared-MIME check and the
    content-encoding refusal — so a second asset would have been retrieved under materially
    weaker rules than the first, which is the opposite of what adding an asset should mean.

    A supplied manifest is still an allowlist entry; it is the descriptor's own committed
    manifest rather than a caller-invented one, and every check below applies to it
    unchanged.
    """
    if manifest is None:
        manifest = USTB_SOURCE_BY_ID.get(source_id) or FOBXX_SOURCE_BY_ID.get(source_id)
    if manifest is None:
        raise SourcePolicyError(f"unknown source_id: {source_id}")
    if manifest.source_id != source_id:
        raise SourcePolicyError(f"manifest names {manifest.source_id}, not {source_id}")
    timeout = finite_positive(timeout, "timeout")
    # Rebound, not merely checked. The validated instant was discarded and the caller's
    # original object handed on to storage and the epoch, so a zone that answered the
    # check and then changed its mind was resolved a second time downstream.
    if retrieved_at is not None:
        retrieved_at = utc_instant(retrieved_at, "retrieved_at")

    requested_url = manifest.url if url is None else url
    if requested_url != manifest.url:
        raise SourcePolicyError("initial URL is not in the exact source allowlist")
    _validate_https_url(requested_url, context="initial URL")

    current_url = requested_url
    redirect_count = 0
    while True:
        response = transport.get(
            current_url,
            timeout=float(timeout),
            max_bytes=manifest.max_bytes,
        )
        _validate_transport_response(response)
        if len(response.body) > manifest.max_bytes:
            raise SourceTooLargeError(
                f"response exceeds {manifest.max_bytes} byte limit for {source_id}"
            )
        if response.status_code in _REDIRECT_STATUSES:
            if redirect_count == 1:
                raise SourcePolicyError("source redirected more than once")
            location = _header(response.headers, "Location")
            if location is None or not location.strip():
                raise SourceResponseError("redirect response has no Location header")
            redirected_url = urljoin(current_url, location)
            _validate_redirect(current_url, redirected_url)
            _validate_same_source(redirected_url, manifest)
            current_url = redirected_url
            redirect_count += 1
            continue
        if not 200 <= response.status_code < 300:
            raise SourceResponseError(
                f"source returned HTTP status {response.status_code}"
            )
        break

    _validate_content_encoding(response.headers)
    content_type = _header(response.headers, "Content-Type")
    if content_type is None or not content_type.strip():
        content_type = _MISSING_CONTENT_TYPE
    _validate_media_type(content_type, manifest)
    observed_at = retrieved_at or datetime.now(timezone.utc)
    digest = store.store(
        response.body,
        source_id=manifest.source_id,
        source_url=current_url,
        retrieved_at=observed_at,
        declared_mime=content_type,
    )
    return FetchResult(
        source_id=manifest.source_id,
        source_url=current_url,
        retrieved_at=observed_at,
        content_type=content_type,
        byte_size=len(response.body),
        evidence_sha256=digest,
        redirect_count=redirect_count,
    )


def _read_http_response(http_response, *, max_bytes: int) -> TransportResponse:
    body = http_response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise SourceTooLargeError(f"response exceeds {max_bytes} byte limit")
    items = list(http_response.headers.items())
    for name in ("Content-Type", "Content-Encoding"):
        values = {
            value.strip().lower() for key, value in items if key.lower() == name.lower()
        }
        if len(values) > 1:
            raise SourceResponseError(
                f"source returned conflicting {name} headers: {sorted(values)}"
            )
    return TransportResponse(
        status_code=http_response.getcode(),
        headers=dict(items),
        body=body,
    )


def _validate_transport_response(response: object) -> None:
    if not isinstance(response, TransportResponse):
        raise TypeError("transport must return TransportResponse")
    if (
        isinstance(response.status_code, bool)
        or not isinstance(response.status_code, int)
        or not 100 <= response.status_code <= 599
    ):
        raise SourceResponseError("transport returned an invalid HTTP status")
    if not isinstance(response.headers, Mapping):
        raise SourceResponseError("transport returned invalid headers")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in response.headers.items()
    ):
        raise SourceResponseError("transport returned invalid headers")
    if not isinstance(response.body, bytes):
        raise SourceResponseError("transport returned a non-bytes body")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    matching = [value for key, value in headers.items() if key.lower() == name.lower()]
    if not matching:
        return None
    if len(matching) > 1 and len({value.strip().lower() for value in matching}) > 1:
        raise SourceResponseError(
            f"transport returned conflicting {name} headers; last-wins could hide either"
        )
    value = matching[-1]
    if not isinstance(value, str):
        raise SourceResponseError(f"transport returned invalid {name} header")
    return value


def _validate_https_url(url: object, *, context: str) -> None:
    if not isinstance(url, str):
        raise SourcePolicyError(f"{context} must be a string")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SourcePolicyError(f"{context} is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise SourcePolicyError(f"{context} must be an HTTPS URL")


def _validate_same_source(url: str, manifest: SourceManifest) -> None:
    """A redirect may only land on a URL declared by the source being fetched.

    Allowlisting the whole portfolio is not enough. The byte cap, expected MIME and
    stored ``source_id`` all belong to the source that was requested, so a redirect from
    one approved source to a different one would file the second source's bytes under the
    first source's identity and limits.
    """
    permitted = {manifest.url, *manifest.redirect_aliases}
    if url not in permitted:
        raise SourcePolicyError(
            f"redirect target is not declared by {manifest.source_id}"
        )


def _media_type(content_type: str) -> str:
    """Return the bare media type, dropping parameters such as charset."""
    return content_type.split(";", 1)[0].strip().lower()


def _validate_media_type(content_type: str, manifest: SourceManifest) -> None:
    """Enforce the manifest's declared MIME instead of merely recording what arrived."""
    if content_type == _MISSING_CONTENT_TYPE:
        raise SourceResponseError(
            f"source sent no Content-Type; {manifest.source_id} declares "
            f"{manifest.expected_mime}"
        )
    received = _media_type(content_type)
    if received != _media_type(manifest.expected_mime):
        raise SourceResponseError(
            f"source sent {received}; {manifest.source_id} declares "
            f"{manifest.expected_mime}"
        )


def _validate_content_encoding(headers: Mapping[str, str]) -> None:
    """Refuse a compressed body.

    ``identity`` is requested, and the byte cap is applied to what arrives on the wire, so
    a compressed response could smuggle far more expanded data than the cap allows.
    """
    encoding = _header(headers, "Content-Encoding")
    if encoding is None or not encoding.strip():
        return
    if encoding.strip().lower() != "identity":
        raise SourceResponseError(
            f"source used Content-Encoding {encoding.strip()!r}; only identity is accepted"
        )


def _validate_redirect(current_url: str, redirected_url: str) -> None:
    _validate_https_url(redirected_url, context="redirect URL")
    if urlsplit(current_url).hostname != urlsplit(redirected_url).hostname:
        raise SourcePolicyError("cross-host redirect is not allowed")
