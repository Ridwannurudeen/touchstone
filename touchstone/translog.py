"""Append-only hash-chained transparency log for published signed reports."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re

from touchstone.locking import exclusive_lock
from touchstone.signing import (
    Ed25519Signer,
    canonical_json_bytes,
    frozen_snapshot,
    strict_json_loads,
)


LOG_ENTRY_VERSION = "touchstone.transparency-entry.v1"
CHECKPOINT_VERSION = "touchstone.transparency-checkpoint.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TX_HASH = re.compile(r"0x[0-9a-f]{64}")
_ENTRY_FIELDS = {
    "entry_hash",
    "index",
    "prev_entry_hash",
    "publication",
    "report_sha256",
    "signed_report",
    "supersedes",
    "version",
}


class TransparencyLogError(RuntimeError):
    """Raised when a transparency log fails structural or hash verification."""


class TransparencyLog:
    """A canonical JSON-lines log whose entries are never updated or removed."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).resolve()
        if self.path.exists() and not self.path.is_file():
            raise ValueError(f"transparency log path must be a file: {self.path}")

    def append(
        self,
        signed_report: Mapping[str, object],
        *,
        transaction_hash: str,
        receipt: Mapping[str, object],
        supersedes: str | None = None,
    ) -> dict[str, object]:
        """Verify the existing chain and append one publication receipt.

        Under an exclusive lock for the whole read-modify-write. Verifying and then
        appending as two steps lets two writers agree on the same head and append entries
        claiming the same predecessor, which breaks the chain for good — and does so at
        the moment two publishers are both working, not at an idle one.

        The caller's mappings are copied before the lock is even taken. They are checked,
        hashed, and persisted at three separate moments; a caller that still holds a
        reference can change the report between the hash and the write, producing an entry
        whose `report_sha256` names a report the entry does not contain. This is a public
        boundary, so it cannot rely on its callers being careful.
        """
        frozen_report = frozen_snapshot(signed_report, "signed_report")
        frozen_receipt = frozen_snapshot(receipt, "receipt")
        with exclusive_lock(self.path):
            return self._append_locked(
                frozen_report,
                transaction_hash=transaction_hash,
                receipt=frozen_receipt,
                supersedes=supersedes,
            )

    def _append_locked(
        self,
        signed_report: Mapping[str, object],
        *,
        transaction_hash: str,
        receipt: Mapping[str, object],
        supersedes: str | None = None,
    ) -> dict[str, object]:
        entries = self.verify()
        if _TX_HASH.fullmatch(transaction_hash) is None:
            raise ValueError("transaction_hash must be a lowercase 32-byte hex value")
        if transaction_hash in {
            entry["publication"]["transaction_hash"] for entry in entries
        }:
            raise ValueError("transaction_hash is already recorded")
        if supersedes is not None:
            _digest(supersedes, "supersedes")
            if supersedes not in {entry["entry_hash"] for entry in entries}:
                raise ValueError("supersedes must reference an earlier log entry")

        report = signed_report.get("report")
        if not isinstance(report, Mapping):
            raise ValueError("signed_report.report must be a mapping")
        correction_of = report.get("correction_of")
        if supersedes is None and correction_of is not None:
            raise ValueError("correction report must supersede an earlier log entry")
        if supersedes is not None:
            target = next(
                entry for entry in entries if entry["entry_hash"] == supersedes
            )
            target_report = target["signed_report"]["report"]
            if (
                type(correction_of) is not int
                or correction_of != target_report.get("sequence")
                or report.get("asset_key") != target_report.get("asset_key")
            ):
                raise ValueError(
                    "superseding report must reference the target asset and sequence"
                )
        report_sha256 = hashlib.sha256(canonical_json_bytes(dict(report))).hexdigest()
        entry: dict[str, object] = {
            "index": len(entries) + 1,
            "prev_entry_hash": entries[-1]["entry_hash"] if entries else None,
            "publication": {
                "receipt": dict(receipt),
                "transaction_hash": transaction_hash,
            },
            "report_sha256": report_sha256,
            "signed_report": dict(signed_report),
            "supersedes": supersedes,
            "version": LOG_ENTRY_VERSION,
        }
        entry["entry_hash"] = hashlib.sha256(canonical_json_bytes(entry)).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as output:
            output.write(canonical_json_bytes(entry) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        return entry

    def verify(self) -> list[dict[str, object]]:
        """Verify every canonical line, link, digest, and supersession reference."""
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise TransparencyLogError("log is truncated: final line has no newline")

        entries: list[dict[str, object]] = []
        known_hashes: set[str] = set()
        known_transactions: set[str] = set()
        previous: str | None = None
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                parsed = strict_json_loads(line)
            except (
                TypeError,
                ValueError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as error:
                raise TransparencyLogError(
                    f"invalid JSON at line {line_number}: {error}"
                ) from error
            if not isinstance(parsed, dict) or set(parsed) != _ENTRY_FIELDS:
                raise TransparencyLogError(f"invalid schema at line {line_number}")
            if canonical_json_bytes(parsed) != line:
                raise TransparencyLogError(f"line {line_number} is not canonical JSON")
            if parsed["version"] != LOG_ENTRY_VERSION:
                raise TransparencyLogError(f"unsupported version at line {line_number}")
            if parsed["index"] != line_number:
                raise TransparencyLogError(f"invalid index at line {line_number}")
            if parsed["prev_entry_hash"] != previous:
                raise TransparencyLogError(
                    f"line {line_number} has an invalid previous-entry hash"
                )
            try:
                entry_hash = _digest(parsed["entry_hash"], "entry_hash")
                report_hash = _digest(parsed["report_sha256"], "report_sha256")
            except ValueError as error:
                raise TransparencyLogError(
                    f"invalid digest at line {line_number}: {error}"
                ) from error
            without_hash = {
                key: value for key, value in parsed.items() if key != "entry_hash"
            }
            expected = hashlib.sha256(canonical_json_bytes(without_hash)).hexdigest()
            if entry_hash != expected:
                raise TransparencyLogError(
                    f"line {line_number} has an invalid entry hash"
                )
            signed_report = parsed["signed_report"]
            if not isinstance(signed_report, Mapping) or not isinstance(
                signed_report.get("report"), Mapping
            ):
                raise TransparencyLogError(
                    f"invalid signed report at line {line_number}"
                )
            actual_report_hash = hashlib.sha256(
                canonical_json_bytes(dict(signed_report["report"]))
            ).hexdigest()
            if report_hash != actual_report_hash:
                raise TransparencyLogError(
                    f"line {line_number} has an invalid report digest"
                )
            publication = parsed["publication"]
            if not isinstance(publication, Mapping) or set(publication) != {
                "receipt",
                "transaction_hash",
            }:
                raise TransparencyLogError(f"invalid publication at line {line_number}")
            transaction_hash = publication["transaction_hash"]
            if (
                not isinstance(transaction_hash, str)
                or _TX_HASH.fullmatch(transaction_hash) is None
            ):
                raise TransparencyLogError(
                    f"invalid transaction hash at line {line_number}"
                )
            if transaction_hash in known_transactions:
                raise TransparencyLogError(
                    f"duplicate transaction hash at line {line_number}"
                )
            if not isinstance(publication["receipt"], Mapping):
                raise TransparencyLogError(f"invalid receipt at line {line_number}")
            supersedes = parsed["supersedes"]
            if supersedes is not None:
                try:
                    _digest(supersedes, "supersedes")
                except ValueError as error:
                    raise TransparencyLogError(
                        f"invalid supersession at line {line_number}: {error}"
                    ) from error
                if supersedes not in known_hashes:
                    raise TransparencyLogError(
                        f"line {line_number} supersedes no earlier entry"
                    )
                target = next(
                    entry for entry in entries if entry["entry_hash"] == supersedes
                )
                report = signed_report["report"]
                target_report = target["signed_report"]["report"]
                if (
                    type(report.get("correction_of")) is not int
                    or report["correction_of"] != target_report.get("sequence")
                    or report.get("asset_key") != target_report.get("asset_key")
                ):
                    raise TransparencyLogError(
                        f"invalid correction reference at line {line_number}"
                    )
            elif signed_report["report"].get("correction_of") is not None:
                raise TransparencyLogError(
                    f"correction has no superseded entry at line {line_number}"
                )
            entries.append(parsed)
            known_hashes.add(entry_hash)
            known_transactions.add(transaction_hash)
            previous = entry_hash
        return entries

    def checkpoint(self, signer: Ed25519Signer) -> dict[str, object]:
        """Return a signed head that an external party can store and later compare."""
        entries = self.verify()
        checkpoint = {
            "entry_count": len(entries),
            "head": entries[-1]["entry_hash"] if entries else None,
            "version": CHECKPOINT_VERSION,
        }
        return signer.sign_report(checkpoint)


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value
