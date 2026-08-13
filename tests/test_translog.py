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
