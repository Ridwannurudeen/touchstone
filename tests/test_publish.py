from pathlib import Path

import pytest
from web3.exceptions import TimeExhausted

from touchstone.publish import (
    ChainReport,
    DuplicateSequence,
    PendingSubmission,
    PublisherClient,
    SequenceMismatch,
)
from touchstone.signing import Ed25519Signer
from touchstone.translog import TransparencyLog


class FakeBackend:
    def __init__(self) -> None:
        self.reports: dict[bytes, list[ChainReport]] = {}
        self.receipts: dict[str, dict[str, object]] = {}
        self.submissions: list[int | None] = []
        self.time_out_once = False

    def latest_sequence(self, asset_key: bytes) -> int:
        return len(self.reports.get(asset_key, []))

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport:
        return self.reports[asset_key][sequence - 1]

    def submit(self, asset_key, report, report_uri, correction_of):
        self.submissions.append(correction_of)
        transaction_hash = "0x" + f"{len(self.submissions):064x}"
        self.reports.setdefault(asset_key, []).append(
            ChainReport(
                control_set_root=report["control_set_root"],
                evidence_root=report["evidence_root"],
                status=0,
                observed_at=1_786_630_577,
                valid_until=1_786_665_599,
                publisher="0x" + "22" * 20,
                sequence=report["sequence"],
                report_uri=report_uri,
            )
        )
        self.receipts[transaction_hash] = {
            "blockHash": bytes.fromhex("aa" * 32),
            "blockNumber": len(self.submissions),
            "gasUsed": 200_000,
            "status": 1,
        }
        return transaction_hash

    def get_receipt(self, transaction_hash):
        return self.receipts.get(transaction_hash)

    def wait_for_receipt(self, transaction_hash, timeout):
        del timeout
        if self.time_out_once:
            self.time_out_once = False
            raise TimeExhausted("pending")
        return self.receipts[transaction_hash]

    def find_receipt(self, asset_key, sequence, correction_of):
        del correction_of
        for index, report in enumerate(self.reports.get(asset_key, []), 1):
            if report.sequence == sequence:
                transaction_hash = "0x" + f"{index:064x}"
                return transaction_hash, self.receipts[transaction_hash]
        return None


def _signed_report(sequence: int, *, correction_of: int | None = None):
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    return signer.sign_report(
        {
            "asset_key": "eip155:1:0x" + "11" * 20,
            "control_set_root": "22" * 32,
            "correction_of": correction_of,
            "evidence_root": "33" * 32,
            "observed_at": "2026-08-13T14:16:17Z",
            "publisher_kid": signer.kid,
            "sequence": sequence,
            "state": "CONFIRMED",
            "state_transition": {
                "as_of": "2026-08-13",
                "evidence_deadline": "2026-08-13",
            },
            "valid_until": "2026-08-13T23:59:59Z",
        }
    )


def _client(tmp_path: Path, backend: FakeBackend) -> PublisherClient:
    return PublisherClient(
        backend,
        TransparencyLog(tmp_path / "transparency.jsonl"),
        tmp_path / "pending.json",
    )


def _published_key():
    return Ed25519Signer.from_seed(bytes(range(32))).public_key_record()


def test_publisher_reads_sequence_before_submitting_and_refuses_duplicate(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    result = client.publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )

    assert result.reconciled is False
    assert len(backend.submissions) == 1
    with pytest.raises(DuplicateSequence, match="already published"):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )
    assert len(backend.submissions) == 1


def test_publisher_recovers_timed_out_send_without_resubmitting(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.time_out_once = True
    client = _client(tmp_path, backend)

    with pytest.raises(PendingSubmission, match="remains pending"):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )
    result = client.publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )

    assert result.reconciled is True
    assert len(backend.submissions) == 1
    assert not (tmp_path / "pending.json").exists()
    assert len(TransparencyLog(tmp_path / "transparency.jsonl").verify()) == 1


def test_publisher_rejects_sequence_gap_before_submitting(tmp_path: Path) -> None:
    backend = FakeBackend()
    with pytest.raises(SequenceMismatch, match="next report must be 1"):
        _client(tmp_path, backend).publish(
            _signed_report(2),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:2",
        )
    assert backend.submissions == []


def test_correction_uses_distinct_path_and_supersedes_prior_entry(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    first = client.publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )
    second = client.publish_correction(
        _signed_report(2, correction_of=1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:2",
    )

    assert backend.submissions == [None, 1]
    entries = TransparencyLog(tmp_path / "transparency.jsonl").verify()
    assert entries[1]["supersedes"] == first.log_entry_hash
    assert second.log_entry_hash == entries[1]["entry_hash"]


def test_regular_and_correction_entrypoints_reject_wrong_report_kind(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, FakeBackend())
    with pytest.raises(ValueError, match="publish_correction"):
        client.publish(
            _signed_report(2, correction_of=1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:2",
        )
    with pytest.raises(ValueError, match="correction_of"):
        client.publish_correction(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )


def test_publisher_rejects_unsigned_envelope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kid"):
        _client(tmp_path, FakeBackend()).publish(
            {"report": _signed_report(1)["report"]},
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )


def test_publisher_rejects_signed_freshness_extension(tmp_path: Path) -> None:
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = dict(_signed_report(1)["report"])
    report["valid_until"] = "2099-12-31T23:59:59Z"
    with pytest.raises(ValueError, match="evidence deadline"):
        _client(tmp_path, FakeBackend()).publish(
            signer.sign_report(report),
            published_key=signer.public_key_record(),
            report_uri="urn:touchstone:report:1",
        )


def test_publisher_recovers_crash_between_broadcast_and_hash_journal(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    original_write = client._write_pending
    writes = 0

    def crash_on_hash(value):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("simulated crash")
        original_write(value)

    client._write_pending = crash_on_hash
    with pytest.raises(RuntimeError, match="simulated crash"):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )

    recovered = _client(tmp_path, backend).publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )
    assert recovered.reconciled is True
    assert len(backend.submissions) == 1
    assert len(TransparencyLog(tmp_path / "transparency.jsonl").verify()) == 1


def test_publishing_has_no_unlocked_account_path() -> None:
    """The only way to reach the registry is a locally signed transaction.

    An unlocked-account backend existed before PLAN-T6 and was removed rather than kept
    for local convenience: a second path that skips the manifest preflight is a path that
    is never exercised until the day it runs against something real.
    """
    import touchstone.publish as publish_module

    assert not hasattr(publish_module, "Web3RegistryBackend")
    source = Path(publish_module.__file__).read_text(encoding="utf-8")
    assert ".transact(" not in source, (
        "transact() asks a node to sign; every send must be a signed raw transaction"
    )
