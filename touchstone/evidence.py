"""Immutable, content-addressed storage for raw evidence artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_ENTRY_FIELDS = {
    "byte_size",
    "declared_mime",
    "entry_hash",
    "prev_entry_hash",
    "retrieved_at",
    "sha256",
    "source_id",
    "source_url",
}


CONFIRMATION_INTERVAL_SECONDS = 86_400


class EvidenceIntegrityError(RuntimeError):
    """Raised when the evidence index or a referenced object fails verification."""


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """Immutable identity of one retained capture."""

    source_id: str
    sha256: str
    retrieved_at: datetime
    index_position: int


class EvidenceStore:
    """Filesystem evidence store, rooted at ``data/evidence`` by default."""

    def __init__(self, root: str | os.PathLike[str] = Path("data/evidence")) -> None:
        self.root = Path(root)
        if self.root.exists() and not self.root.is_dir():
            raise ValueError(f"evidence root must be a directory: {self.root}")
        self.objects_dir = self.root / "objects"
        self.index_path = self.root / "index.jsonl"

    def store(
        self,
        content: bytes | bytearray | memoryview,
        *,
        source_id: str,
        source_url: str,
        retrieved_at: datetime,
        declared_mime: str,
    ) -> str:
        """Persist one artifact and append its observation metadata."""
        raw = _validate_content(content)
        _validate_nonempty_string(source_id, "source_id")
        _validate_source_url(source_url)
        retrieved_at_text = _normalize_retrieval_time(retrieved_at)
        _validate_nonempty_string(declared_mime, "declared_mime")

        self.verify()
        digest = hashlib.sha256(raw).hexdigest()
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self._write_object_once(digest, raw)

        previous_hash = self._last_entry_hash()
        entry: dict[str, Any] = {
            "byte_size": len(raw),
            "declared_mime": declared_mime,
            "prev_entry_hash": previous_hash,
            "retrieved_at": retrieved_at_text,
            "sha256": digest,
            "source_id": source_id,
            "source_url": source_url,
        }
        entry["entry_hash"] = hashlib.sha256(_canonical_bytes(entry)).hexdigest()
        line = _canonical_bytes(entry) + b"\n"
        with self.index_path.open("ab") as index_file:
            index_file.write(line)
            index_file.flush()
            os.fsync(index_file.fileno())
        return digest

    def confirmation_capture(
        self, source_id: str, *, before: datetime
    ) -> CaptureRecord | None:
        """Return the newest capture of ``source_id`` old enough to confirm ``before``.

        Qualifying captures precede ``before`` in index order and were retrieved at least
        ``CONFIRMATION_INTERVAL_SECONDS`` earlier, so two captures taken minutes apart —
        including either side of midnight — never confirm each other.
        """
        _validate_nonempty_string(source_id, "source_id")
        if not isinstance(before, datetime):
            raise TypeError("before must be a datetime")
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("before must be timezone-aware")

        self.verify()
        deadline = before.astimezone(timezone.utc) - timedelta(
            seconds=CONFIRMATION_INTERVAL_SECONDS
        )
        qualifying: list[CaptureRecord] = []
        for position, entry in enumerate(self._entries()):
            if entry["source_id"] != source_id:
                continue
            retrieved_at = datetime.fromisoformat(entry["retrieved_at"][:-1] + "+00:00")
            if retrieved_at <= deadline:
                qualifying.append(
                    CaptureRecord(
                        source_id=source_id,
                        sha256=entry["sha256"],
                        retrieved_at=retrieved_at,
                        index_position=position,
                    )
                )
        return max(
            qualifying,
            key=lambda record: (record.retrieved_at, record.index_position),
            default=None,
        )

    def _entries(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        index_bytes = self.index_path.read_bytes()
        if not index_bytes:
            return []
        return [
            self._decode_entry(line, number)
            for number, line in enumerate(index_bytes.splitlines(), 1)
        ]

    def verify(self) -> int:
        """Verify the complete index chain and all referenced objects."""
        if not self.index_path.exists():
            return 0
        if not self.index_path.is_file():
            raise EvidenceIntegrityError(f"index is not a file: {self.index_path}")

        try:
            index_bytes = self.index_path.read_bytes()
        except OSError as error:
            raise EvidenceIntegrityError(f"cannot read index: {error}") from error
        if not index_bytes:
            return 0
        if not index_bytes.endswith(b"\n"):
            raise EvidenceIntegrityError(
                "index is truncated: final line has no newline"
            )

        expected_previous: str | None = None
        entries = 0
        for line_number, raw_line in enumerate(
            index_bytes.splitlines(keepends=True), 1
        ):
            entry = self._decode_entry(raw_line[:-1], line_number)
            self._verify_entry(entry, raw_line[:-1], line_number, expected_previous)
            expected_previous = entry["entry_hash"]
            entries += 1
        return entries

    def _write_object_once(self, digest: str, content: bytes) -> None:
        object_path = self.objects_dir / digest
        if object_path.exists():
            self._verify_object(
                object_path, digest, len(content), context="existing object"
            )
            return

        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.objects_dir, prefix=f".{digest}."
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            self._verify_object(
                temporary_path, digest, len(content), context="temporary object"
            )
            try:
                os.link(temporary_path, object_path)
            except FileExistsError:
                self._verify_object(
                    object_path, digest, len(content), context="existing object"
                )
        finally:
            temporary_path.unlink(missing_ok=True)

    def _last_entry_hash(self) -> str | None:
        if not self.index_path.exists() or self.index_path.stat().st_size == 0:
            return None
        last_line = self.index_path.read_bytes().splitlines()[-1]
        entry = self._decode_entry(last_line, 0)
        return entry["entry_hash"]

    def _decode_entry(self, raw_line: bytes, line_number: int) -> dict[str, Any]:
        try:
            text = raw_line.decode("utf-8")
            entry = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            context = f"line {line_number}" if line_number else "last index line"
            raise EvidenceIntegrityError(
                f"invalid JSON at {context}: {error}"
            ) from error
        if not isinstance(entry, dict):
            context = f"line {line_number}" if line_number else "last index line"
            raise EvidenceIntegrityError(f"index entry at {context} must be an object")
        return entry

    def _verify_entry(
        self,
        entry: dict[str, Any],
        raw_line: bytes,
        line_number: int,
        expected_previous: str | None,
    ) -> None:
        if set(entry) != _ENTRY_FIELDS:
            missing = sorted(_ENTRY_FIELDS - set(entry))
            unknown = sorted(set(entry) - _ENTRY_FIELDS)
            raise EvidenceIntegrityError(
                f"invalid schema at line {line_number}: missing={missing}, unknown={unknown}"
            )
        try:
            _validate_nonempty_string(entry["source_id"], "source_id")
            _validate_source_url(entry["source_url"])
            _validate_nonempty_string(entry["declared_mime"], "declared_mime")
            _validate_retrieval_time_text(entry["retrieved_at"])
            _validate_hash(entry["sha256"], "sha256")
            _validate_hash(entry["entry_hash"], "entry_hash")
            if entry["prev_entry_hash"] is not None:
                _validate_hash(entry["prev_entry_hash"], "prev_entry_hash")
            if (
                isinstance(entry["byte_size"], bool)
                or not isinstance(entry["byte_size"], int)
                or entry["byte_size"] < 0
            ):
                raise ValueError("byte_size must be a non-negative integer")
        except (TypeError, ValueError) as error:
            raise EvidenceIntegrityError(
                f"invalid schema at line {line_number}: {error}"
            ) from error

        if entry["prev_entry_hash"] != expected_previous:
            raise EvidenceIntegrityError(
                f"line {line_number} has an invalid previous-entry hash"
            )
        try:
            canonical_line = _canonical_bytes(entry)
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise EvidenceIntegrityError(
                f"line {line_number} cannot be canonically encoded: {error}"
            ) from error
        if raw_line != canonical_line:
            raise EvidenceIntegrityError(f"line {line_number} is not canonical JSON")

        entry_without_hash = {
            key: value for key, value in entry.items() if key != "entry_hash"
        }
        expected_hash = hashlib.sha256(_canonical_bytes(entry_without_hash)).hexdigest()
        if entry["entry_hash"] != expected_hash:
            raise EvidenceIntegrityError(
                f"line {line_number} has an invalid entry hash"
            )

        digest = entry["sha256"]
        self._verify_object(
            self.objects_dir / digest,
            digest,
            entry["byte_size"],
            context=f"line {line_number}",
        )

    @staticmethod
    def _verify_object(
        object_path: Path, digest: str, expected_size: int, *, context: str
    ) -> None:
        if not object_path.is_file():
            raise EvidenceIntegrityError(f"{context}: object {digest} is missing")
        try:
            size = object_path.stat().st_size
            if size != expected_size:
                raise EvidenceIntegrityError(
                    f"{context}: object {digest} has size {size}, expected {expected_size}"
                )
            actual_digest = hashlib.sha256(object_path.read_bytes()).hexdigest()
        except OSError as error:
            raise EvidenceIntegrityError(
                f"{context}: cannot read object {digest}: {error}"
            ) from error
        if actual_digest != digest:
            raise EvidenceIntegrityError(f"{context}: object {digest} has wrong digest")


def _validate_content(content: object) -> bytes:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("content must be bytes-like")
    return bytes(content)


def _validate_nonempty_string(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    value.encode("utf-8", errors="strict")


def _validate_source_url(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError("source_url must be a string")
    value.encode("utf-8", errors="strict")
    if any(character.isspace() for character in value):
        raise ValueError("source_url must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError("source_url must be an absolute HTTP(S) URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("source_url must be an absolute HTTP(S) URL")


def _normalize_retrieval_time(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("retrieved_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_retrieval_time_text(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("retrieved_at must be a normalized UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("retrieved_at must be a normalized UTC timestamp") from error
    if _normalize_retrieval_time(parsed) != value:
        raise ValueError("retrieved_at must be a normalized UTC timestamp")


def _validate_hash(value: object, field: str) -> None:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
