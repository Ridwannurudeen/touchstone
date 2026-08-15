import hashlib
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

import touchstone.sources as sources_module
from touchstone.evidence import EvidenceStore
from touchstone.sources import (
    USTB_SOURCES,
    SourcePolicyError,
    SourceResponseError,
    SourceTooLargeError,
    TransportResponse,
    fetch_source,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"
RETRIEVED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
EXPECTED_SOURCES = [
    (
        "superstate-ustb-nav-daily",
        "https://api.superstate.com/v1/funds/1/nav-daily",
        "business-daily",
        262_144,
        "ustb-nav.json",
    ),
    (
        "superstate-ustb-yield",
        "https://api.superstate.com/v1/funds/1/yield",
        "business-daily",
        4_096,
        "ustb-yield.json",
    ),
    (
        "superstate-ustb-holdings",
        "https://api.superstate.com/v2/funds/1/holdings",
        "periodic",
        16_384,
        "ustb-holdings.json",
    ),
]


class FakeTransport:
    def __init__(self, responses: dict[str, TransportResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float, int]] = []

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        self.calls.append((url, timeout, max_bytes))
        return self.responses[url]


def response(
    body: bytes,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> TransportResponse:
    return TransportResponse(
        status_code=status_code,
        headers={"Content-Type": "application/json"} if headers is None else headers,
        body=body,
    )


def read_index(root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_ustb_source_registry_is_exact_and_frozen() -> None:
    assert [
        (
            source.source_id,
            source.url,
            source.cadence,
            source.max_bytes,
        )
        for source in USTB_SOURCES
    ] == [item[:4] for item in EXPECTED_SOURCES]
    assert all(source.expected_mime == "application/json" for source in USTB_SOURCES)
    assert all(source.authority_class == "issuer-api" for source in USTB_SOURCES)
    assert isinstance(USTB_SOURCES, tuple)


@pytest.mark.parametrize(
    ("source_id", "url", "cadence", "max_bytes", "fixture_name"),
    EXPECTED_SOURCES,
)
def test_fetch_stores_exact_fixture_bytes_and_records_response_content_type(
    tmp_path: Path,
    source_id: str,
    url: str,
    cadence: str,
    max_bytes: int,
    fixture_name: str,
) -> None:
    del cadence
    raw = (FIXTURES / fixture_name).read_bytes()
    transport = FakeTransport(
        {url: response(raw, headers={"content-type": "application/json; charset=utf-8"})}
    )

    result = fetch_source(
        source_id,
        store=EvidenceStore(tmp_path),
        transport=transport,
        retrieved_at=RETRIEVED_AT,
    )

    digest = hashlib.sha256(raw).hexdigest()
    assert result.source_id == source_id
    assert result.source_url == url
    assert result.evidence_sha256 == digest
    assert result.byte_size == len(raw)
    assert result.content_type == "application/json; charset=utf-8"
    assert result.redirect_count == 0
    assert transport.calls == [(url, 10.0, max_bytes)]
    assert (tmp_path / "objects" / digest).read_bytes() == raw
    entry = read_index(tmp_path)[0]
    assert entry["declared_mime"] == "application/json; charset=utf-8"
    assert entry["source_url"] == url


def test_missing_content_type_is_refused(tmp_path: Path) -> None:
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {
            source.url: response(
                (FIXTURES / "ustb-yield.json").read_bytes(), headers={}
            )
        }
    )

    with pytest.raises(SourceResponseError, match="no Content-Type"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )


def with_aliases(monkeypatch, source, *aliases: str):
    """Declare redirect aliases on one source for the duration of a test.

    The real mapping is an immutable proxy, so the whole mapping is swapped rather than
    mutated — which also keeps the production object genuinely read-only.
    """
    aliased = replace(source, redirect_aliases=tuple(aliases))
    patched = dict(sources_module.USTB_SOURCE_BY_ID)
    patched[source.source_id] = aliased
    monkeypatch.setattr(
        sources_module, "USTB_SOURCE_BY_ID", MappingProxyType(patched)
    )
    return aliased


def test_redirect_to_a_declared_alias_is_followed_once(
    tmp_path: Path, monkeypatch
) -> None:
    """A redirect is followed only to a URL the SAME source declares as its own alias."""
    source = USTB_SOURCES[1]
    redirected_url = "https://api.superstate.com/v1/funds/1/yield?format=canonical"
    with_aliases(monkeypatch, source, redirected_url)
    raw = (FIXTURES / "ustb-yield.json").read_bytes()
    transport = FakeTransport(
        {
            source.url: response(
                b"",
                status_code=302,
                headers={"Location": redirected_url},
            ),
            redirected_url: response(raw),
        }
    )

    result = fetch_source(
        source.source_id,
        store=EvidenceStore(tmp_path),
        transport=transport,
        retrieved_at=RETRIEVED_AT,
    )

    assert result.source_url == redirected_url
    assert result.redirect_count == 1
    assert [call[0] for call in transport.calls] == [source.url, redirected_url]
    assert read_index(tmp_path)[0]["source_url"] == redirected_url


def test_a_redirect_to_another_approved_source_is_refused(tmp_path: Path) -> None:
    """Allowlisting the whole portfolio would file one source's bytes under another's
    identity, cap and expected MIME. A redirect must stay within its own source."""
    nav = USTB_SOURCES[0]
    transport = FakeTransport(
        {
            nav.url: response(
                b"", status_code=302, headers={"Location": USTB_SOURCES[1].url}
            ),
            USTB_SOURCES[1].url: response(
                (FIXTURES / "ustb-yield.json").read_bytes()
            ),
        }
    )

    with pytest.raises(SourcePolicyError, match="not declared by"):
        fetch_source(
            nav.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )
    assert [call[0] for call in transport.calls] == [nav.url]


def test_same_host_redirect_off_the_allowlist_is_refused(tmp_path: Path) -> None:
    """An open redirect on an approved host must not move retrieval off-manifest."""
    source = USTB_SOURCES[1]
    elsewhere = "https://api.superstate.com/v1/funds/1/yield?version=attacker"
    transport = FakeTransport(
        {
            source.url: response(
                b"", status_code=302, headers={"Location": elsewhere}
            ),
            elsewhere: response(b'{"as_of_date":"1970-01-01"}'),
        }
    )

    with pytest.raises(SourcePolicyError, match="not declared by"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )
    assert [call[0] for call in transport.calls] == [source.url]


@pytest.mark.parametrize(
    "url",
    [
        "http://api.superstate.com/v1/funds/1/yield",
        "https://evil.example/v1/funds/1/yield",
        "https://api.superstate.com/v1/funds/1/yield?extra=true",
    ],
)
def test_non_allowlisted_initial_url_is_refused_before_transport(
    tmp_path: Path, url: str
) -> None:
    transport = FakeTransport({})

    with pytest.raises(SourcePolicyError, match="allowlist"):
        fetch_source(
            "superstate-ustb-yield",
            url=url,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )

    assert transport.calls == []
    assert list(tmp_path.iterdir()) == []


def test_unknown_source_id_is_refused_before_transport(tmp_path: Path) -> None:
    transport = FakeTransport({})

    with pytest.raises(SourcePolicyError, match="unknown source_id"):
        fetch_source(
            "unknown",
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/yield",
        "http://api.superstate.com/v1/funds/1/yield-next",
        "//evil.example/yield",
    ],
)
def test_cross_host_or_non_https_redirect_is_refused(
    tmp_path: Path, location: str
) -> None:
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {
            source.url: response(
                b"", status_code=302, headers={"Location": location}
            )
        }
    )

    with pytest.raises(SourcePolicyError, match="redirect"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )

    assert len(transport.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_second_redirect_is_refused(tmp_path: Path, monkeypatch) -> None:
    source = USTB_SOURCES[1]
    first_redirect = "https://api.superstate.com/v1/funds/1/yield?hop=1"
    second_redirect = "https://api.superstate.com/v1/funds/1/yield?hop=2"
    with_aliases(monkeypatch, source, first_redirect, second_redirect)
    transport = FakeTransport(
        {
            source.url: response(
                b"", status_code=301, headers={"Location": first_redirect}
            ),
            first_redirect: response(
                b"", status_code=307, headers={"Location": second_redirect}
            ),
        }
    )

    with pytest.raises(SourcePolicyError, match="more than once"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )

    assert len(transport.calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_redirect_without_location_is_refused(tmp_path: Path) -> None:
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {source.url: response(b"", status_code=302, headers={"Server": "test"})}
    )

    with pytest.raises(SourceResponseError, match="Location"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )


def test_oversize_response_is_refused_without_storage(tmp_path: Path) -> None:
    source = USTB_SOURCES[1]
    transport = FakeTransport({source.url: response(b"x" * (source.max_bytes + 1))})

    with pytest.raises(SourceTooLargeError, match="4096"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status_code", [199, 400, 500])
def test_non_success_response_is_refused(
    tmp_path: Path, status_code: int
) -> None:
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {source.url: response(b"error", status_code=status_code)}
    )

    with pytest.raises(SourceResponseError, match=str(status_code)):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )

    assert list(tmp_path.iterdir()) == []


def test_timeout_must_be_positive_and_is_forwarded(tmp_path: Path) -> None:
    source = USTB_SOURCES[1]
    raw = (FIXTURES / "ustb-yield.json").read_bytes()
    transport = FakeTransport({source.url: response(raw)})

    fetch_source(
        source.source_id,
        store=EvidenceStore(tmp_path),
        transport=transport,
        timeout=2.5,
        retrieved_at=RETRIEVED_AT,
    )
    assert transport.calls == [(source.url, 2.5, source.max_bytes)]

    with pytest.raises(ValueError, match="timeout"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path / "invalid"),
            transport=transport,
            timeout=0,
            retrieved_at=RETRIEVED_AT,
        )

    with pytest.raises(ValueError, match="timeout"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path / "nan"),
            transport=transport,
            timeout=math.nan,
            retrieved_at=RETRIEVED_AT,
        )


def test_invalid_retrieval_time_is_refused_before_transport(tmp_path: Path) -> None:
    transport = FakeTransport({})

    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_source(
            "superstate-ustb-yield",
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=datetime(2026, 8, 13),
        )

    assert transport.calls == []


def test_a_wrong_media_type_is_refused(tmp_path: Path) -> None:
    """The manifest's expected_mime is enforced, not merely recorded alongside it."""
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {
            source.url: response(
                (FIXTURES / "ustb-yield.json").read_bytes(),
                headers={"Content-Type": "text/html"},
            )
        }
    )

    with pytest.raises(SourceResponseError, match="text/html"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )
    assert not (tmp_path / "index.jsonl").exists(), "rejected bytes must not be stored"


def test_a_charset_parameter_does_not_defeat_the_media_type_check(
    tmp_path: Path,
) -> None:
    """Enforcement compares the media type, so a charset parameter is still accepted."""
    source = USTB_SOURCES[1]
    raw = (FIXTURES / "ustb-yield.json").read_bytes()
    transport = FakeTransport(
        {
            source.url: response(
                raw, headers={"Content-Type": "APPLICATION/JSON; charset=UTF-8"}
            )
        }
    )

    result = fetch_source(
        source.source_id,
        store=EvidenceStore(tmp_path),
        transport=transport,
        retrieved_at=RETRIEVED_AT,
    )

    assert result.byte_size == len(raw)


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "gzip, identity"])
def test_a_compressed_body_is_refused(tmp_path: Path, encoding: str) -> None:
    """A compressed body could expand far past the byte cap applied on the wire."""
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {
            source.url: response(
                (FIXTURES / "ustb-yield.json").read_bytes(),
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": encoding,
                },
            )
        }
    )

    with pytest.raises(SourceResponseError, match="only identity is accepted"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )


def test_an_explicit_identity_encoding_is_accepted(tmp_path: Path) -> None:
    source = USTB_SOURCES[1]
    raw = (FIXTURES / "ustb-yield.json").read_bytes()
    transport = FakeTransport(
        {
            source.url: response(
                raw,
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": "identity",
                },
            )
        }
    )

    assert (
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        ).byte_size
        == len(raw)
    )


def test_identity_with_a_parameter_is_refused(tmp_path: Path) -> None:
    """The exact bypass that existed: parameter-stripping made 'identity; gzip' pass."""
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {
            source.url: response(
                (FIXTURES / "ustb-yield.json").read_bytes(),
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": "identity; gzip",
                },
            )
        }
    )

    with pytest.raises(SourceResponseError, match="only identity is accepted"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )


class DuplicateHeaders(Mapping):
    """Headers carrying the same field twice, as a real response can."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def items(self):
        return iter(self._pairs)

    def __iter__(self):
        return iter(name for name, _ in self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    def __getitem__(self, key: str) -> str:
        for name, value in self._pairs:
            if name == key:
                return value
        raise KeyError(key)


@pytest.mark.parametrize(
    "pairs",
    [
        [("Content-Type", "application/json"), ("content-type", "text/html")],
        [
            ("Content-Type", "application/json"),
            ("Content-Encoding", "identity"),
            ("content-encoding", "gzip"),
        ],
    ],
)
def test_conflicting_duplicate_headers_are_refused(
    tmp_path: Path, pairs: list[tuple[str, str]]
) -> None:
    """Last-wins on a duplicated header would let either value hide behind the other."""
    source = USTB_SOURCES[1]
    transport = FakeTransport(
        {
            source.url: TransportResponse(
                status_code=200,
                headers=DuplicateHeaders(pairs),
                body=(FIXTURES / "ustb-yield.json").read_bytes(),
            )
        }
    )

    with pytest.raises(SourceResponseError, match="conflicting"):
        fetch_source(
            source.source_id,
            store=EvidenceStore(tmp_path),
            transport=transport,
            retrieved_at=RETRIEVED_AT,
        )
