from collections.abc import Mapping
from pathlib import Path

import pytest

from touchstone.signing import Ed25519Signer
from touchstone.translog import TransparencyLog, TransparencyLogError


def _signed_report(sequence: int = 1, *, correction_of: int | None = None):
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    return signer.sign_report(
        {
            "asset_key": "eip155:1:0x" + "11" * 20,
            "publisher_kid": signer.kid,
            "sequence": sequence,
            "correction_of": correction_of,
        }
    )


def test_log_appends_verifies_and_checkpoints(tmp_path: Path) -> None:
    log = TransparencyLog(tmp_path / "transparency.jsonl")
    first = log.append(
        _signed_report(),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    second = log.append(
        _signed_report(2, correction_of=1),
        transaction_hash="0x" + "bb" * 32,
        receipt={"block_number": 2, "status": 1},
        supersedes=first["entry_hash"],
    )

    assert len(log.verify()) == 2
    assert second["prev_entry_hash"] == first["entry_hash"]
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    checkpoint = log.checkpoint(signer)
    assert checkpoint["report"] == {
        "entry_count": 2,
        "head": second["entry_hash"],
        "version": "touchstone.transparency-checkpoint.v1",
    }


def test_log_detects_one_byte_tamper(tmp_path: Path) -> None:
    path = tmp_path / "transparency.jsonl"
    log = TransparencyLog(path)
    log.append(
        _signed_report(),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'"status":1', b'"status":0', 1))

    with pytest.raises(TransparencyLogError, match="invalid entry hash"):
        log.verify()


def test_log_rejects_unknown_supersession(tmp_path: Path) -> None:
    log = TransparencyLog(tmp_path / "transparency.jsonl")
    with pytest.raises(ValueError, match="earlier log entry"):
        log.append(
            _signed_report(2, correction_of=1),
            transaction_hash="0x" + "aa" * 32,
            receipt={"block_number": 1, "status": 1},
            supersedes="11" * 32,
        )


def test_log_rejects_truncated_final_line(tmp_path: Path) -> None:
    path = tmp_path / "transparency.jsonl"
    log = TransparencyLog(path)
    log.append(
        _signed_report(),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    path.write_bytes(path.read_bytes()[:-1])

    with pytest.raises(TransparencyLogError, match="truncated"):
        log.verify()


def test_log_rejects_correction_without_matching_supersession(tmp_path: Path) -> None:
    log = TransparencyLog(tmp_path / "transparency.jsonl")
    first = log.append(
        _signed_report(),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    with pytest.raises(ValueError, match="target asset and sequence"):
        log.append(
            _signed_report(2, correction_of=99),
            transaction_hash="0x" + "bb" * 32,
            receipt={"block_number": 2, "status": 1},
            supersedes=first["entry_hash"],
        )


class _ShiftingEnvelope(Mapping):
    """A caller's mapping whose report changes on every read.

    Not a contrived input: any envelope a caller still holds a reference to can be
    rewritten by that caller — or by a retry, a callback, or another thread — between the
    log's separate reads of it.
    """

    def __init__(self, envelope: Mapping[str, object]) -> None:
        self._envelope = dict(envelope)
        self._envelope["report"] = dict(self._envelope["report"])
        self.reads = 0

    def __getitem__(self, key: str) -> object:
        if key != "report":
            return self._envelope[key]
        self.reads += 1
        # The *same* nested object every time, mutated in place. Returning a fresh dict
        # meant a shallow `dict(envelope)` already captured an independent report, so the
        # test passed against a copy shallow enough to leave the real hazard open.
        self._envelope["report"]["sequence"] = self.reads
        return self._envelope["report"]

    def __iter__(self):
        return iter(self._envelope)

    def __len__(self) -> int:
        return len(self._envelope)


def test_append_records_the_report_it_hashed_even_if_the_caller_changes_it(
    tmp_path: Path,
) -> None:
    """The log checks, hashes, and persists the report at three separate moments.

    Reading the caller's mapping at each of them lets it hash report A and persist report
    B, producing an entry whose `report_sha256` names something the entry does not contain
    — a log that fails its own verification, written by code that succeeded. Freezing at
    the boundary means there is only one report to read.
    """
    log = TransparencyLog(tmp_path / "transparency.jsonl")
    envelope = _ShiftingEnvelope(_signed_report())

    entry = log.append(
        envelope,
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )

    assert envelope.reads == 1, "the caller's mapping was read exactly once"
    assert log.verify() == [entry], (
        "and the persisted entry verifies against its digest"
    )


def test_append_records_the_receipt_it_was_given(tmp_path: Path) -> None:
    """The receipt is caller-owned too, and it is the on-chain half of the record."""
    log = TransparencyLog(tmp_path / "transparency.jsonl")
    receipt = {"block_number": 1, "confirmations": {"depth": 12}, "status": 1}

    entry = log.append(
        _signed_report(),
        transaction_hash="0x" + "aa" * 32,
        receipt=receipt,
    )
    receipt["status"] = 0
    receipt["block_number"] = 999
    # Nested as well as flat. A shallow copy passes the flat case and shares this one.
    receipt["confirmations"]["depth"] = 0

    assert entry["publication"]["receipt"] == {
        "block_number": 1,
        "confirmations": {"depth": 12},
        "status": 1,
    }
    assert log.verify() == [entry]
