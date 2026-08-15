from pathlib import Path

import pytest
from web3.exceptions import TimeExhausted

from web3 import Web3

from touchstone.publish import (
    ChainReport,
    DuplicateSequence,
    PendingSubmission,
    PreflightFailed,
    PreparedTransaction,
    PublisherClient,
    SequenceMismatch,
)
from touchstone.signing import (
    Ed25519Signer,
    canonical_json_bytes,
    strict_json_loads,
)
from touchstone.translog import TransparencyLog


class FakeBackend:
    """Records what was prepared and what was actually broadcast, separately.

    The distinction is the point: preparation can refuse, and a refusal must leave nothing
    behind. Broadcasting the same prepared bytes twice must land exactly one publication.
    """

    def __init__(self) -> None:
        self.reports: dict[bytes, list[ChainReport]] = {}
        self.receipts: dict[str, dict[str, object]] = {}
        self.submissions: list[int | None] = []
        self.intents: dict[str, tuple] = {}
        self.broadcasts: list[str] = []
        self.revalidations = 0
        self.prepared = 0
        self.time_out_once = False
        self.refuse_before_signing = False
        self.drop_first_broadcast = False

    def revalidate(self) -> None:
        self.revalidations += 1

    def latest_sequence(self, asset_key: bytes) -> int:
        return len(self.reports.get(asset_key, []))

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport:
        return self.reports[asset_key][sequence - 1]

    def prepare(self, asset_key, report, report_uri, correction_of):
        """Produce bytes whose hash is genuinely their digest, as a real signer would."""
        if self.refuse_before_signing:
            raise PreflightFailed("definite refusal reached before any broadcast")
        self.prepared += 1
        raw = canonical_json_bytes(
            {"nonce": self.prepared - 1, "sequence": report["sequence"]}
        )
        transaction_hash = "0x" + Web3.keccak(raw).hex().removeprefix("0x")
        self.intents[transaction_hash] = (
            asset_key,
            dict(report),
            report_uri,
            correction_of,
        )
        return PreparedTransaction(
            transaction_hash=transaction_hash, raw=raw, nonce=self.prepared - 1
        )

    def broadcast(self, prepared) -> str:
        self.broadcasts.append(prepared.transaction_hash)
        if self.drop_first_broadcast:
            # The node accepted it and then the transaction never appeared: the
            # mempool-eviction case a rebroadcast is supposed to survive.
            self.drop_first_broadcast = False
            return prepared.transaction_hash
        if prepared.transaction_hash in self.receipts:
            return prepared.transaction_hash
        asset_key, report, report_uri, correction_of = self.intents[
            prepared.transaction_hash
        ]
        self.submissions.append(correction_of)
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
        self.receipts[prepared.transaction_hash] = {
            "blockHash": bytes.fromhex("aa" * 32),
            "blockNumber": len(self.submissions),
            "gasUsed": 200_000,
            "status": 1,
        }
        return prepared.transaction_hash

    def get_receipt(self, transaction_hash):
        return self.receipts.get(transaction_hash)

    def wait_for_receipt(self, transaction_hash, timeout):
        del timeout
        if self.time_out_once:
            self.time_out_once = False
            raise TimeExhausted("pending")
        if transaction_hash not in self.receipts:
            raise TimeExhausted("pending")
        return self.receipts[transaction_hash]

    def find_receipt(self, asset_key, sequence, correction_of):
        del correction_of
        for transaction_hash, (key, report, _, _) in self.intents.items():
            if key == asset_key and report["sequence"] == sequence:
                receipt = self.receipts.get(transaction_hash)
                if receipt is not None:
                    return transaction_hash, receipt
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


def test_publisher_recovers_crash_between_journal_and_broadcast(tmp_path: Path) -> None:
    """The journal now precedes the send, so this is the crash that can actually happen.

    The record names a transaction that may never have reached the wire. Because it holds
    the exact signed bytes, recovery re-sends those bytes rather than guessing: same nonce,
    same hash, and therefore exactly one publication however many times it is attempted.
    """
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    backend_broadcast = backend.broadcast

    def crash_before_sending(prepared):
        raise RuntimeError("simulated crash")

    backend.broadcast = crash_before_sending
    with pytest.raises(RuntimeError, match="simulated crash"):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )
    assert (tmp_path / "pending.json").exists(), (
        "the signed bytes must survive the crash"
    )
    assert backend.submissions == []

    backend.broadcast = backend_broadcast
    recovered = _client(tmp_path, backend).publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )

    assert recovered.reconciled is True
    assert len(backend.submissions) == 1
    assert backend.prepared == 1, (
        "recovery re-sends the signed bytes, it does not re-sign"
    )
    assert len(TransparencyLog(tmp_path / "transparency.jsonl").verify()) == 1


def test_a_refusal_before_signing_leaves_no_pending_record(tmp_path: Path) -> None:
    """A definite refusal is not an unknown broadcast outcome.

    Journalling before `submit` meant that preflight, gas estimation, the fee ceiling and
    signing — all of which refuse without touching the wire — left a record saying a
    transaction might be out there. The next run then refused forever to protect a
    transaction that never existed.
    """
    backend = FakeBackend()
    backend.refuse_before_signing = True
    client = _client(tmp_path, backend)

    with pytest.raises(PreflightFailed, match="before any broadcast"):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )

    assert not (tmp_path / "pending.json").exists()
    assert backend.broadcasts == []

    backend.refuse_before_signing = False
    result = client.publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )

    assert result.reconciled is False
    assert len(backend.submissions) == 1


def test_a_dropped_transaction_is_rebroadcast_rather_than_abandoned(
    tmp_path: Path,
) -> None:
    """A node that accepts bytes and then loses them must not end publication forever."""
    backend = FakeBackend()
    backend.drop_first_broadcast = True
    client = _client(tmp_path, backend)

    with pytest.raises(PendingSubmission, match="remains pending"):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )
    assert backend.submissions == []

    result = _client(tmp_path, backend).publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )

    assert result.reconciled is True
    assert len(backend.broadcasts) == 2, "the same bytes were sent again"
    assert len(set(backend.broadcasts)) == 1, "and they were the same bytes"
    assert len(backend.submissions) == 1, "landing exactly one publication"


def test_an_edited_pending_record_is_refused(tmp_path: Path) -> None:
    """A transaction hash is the digest of its signed bytes, so the two must agree."""
    backend = FakeBackend()
    backend.drop_first_broadcast = True
    client = _client(tmp_path, backend)
    with pytest.raises(PendingSubmission):
        client.publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )

    pending = tmp_path / "pending.json"
    tampered = strict_json_loads(pending.read_bytes())
    tampered["raw_transaction"] = canonical_json_bytes({"nonce": 99}).hex()
    pending.write_bytes(canonical_json_bytes(tampered) + b"\n")

    with pytest.raises(PendingSubmission, match="signed bytes hash to"):
        _client(tmp_path, backend).publish(
            _signed_report(1),
            published_key=_published_key(),
            report_uri="urn:touchstone:report:1",
        )
    assert len(backend.broadcasts) == 1, "nothing further was sent"


def test_endpoint_identity_is_reproved_within_a_publication(tmp_path: Path) -> None:
    """Caching a verified identity for the life of the process was the defect.

    An endpoint repointed between phases would otherwise serve the reads that decide a
    publication is real under an identity checked long before.
    """
    backend = FakeBackend()
    client = _client(tmp_path, backend)

    client.publish(
        _signed_report(1),
        published_key=_published_key(),
        report_uri="urn:touchstone:report:1",
    )

    assert backend.revalidations >= 2, (
        "identity is reproved at the start of the publication and again before the "
        "onchain result is accepted"
    )


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
