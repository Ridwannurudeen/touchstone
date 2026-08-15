"""Append-only incident log: what went wrong, when, and when it stopped.

An incident is opened when the service cannot do what it was scheduled to do — a source
that would not answer, an epoch that failed, a slot that passed unrun. It is closed by
*appending* a closure that references it. Nothing is ever edited or removed, because the
value of the record is precisely that it cannot be tidied after the fact.

The chain of entry hashes detects a rewritten or reordered history. It cannot, on its own,
detect the deletion of a complete final entry: truncate the file after any entry and what
remains is a perfectly valid shorter chain. So the expected head and count are persisted
separately and compared on every verification. That is a weaker guarantee than it sounds —
an actor who can write both files can still forge both consistently — and it is stated that
way rather than described as tamper-proof.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re

from touchstone.signing import canonical_json_bytes, strict_json_loads


INCIDENT_ENTRY_VERSION = "touchstone.incident-entry.v1"
INCIDENT_HEAD_VERSION = "touchstone.incident-head.v1"

# An entry opens an incident when it references none, and closes one when it does. There
# is no separate "kind of entry" field: two ways of saying the same thing drift apart.

_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENTRY_FIELDS = frozenset(
    {
        "asset_key",
        "closes",
        "detail",
        "entry_hash",
        "index",
        "kind",
        "occurred_at",
        "prev_entry_hash",
        "state",
        "version",
    }
)
_HEAD_FIELDS = frozenset({"count", "head_entry_hash", "version"})

# What an incident is about. Deliberately few: a category nobody can map to an action is a
# category that only makes the log harder to read.
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
EPOCH_FAILED = "EPOCH_FAILED"
SLOT_MISSED = "SLOT_MISSED"
PUBLICATION_UNRESOLVED = "PUBLICATION_UNRESOLVED"
KINDS = frozenset(
    {SOURCE_UNAVAILABLE, EPOCH_FAILED, SLOT_MISSED, PUBLICATION_UNRESOLVED}
)


class IncidentLogError(RuntimeError):
    """The incident log does not verify, or an append would violate its rules."""


@dataclass(frozen=True, slots=True)
class Incident:
    """One open incident, as reconstructed from the log."""

    incident_id: str
    asset_key: str
    kind: str
    detail: str
    occurred_at: str


class IncidentLog:
    """A hash-chained JSON-lines log with a separately persisted head."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        if self.path.exists() and not self.path.is_file():
            raise ValueError(f"incident log path must be a file: {self.path}")
        self.head_path = self.path.with_name(self.path.name + ".head")

    # ------------------------------------------------------------------ writing
    def open_incident(
        self,
        *,
        asset_key: str,
        kind: str,
        detail: str,
        occurred_at: datetime,
        state: str | None = None,
    ) -> dict[str, object]:
        """Record that something stopped working. Returns the entry; its hash is the id."""
        if kind not in KINDS:
            raise IncidentLogError(f"unknown incident kind: {kind!r}")
        return self._append(
            asset_key=asset_key,
            kind=kind,
            detail=detail,
            occurred_at=occurred_at,
            closes=None,
            state=state,
        )

    def close_incident(
        self,
        incident_id: str,
        *,
        detail: str,
        occurred_at: datetime,
        state: str | None = None,
    ) -> dict[str, object]:
        """Record that it started working again, by appending rather than editing."""
        entries = self.verify()
        opened = {
            entry["entry_hash"]: entry
            for entry in entries
            if entry["closes"] is None
        }
        target = opened.get(incident_id)
        if target is None:
            raise IncidentLogError(f"no incident was opened with id {incident_id}")
        if any(entry["closes"] == incident_id for entry in entries):
            raise IncidentLogError(f"incident {incident_id} is already closed")
        return self._append(
            asset_key=target["asset_key"],
            kind=target["kind"],
            detail=detail,
            occurred_at=occurred_at,
            closes=incident_id,
            state=state,
            entries=entries,
        )

    # ------------------------------------------------------------------ reading
    def verify(self) -> list[dict[str, object]]:
        """Read the log, prove its chain, and prove nothing was cut off the end."""
        entries = self._read()
        previous: str | None = None
        for index, entry in enumerate(entries):
            if set(entry) != _ENTRY_FIELDS:
                raise IncidentLogError(
                    f"incident entry {index} fields must be exactly "
                    f"{sorted(_ENTRY_FIELDS)}"
                )
            if entry["version"] != INCIDENT_ENTRY_VERSION:
                raise IncidentLogError(f"incident entry {index} version is unsupported")
            if entry["index"] != index:
                raise IncidentLogError(
                    f"incident entry {index} claims index {entry['index']}"
                )
            if entry["prev_entry_hash"] != previous:
                raise IncidentLogError(f"incident entry {index} breaks the hash chain")
            expected = _entry_hash(entry)
            if entry["entry_hash"] != expected:
                raise IncidentLogError(
                    f"incident entry {index} hash does not match its content"
                )
            if entry["kind"] not in KINDS:
                raise IncidentLogError(f"incident entry {index} has an unknown kind")
            previous = expected

        self._verify_head(entries)
        _verify_closures(entries)
        return entries

    def open_incidents(self, asset_key: str | None = None) -> list[Incident]:
        """Every incident that has been opened and not since closed."""
        entries = self.verify()
        closed = {
            entry["closes"] for entry in entries if entry["closes"] is not None
        }
        return [
            Incident(
                incident_id=entry["entry_hash"],
                asset_key=entry["asset_key"],
                kind=entry["kind"],
                detail=entry["detail"],
                occurred_at=entry["occurred_at"],
            )
            for entry in entries
            if entry["closes"] is None
            and entry["entry_hash"] not in closed
            and (asset_key is None or entry["asset_key"] == asset_key)
        ]

    # ------------------------------------------------------------------ internals
    def _append(
        self,
        *,
        asset_key: str,
        kind: str,
        detail: str,
        occurred_at: datetime,
        closes: str | None,
        state: str | None,
        entries: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        if entries is None:
            entries = self.verify()
        if not isinstance(asset_key, str) or not asset_key:
            raise IncidentLogError("asset_key must be nonempty text")
        if not isinstance(detail, str) or not detail.strip():
            raise IncidentLogError("an incident must say what happened")
        entry = {
            "asset_key": asset_key,
            "closes": closes,
            "detail": detail,
            "index": len(entries),
            "kind": kind,
            "occurred_at": _stamp(occurred_at),
            "prev_entry_hash": entries[-1]["entry_hash"] if entries else None,
            "state": state,
            "version": INCIDENT_ENTRY_VERSION,
        }
        entry["entry_hash"] = _entry_hash({**entry, "entry_hash": ""})
        # The record is only meaningful if it survives the failure it describes, so the
        # line is on disk before the head that attests to it.
        with self.path.open("ab") as log:
            log.write(canonical_json_bytes(entry) + b"\n")
            log.flush()
            os.fsync(log.fileno())
        self._write_head(len(entries) + 1, entry["entry_hash"])
        return entry

    def _read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_bytes()
        except OSError as error:
            raise IncidentLogError(f"cannot read incident log: {error}") from error
        entries: list[dict[str, object]] = []
        for number, line in enumerate(raw.splitlines()):
            if not line.strip():
                continue
            try:
                value = strict_json_loads(line)
            except (TypeError, ValueError) as error:
                raise IncidentLogError(
                    f"incident log line {number} is not strict JSON: {error}"
                ) from error
            if not isinstance(value, dict):
                raise IncidentLogError(f"incident log line {number} is not an object")
            entries.append(value)
        return entries

    def _write_head(self, count: int, head_entry_hash: str) -> None:
        head = {
            "count": count,
            "head_entry_hash": head_entry_hash,
            "version": INCIDENT_HEAD_VERSION,
        }
        temporary = self.head_path.with_name(self.head_path.name + ".tmp")
        with temporary.open("wb") as output:
            output.write(canonical_json_bytes(head) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.head_path)

    def _verify_head(self, entries: list[dict[str, object]]) -> None:
        """Compare the log against the head written beside it.

        This is what catches a truncation: cutting entries off the end leaves a valid
        chain, so the chain cannot object. It is not tamper-proofing — an actor able to
        write both files can forge both consistently — it detects loss and partial edits.
        """
        if not self.head_path.exists():
            if entries:
                raise IncidentLogError(
                    "the incident log has entries but no head to attest to them; its "
                    "completeness cannot be established"
                )
            return
        try:
            head = strict_json_loads(self.head_path.read_bytes())
        except (OSError, TypeError, ValueError) as error:
            raise IncidentLogError(f"cannot read incident head: {error}") from error
        if not isinstance(head, dict) or set(head) != _HEAD_FIELDS:
            raise IncidentLogError("incident head fields are not as expected")
        if head["version"] != INCIDENT_HEAD_VERSION:
            raise IncidentLogError("incident head version is unsupported")
        if head["count"] != len(entries):
            raise IncidentLogError(
                f"incident head expects {head['count']} entries, the log holds "
                f"{len(entries)}"
            )
        expected = entries[-1]["entry_hash"] if entries else None
        if head["head_entry_hash"] != expected:
            raise IncidentLogError("incident head does not name the log's final entry")


def _verify_closures(entries: list[dict[str, object]]) -> None:
    opened: set[str] = set()
    closed: set[str] = set()
    for entry in entries:
        closes = entry["closes"]
        if closes is None:
            opened.add(entry["entry_hash"])
            continue
        if not isinstance(closes, str) or _DIGEST.fullmatch(closes) is None:
            raise IncidentLogError("a closure must reference an entry hash")
        if closes not in opened:
            raise IncidentLogError(
                f"closure at index {entry['index']} references {closes}, which was never "
                "opened before it"
            )
        if closes in closed:
            raise IncidentLogError(f"incident {closes} is closed more than once")
        closed.add(closes)


def _entry_hash(entry: Mapping[str, object]) -> str:
    body = {key: value for key, value in entry.items() if key != "entry_hash"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _stamp(occurred_at: datetime) -> str:
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
        raise IncidentLogError("an incident instant must be timezone-aware")
    return occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
