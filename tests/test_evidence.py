import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from touchstone.evidence import EvidenceIntegrityError, EvidenceStore


RETRIEVED_AT = datetime(2026, 8, 13, 14, 16, 17, 123456, tzinfo=timezone.utc)


def store_observation(
    store: EvidenceStore,
    content: bytes = b'{"nav":"11.17558800"}',
    *,
    source_id: str = "superstate-ustb-nav",
    source_url: str = "https://api.superstate.com/v1/funds/1/nav-daily",
    retrieved_at: datetime = RETRIEVED_AT,
    declared_mime: str = "application/json",
) -> str:
    return store.store(
        content,
        source_id=source_id,
        source_url=source_url,
        retrieved_at=retrieved_at,
        declared_mime=declared_mime,
    )


def read_entries(root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def rewrite_entry(root: Path, line_number: int, mutate) -> None:
    entries = read_entries(root)
    mutate(entries[line_number - 1])
    (root / "index.jsonl").write_bytes(
        b"".join(canonical_bytes(entry) + b"\n" for entry in entries)
    )


def test_store_persists_exact_bytes_and_metadata(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    content = "café \u2615".encode()
    digest = hashlib.sha256(content).hexdigest()

    evidence_id = store_observation(
        store,
        content,
        source_id="source-α",
        source_url="https://example.com/資料?q=ナビ",
        retrieved_at=datetime(
            2026, 8, 13, 15, 16, 17, 123456, tzinfo=timezone(timedelta(hours=1))
        ),
        declared_mime="application/vnd.example+ζ",
    )

    assert evidence_id == digest
    assert (tmp_path / "objects" / digest).read_bytes() == content
    entries = read_entries(tmp_path)
    assert entries == [
        {
            "byte_size": len(content),
            "declared_mime": "application/vnd.example+ζ",
            "entry_hash": entries[0]["entry_hash"],
            "prev_entry_hash": None,
            "retrieved_at": "2026-08-13T14:16:17.123456Z",
            "sha256": digest,
            "source_id": "source-α",
            "source_url": "https://example.com/資料?q=ナビ",
        }
    ]
    entry_without_hash = {
        key: value for key, value in entries[0].items() if key != "entry_hash"
    }
    assert (
        entries[0]["entry_hash"]
        == hashlib.sha256(canonical_bytes(entry_without_hash)).hexdigest()
    )
    assert store.verify() == 1


def test_same_content_keeps_object_and_records_each_observation(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    content = bytearray(b"same content")
    evidence_id = store_observation(store, content)
    object_path = tmp_path / "objects" / evidence_id
    original_stat = object_path.stat()

    second_id = store_observation(
        store,
        memoryview(content),
        retrieved_at=RETRIEVED_AT + timedelta(minutes=1),
    )

    assert second_id == evidence_id
    assert object_path.stat().st_ino == original_stat.st_ino
    assert object_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    entries = read_entries(tmp_path)
    assert len(entries) == 2
    assert entries[1]["prev_entry_hash"] == entries[0]["entry_hash"]
    assert entries[0]["sha256"] == entries[1]["sha256"] == evidence_id
    assert store.verify() == 2


def test_reopened_store_continues_chain(tmp_path: Path) -> None:
    first = EvidenceStore(tmp_path)
    store_observation(first)
    first_hash = read_entries(tmp_path)[0]["entry_hash"]

    reopened = EvidenceStore(tmp_path)
    store_observation(
        reopened, b"second", retrieved_at=RETRIEVED_AT + timedelta(hours=1)
    )

    assert read_entries(tmp_path)[1]["prev_entry_hash"] == first_hash
    assert reopened.verify() == 2


@pytest.mark.parametrize(
    ("content", "field", "value"),
    [
        ("not bytes", None, None),
        (b"content", "source_id", ""),
        (b"content", "source_id", "   "),
        (b"content", "source_id", 7),
        (b"content", "source_id", chr(0xD800)),
        (b"content", "declared_mime", ""),
        (b"content", "declared_mime", "\t"),
        (b"content", "declared_mime", 7),
        (b"content", "source_url", "/relative"),
        (b"content", "source_url", "ftp://example.com/file"),
        (b"content", "source_url", "https:///missing-host"),
        (b"content", "source_url", " https://example.com/evidence"),
        (b"content", "source_url", 7),
        (b"content", "retrieved_at", datetime(2026, 8, 13)),
        (b"content", "retrieved_at", "2026-08-13T00:00:00Z"),
    ],
)
def test_invalid_inputs_do_not_create_store(
    tmp_path: Path, content: object, field: str | None, value: object
) -> None:
    kwargs = {
        "source_id": "source",
        "source_url": "https://example.com/evidence",
        "retrieved_at": RETRIEVED_AT,
        "declared_mime": "application/json",
    }
    if field is not None:
        kwargs[field] = value

    with pytest.raises((TypeError, ValueError)):
        EvidenceStore(tmp_path).store(content, **kwargs)

    assert list(tmp_path.iterdir()) == []


def test_existing_object_is_never_overwritten_and_tampering_is_detected(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)
    evidence_id = store_observation(store, b"original")
    object_path = tmp_path / "objects" / evidence_id
    object_path.write_bytes(b"tampered")

    with pytest.raises(EvidenceIntegrityError, match=evidence_id):
        store_observation(
            store, b"original", retrieved_at=RETRIEVED_AT + timedelta(days=1)
        )

    assert object_path.read_bytes() == b"tampered"
    assert len(read_entries(tmp_path)) == 1


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda entry: entry.__setitem__("source_id", "changed"), "line 1"),
        (lambda entry: entry.__setitem__("entry_hash", "0" * 64), "line 1"),
        (lambda entry: entry.__setitem__("extra", "field"), "line 1"),
        (lambda entry: entry.pop("declared_mime"), "line 1"),
        (lambda entry: entry.__setitem__("byte_size", -1), "line 1"),
        (lambda entry: entry.__setitem__("retrieved_at", "not-a-time"), "line 1"),
        (lambda entry: entry.__setitem__("source_url", "relative"), "line 1"),
    ],
)
def test_verify_rejects_invalid_or_tampered_index_fields(
    tmp_path: Path, mutation, match: str
) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    rewrite_entry(tmp_path, 1, mutation)

    with pytest.raises(EvidenceIntegrityError, match=match):
        store.verify()


def test_verify_rejects_malformed_json(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    (tmp_path / "index.jsonl").write_bytes(b'{"malformed":}\n')

    with pytest.raises(EvidenceIntegrityError, match="line 1"):
        store.verify()


def test_verify_rejects_noncanonical_index_line(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    entry = read_entries(tmp_path)[0]
    (tmp_path / "index.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError, match="canonical"):
        store.verify()


def test_verify_rejects_truncated_final_newline(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    index_path = tmp_path / "index.jsonl"
    index_path.write_bytes(index_path.read_bytes().removesuffix(b"\n"))

    with pytest.raises(EvidenceIntegrityError, match="truncated"):
        store.verify()


def test_verify_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    line = (tmp_path / "index.jsonl").read_bytes()
    (tmp_path / "index.jsonl").write_bytes(
        line.replace(b"{", b'{"sha256":"duplicate",', 1)
    )

    with pytest.raises(EvidenceIntegrityError, match="duplicate"):
        store.verify()


def test_verify_rejects_broken_chain_link(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    store_observation(
        store, b"second", retrieved_at=RETRIEVED_AT + timedelta(minutes=1)
    )
    rewrite_entry(
        tmp_path, 2, lambda entry: entry.__setitem__("prev_entry_hash", "0" * 64)
    )

    with pytest.raises(EvidenceIntegrityError, match="line 2.*previous"):
        store.verify()


def test_verify_rejects_missing_object(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    evidence_id = store_observation(store)
    (tmp_path / "objects" / evidence_id).unlink()

    with pytest.raises(EvidenceIntegrityError, match=f"line 1.*{evidence_id}.*missing"):
        store.verify()


@pytest.mark.parametrize("replacement", [b"short", b"x" * 25])
def test_verify_rejects_wrong_object_size(tmp_path: Path, replacement: bytes) -> None:
    store = EvidenceStore(tmp_path)
    evidence_id = store_observation(store)
    (tmp_path / "objects" / evidence_id).write_bytes(replacement)

    with pytest.raises(EvidenceIntegrityError, match=f"line 1.*{evidence_id}.*size"):
        store.verify()


def test_verify_rejects_wrong_object_digest_at_same_size(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    evidence_id = store_observation(store, b"real")
    (tmp_path / "objects" / evidence_id).write_bytes(b"fake")

    with pytest.raises(EvidenceIntegrityError, match=f"line 1.*{evidence_id}.*digest"):
        store.verify()


def test_store_rejects_root_that_is_a_file(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_bytes(b"file")

    with pytest.raises(ValueError, match="directory"):
        EvidenceStore(root)


def test_index_append_keeps_existing_bytes_unchanged(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store)
    index_path = tmp_path / "index.jsonl"
    first_bytes = index_path.read_bytes()

    store_observation(
        store, b"second", retrieved_at=RETRIEVED_AT + timedelta(seconds=1)
    )

    assert index_path.read_bytes().startswith(first_bytes)
    assert os.path.getsize(index_path) > len(first_bytes)
