import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from touchstone.evidence import EvidenceIntegrityError, EvidenceStore, read_object


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


def test_confirmation_capture_requires_a_full_day_of_separation(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store, b"older", retrieved_at=RETRIEVED_AT)
    current = RETRIEVED_AT + timedelta(hours=23, minutes=59)

    assert store.confirmation_capture("superstate-ustb-nav", before=current) is None
    assert (
        store.confirmation_capture(
            "superstate-ustb-nav", before=RETRIEVED_AT + timedelta(hours=24)
        )
        is not None
    )


def test_confirmation_capture_ignores_other_sources(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store, b"yield", source_id="superstate-ustb-yield")

    assert (
        store.confirmation_capture(
            "superstate-ustb-nav", before=RETRIEVED_AT + timedelta(days=2)
        )
        is None
    )


def test_confirmation_capture_selects_the_newest_qualifying_predecessor(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)
    store_observation(store, b"oldest", retrieved_at=RETRIEVED_AT)
    newest_digest = store_observation(
        store, b"newest-qualifying", retrieved_at=RETRIEVED_AT + timedelta(days=1)
    )
    store_observation(
        store, b"too-recent", retrieved_at=RETRIEVED_AT + timedelta(days=1, hours=23)
    )

    capture = store.confirmation_capture(
        "superstate-ustb-nav", before=RETRIEVED_AT + timedelta(days=2)
    )

    assert capture is not None
    assert capture.sha256 == newest_digest
    assert capture.retrieved_at == RETRIEVED_AT + timedelta(days=1)


def test_confirmation_capture_accepts_identical_bytes_from_a_later_day(
    tmp_path: Path,
) -> None:
    """Unchanged bytes on a separate day are a genuine second observation."""
    store = EvidenceStore(tmp_path)
    digest = store_observation(store, b"unchanged", retrieved_at=RETRIEVED_AT)
    store_observation(
        store, b"unchanged", retrieved_at=RETRIEVED_AT + timedelta(days=1)
    )

    capture = store.confirmation_capture(
        "superstate-ustb-nav", before=RETRIEVED_AT + timedelta(days=2)
    )

    assert capture is not None
    assert capture.sha256 == digest


def test_confirmation_capture_acts_on_the_snapshot_it_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifying one copy of the index and then reading another verifies nothing.

    The two reads are separate syscalls with a window between them, and everything that
    matters — selection, and the signed report built on it — happens on the second. A
    forgery that arrives inside that window is chosen without ever being checked. The fix
    is not a tighter window: it is returning the entries that were verified, so there is
    only ever one snapshot to act on.
    """
    store = EvidenceStore(tmp_path)
    store_observation(store, b"first-day", retrieved_at=RETRIEVED_AT)
    honest = store_observation(
        store, b"second-day", retrieved_at=RETRIEVED_AT + timedelta(days=1)
    )

    honest_index = store.index_path.read_bytes()
    forged_entry = json.loads(honest_index.splitlines()[-1])
    forged_entry["sha256"] = hashlib.sha256(b"never-captured").hexdigest()
    forged_entry["retrieved_at"] = (
        (RETRIEVED_AT + timedelta(days=2)).isoformat().replace("+00:00", "Z")
    )
    forged_index = honest_index + json.dumps(forged_entry).encode() + b"\n"

    reads = []
    real_read_bytes = Path.read_bytes

    def one_honest_read(self: Path) -> bytes:
        if self != store.index_path:
            return real_read_bytes(self)
        reads.append(self)
        # The first read is clean; anything after it sees the forgery. Code that reads
        # once cannot be reached by this at all.
        return honest_index if len(reads) == 1 else forged_index

    monkeypatch.setattr(Path, "read_bytes", one_honest_read)

    capture = store.confirmation_capture(
        "superstate-ustb-nav", before=RETRIEVED_AT + timedelta(days=3)
    )

    assert reads == [store.index_path], "the index was read exactly once"
    assert capture is not None
    assert capture.sha256 == honest, "and the capture came from the verified snapshot"


def test_read_object_refuses_bytes_that_no_longer_match_their_digest(
    tmp_path: Path,
) -> None:
    """The index and the object are separate files; verifying one does not vouch for the other.

    Evaluation consumes the object, not the index entry, so reading it on the strength of
    the index's verification binds a signed report to bytes nobody checked.
    """
    store = EvidenceStore(tmp_path)
    digest = store_observation(store, b'{"nav":"11.17558800"}')
    (store.objects_dir / digest).write_bytes(b'{"nav":"99.99999999"}')

    with pytest.raises(EvidenceIntegrityError, match="now hashes to"):
        read_object(store, digest)


def test_confirmation_capture_rejects_naive_timestamps(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)

    with pytest.raises(ValueError, match="timezone-aware"):
        store.confirmation_capture(
            "superstate-ustb-nav", before=datetime(2026, 8, 14, 17, 8, 12)
        )


def test_an_object_that_cannot_be_read_is_this_modules_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable artifact has not been proved to be the artifact its digest names.

    That is the same conclusion as a mismatch, so it belongs in the same type rather than
    reaching the caller as whatever the filesystem happened to raise.
    """
    store = EvidenceStore(tmp_path)
    digest = store.store(
        b'{"value":1}',
        source_id="superstate-ustb-nav-daily",
        source_url="https://api.superstate.com/v1/funds/1/nav-daily",
        retrieved_at=RETRIEVED_AT,
        declared_mime="application/json",
    )

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)

    with pytest.raises(EvidenceIntegrityError, match="cannot be read"):
        read_object(store, digest)
