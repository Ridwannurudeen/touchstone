"""Allowlisted source retrieval with bounded, content-addressed evidence storage."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from types import MappingProxyType
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from touchstone.evidence import EvidenceStore


DEFAULT_TIMEOUT_SECONDS = 10.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MISSING_CONTENT_TYPE = "<missing>"


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """One exact, approved source endpoint and its retrieval limits."""

    source_id: str
    url: str
    expected_mime: str
    authority_class: str
    cadence: str
    max_bytes: int


USTB_SOURCES = (
    SourceManifest(
        source_id="superstate-ustb-nav-daily",
        url="https://api.superstate.com/v1/funds/1/nav-daily",
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="business-daily",
        max_bytes=262_144,
    ),
    SourceManifest(
        source_id="superstate-ustb-yield",
        url="https://api.superstate.com/v1/funds/1/yield",
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="business-daily",
        max_bytes=4_096,
    ),
    SourceManifest(
        source_id="superstate-ustb-holdings",
        url="https://api.superstate.com/v2/funds/1/holdings",
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="periodic",
        max_bytes=16_384,
    ),
)
USTB_SOURCE_BY_ID: Mapping[str, SourceManifest] = MappingProxyType(
    {source.source_id: source for source in USTB_SOURCES}
)


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Uninterpreted response returned by a transport."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    """Injectable HTTP GET boundary used by the source fetcher."""

    def get(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> TransportResponse:
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

    def get(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> TransportResponse:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "touchstone/0.1.0",
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
            raise SourceTransportError(f"transport failed for {url}: {error}") from error


def fetch_source(
    source_id: str,
    *,
    store: EvidenceStore,
    transport: Transport,
    url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retrieved_at: datetime | None = None,
) -> FetchResult:
    """Fetch one allowlisted USTB source and store its exact response bytes."""
    manifest = USTB_SOURCE_BY_ID.get(source_id)
    if manifest is None:
        raise SourcePolicyError(f"unknown source_id: {source_id}")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive number")
    if retrieved_at is not None and (
        not isinstance(retrieved_at, datetime)
        or retrieved_at.tzinfo is None
        or retrieved_at.utcoffset() is None
    ):
        raise ValueError("retrieved_at must be a timezone-aware datetime")

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
            _validate_allowlisted(redirected_url)
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
    return TransportResponse(
        status_code=http_response.getcode(),
        headers=dict(http_response.headers.items()),
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


def _validate_allowlisted(url: str) -> None:
    """A redirect may only land on a URL the allowlist already names.

    Same-host is not enough: an open redirect on an approved host would otherwise move
    retrieval to bytes the manifest never approved.
    """
    if url not in {manifest.url for manifest in USTB_SOURCES}:
        raise SourcePolicyError("redirect target is not in the exact source allowlist")


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
    if _media_type(encoding) not in {"identity"}:
        raise SourceResponseError(
            f"source used Content-Encoding {encoding.strip()!r}; only identity is accepted"
        )


def _validate_redirect(current_url: str, redirected_url: str) -> None:
    _validate_https_url(redirected_url, context="redirect URL")
    if urlsplit(current_url).hostname != urlsplit(redirected_url).hostname:
        raise SourcePolicyError("cross-host redirect is not allowed")
