from collections.abc import Mapping
from datetime import datetime, time, timezone
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import TimeExhausted

from touchstone.deployment import DeploymentManifest
from touchstone.keyring import PublisherKey
from touchstone.publish import (  # noqa: F401
    CONFIRMED,
    INCLUDED,
    MISSING,
    ChainReport,
    DeploymentIdentity,
    DuplicateSequence,
    PendingSubmission,
    PreflightFailed,
    PreparedTransaction,
    PublisherClient,
    SequenceMismatch,
    SubmissionFailed,
    asset_key_bytes,
)
from touchstone.signing import (
    Ed25519Signer,
    canonical_json_bytes,
    strict_json_loads,
)
from touchstone.translog import TransparencyLog


PUBLISHER_SECRET = "a1" * 32
DEPLOYER_SECRET = "b2" * 32
OPERATIONS_SECRET = "c3" * 32
REGISTRY = Web3.to_checksum_address("0x" + "ab" * 20)
# The asset every fixture report is about; shared so other suites need not restate it.
ASSET_KEY_OF = "eip155:1:0x" + "11" * 20
REPORTER = Ed25519Signer.from_seed(bytes(range(32)))
CHAIN_ID = 31337
STRANGER_SECRET = "d4" * 32
# The publishing identity the registry recorded; it survives a rotation of the address.
REGISTRY_LINEAGE = Account.from_key(bytes.fromhex(PUBLISHER_SECRET)).address
# The registry's own encoding, read off `TouchstoneRegistry.sol` — declaration order of
# `enum Status`. Deliberately not imported from `touchstone.publish`: the fake stands in for
# the chain, and a fake that shares the production mapping cannot disagree with it. A
# regression in that mapping would move the expected value and the fake chain's value
# together, and the comparison the publisher makes between them would still pass.
_CHAIN_STATUS = {"CONFIRMED": 0, "STALE": 1, "INCONSISTENT": 2, "UNVERIFIABLE": 3}


def address(secret: str) -> str:
    return Account.from_key(bytes.fromhex(secret)).address


def _chain_seconds(value: str) -> int:
    """A uint64 timestamp as the registry would hold it, parsed independently."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _chain_epoch_key(epoch_id: str) -> bytes:
    """The epoch key the registry would be given, derived here rather than imported."""
    return bytes(Web3.keccak(text=epoch_id))


def _manifest(**overrides: object) -> DeploymentManifest:
    value: dict[str, object] = {
        "manifest_version": 1,
        "network": "hardhat-local",
        "chain_id": CHAIN_ID,
        "rpc_url": "http://127.0.0.1:8545",
        "registry_address": REGISTRY,
        "registry_runtime_bytecode_sha256": "cd" * 32,
        "publisher_address": address(PUBLISHER_SECRET),
        "publisher_identity_address": address(PUBLISHER_SECRET),
        "deployer_address": address(DEPLOYER_SECRET),
        "operations_address": address(OPERATIONS_SECRET),
        "confirmations": 1,
        "deployment_state": "active",
        "deployment_block": 3,
        "reporting_keys": [
            {
                "kid": REPORTER.kid,
                "public_key": REPORTER.public_key_record()["public_key"],
                "state": "active",
            }
        ],
    }
    value.update(overrides)
    return DeploymentManifest.from_mapping(value)


class FakeBackend:
    """Records what was prepared and what was actually broadcast, separately.

    It signs real transactions with a real key, because the journal is now decoded rather
    than trusted: a fake that produced undecodable bytes would pass tests that the
    production path cannot.
    """

    def __init__(
        self,
        manifest: DeploymentManifest | None = None,
        secret: str = PUBLISHER_SECRET,
    ) -> None:
        self.manifest = manifest if manifest is not None else _manifest()
        self.key = PublisherKey.from_hex(secret, self.manifest)
        self.reports: dict[bytes, list[ChainReport]] = {}
        # (asset key, epoch key) -> the sequence that first published that epoch.
        self.epochs: dict[tuple[bytes, bytes], int] = {}
        self.receipts: dict[str, dict[str, object]] = {}
        self.submissions: list[int | None] = []
        self.intents: dict[str, tuple] = {}
        self.broadcasts: list[str] = []
        self.reads: list[str] = []
        self.revalidations = 0
        self.prepared = 0
        self.time_out_once = False
        self.refuse_before_signing = False
        self.drop_first_broadcast = False
        self.withhold_confirmation = False
        self.failing_receipt = False
        # Set to make the state *after* the wait differ from the state during it, which is
        # the only way to show which of the two readings actually decides the outcome.
        self.state_after_wait: str | None = None
        self.status_after_wait: int | None = None
        self.waited = False
        self.publisher_override: str | None = None
        # address -> the publishing identity the registry recorded for it
        self.lineage: dict[str, str] = {}

    def revalidate(self) -> None:
        self.revalidations += 1
        self.reads.append("revalidate")

    def publisher_lineage(self, address: str) -> str:
        """Lineage follows the registry: rotation carries it, strangers do not share it."""
        self.reads.append("publisher_lineage")
        return self.lineage.get(
            Web3.to_checksum_address(address), Web3.to_checksum_address(address)
        )

    def identity(self) -> DeploymentIdentity:
        return DeploymentIdentity(
            chain_id=self.manifest.chain_id,
            registry_address=self.manifest.registry_address,
            publisher_address=self.manifest.publisher_address,
        )

    def latest_sequence(self, asset_key: bytes) -> int:
        self.reads.append("latest_sequence")
        return len(self.reports.get(asset_key, []))

    def epoch_sequence(self, asset_key: bytes, epoch_key: bytes) -> int:
        self.reads.append("epoch_sequence")
        return self.epochs.get((asset_key, epoch_key), 0)

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport:
        self.reads.append("get_report")
        return self.reports[asset_key][sequence - 1]

    def calldata(self, asset_key, report, report_uri, correction_of) -> bytes:
        """Encode the real call, so binding the journal to it means something."""
        return canonical_json_bytes(
            {
                "asset_key": asset_key.hex(),
                "correction_of": correction_of,
                "report_uri": report_uri,
                "sequence": report["sequence"],
            }
        )

    def prepare(self, asset_key, report, report_uri, correction_of):
        if self.refuse_before_signing:
            raise PreflightFailed("definite refusal reached before any broadcast")
        nonce = self.prepared
        self.prepared += 1
        transaction_hash, raw = self.key.sign_transaction(
            {
                "to": self.manifest.registry_address,
                "value": 0,
                "gas": 200_000,
                "maxFeePerGas": 10**9,
                "maxPriorityFeePerGas": 10**8,
                "nonce": nonce,
                "chainId": self.manifest.chain_id,
                "data": self.calldata(asset_key, report, report_uri, correction_of),
            }
        )
        self.intents[transaction_hash] = (
            asset_key,
            dict(report),
            report_uri,
            correction_of,
        )
        return PreparedTransaction(
            transaction_hash=transaction_hash, raw=raw, nonce=nonce
        )

    def broadcast(self, prepared) -> str:
        self.broadcasts.append(prepared.transaction_hash)
        if self.drop_first_broadcast:
            self.drop_first_broadcast = False
            return prepared.transaction_hash
        if prepared.transaction_hash in self.receipts:
            return prepared.transaction_hash
        asset_key, report, report_uri, correction_of = self.intents[
            prepared.transaction_hash
        ]
        epoch_key = _chain_epoch_key(report["epoch_id"])
        if correction_of is None and (asset_key, epoch_key) in self.epochs:
            # The registry's `EpochAlreadyPublished`, which is what actually stops a
            # restarted daemon publishing a second report about one day. A fake that
            # accepted it would let every test of that suppression pass whether the guard
            # existed or not.
            raise SubmissionFailed(
                f"epoch {report['epoch_id']} was already published at sequence "
                f"{self.epochs[(asset_key, epoch_key)]}"
            )
        self.submissions.append(correction_of)
        self.reports.setdefault(asset_key, []).append(
            # Derived from the report, as a registry does. Canned values matched the
            # fixtures this double was written for and nothing else, so the first real
            # report published through it failed its own on-chain match check — a defect
            # in the stand-in, reported as a defect in the service.
            ChainReport(
                control_set_root=report["control_set_root"],
                evidence_root=report["evidence_root"],
                epoch_key=epoch_key.hex(),
                status=_CHAIN_STATUS[report["state"]],
                observed_at=_chain_seconds(report["observed_at"]),
                valid_until=_chain_seconds(report["valid_until"]),
                publisher=self.publisher_override or self.manifest.publisher_address,
                sequence=report["sequence"],
                report_uri=report_uri,
            )
        )
        # A correction restates the epoch it corrects and does not claim it, exactly as the
        # registry leaves `epochSequence` pointing at the first publication.
        self.epochs.setdefault((asset_key, epoch_key), report["sequence"])
        self.receipts[prepared.transaction_hash] = {
            "blockHash": bytes.fromhex("aa" * 32),
            "blockNumber": len(self.submissions),
            "gasUsed": 200_000,
            "status": 0 if self.failing_receipt else 1,
        }
        return prepared.transaction_hash

    def receipt_state(self, transaction_hash):
        self.reads.append("receipt_state")
        if self.waited and self.state_after_wait is not None:
            receipt = self.receipts.get(transaction_hash)
            if self.state_after_wait == MISSING:
                return MISSING, None
            return self.state_after_wait, receipt
        if self.waited and self.status_after_wait is not None:
            receipt = dict(self.receipts[transaction_hash])
            receipt["status"] = self.status_after_wait
            return CONFIRMED, receipt
        return self._state(transaction_hash)

    def _state(self, transaction_hash):
        """The same answer without recording it, for the fake's own internal use."""
        receipt = self.receipts.get(transaction_hash)
        if receipt is None:
            return MISSING, None
        if self.withhold_confirmation:
            return INCLUDED, receipt
        return CONFIRMED, receipt

    def get_receipt(self, transaction_hash):
        state, receipt = self._state(transaction_hash)
        return receipt if state == CONFIRMED else None

    def wait_for_receipt(self, transaction_hash, timeout):
        del timeout
        self.reads.append("wait_for_receipt")
        self.waited = True
        if self.time_out_once:
            self.time_out_once = False
            raise TimeExhausted("pending")
        state, receipt = self._state(transaction_hash)
        if state != CONFIRMED:
            raise TimeExhausted("pending")
        return receipt

    def find_receipt(self, asset_key, sequence, correction_of):
        """Confirmed events only, matching SignedRegistryBackend."""
        del correction_of
        for transaction_hash, (key, report, _, _) in self.intents.items():
            if key == asset_key and report["sequence"] == sequence:
                state, receipt = self._state(transaction_hash)
                if state == CONFIRMED:
                    return transaction_hash, receipt
        return None

    def confirm_publication(
        self, transaction_hash, asset_key, report, report_uri, correction_of
    ):
        expected = (asset_key, dict(report), report_uri, correction_of)
        if self.intents.get(transaction_hash) != expected:
            raise SubmissionFailed("publication transaction does not match")
        state, receipt = self._state(transaction_hash)
        if state != CONFIRMED:
            raise PendingSubmission("publication transaction is not confirmed")
        return receipt


def _signed_report(
    sequence: int,
    *,
    correction_of: int | None = None,
    control_set_root: str = "22" * 32,
    evidence_root: str = "33" * 32,
    state: str = "CONFIRMED",
    observed_at: str = "2026-08-13T14:16:17Z",
    epoch_id: str | None = None,
):
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    # One epoch per sequence by default, because the registry refuses a second publication
    # of an epoch: a suite that advanced the sequence while restating one epoch would be
    # refused everywhere for a reason none of its tests are about. A test that means to
    # republish an epoch names it.
    epoch_id = f"epoch-{sequence}" if epoch_id is None else epoch_id
    # Derived rather than written out beside the timestamp, so a caller choosing a different
    # observation instant cannot accidentally leave the two disagreeing — which the
    # publisher refuses outright.
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    day = observed.date()
    valid_until = max(
        datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc), observed
    )
    return signer.sign_report(
        {
            "asset_key": ASSET_KEY_OF,
            "control_set_root": control_set_root,
            "correction_of": correction_of,
            "epoch_id": epoch_id,
            "evidence_root": evidence_root,
            "observed_at": observed_at,
            "publisher_kid": signer.kid,
            "sequence": sequence,
            "state": state,
            "state_transition": {
                "as_of": day.isoformat(),
                "evidence_deadline": day.isoformat(),
            },
            "valid_until": valid_until.isoformat().replace("+00:00", "Z"),
        }
    )


def _client(tmp_path: Path, backend: FakeBackend) -> PublisherClient:
    return PublisherClient(
        backend,
        TransparencyLog(tmp_path / "transparency.jsonl"),
        tmp_path / "pending.json",
    )


@pytest.mark.parametrize(
    ("state", "ordinal"),
    [("CONFIRMED", 0), ("STALE", 1), ("INCONSISTENT", 2), ("UNVERIFIABLE", 3)],
)
def test_each_state_reaches_the_chain_as_the_registry_declares_it(
    tmp_path: Path, state: str, ordinal: int
) -> None:
    """The four ordinals, against the Solidity enum rather than the encoder that wrote them.

    `AssetGate.check` admits a report by `1 << report.status` against its allowed-status
    mask, so the integer is not bookkeeping — it decides whether a consumer contract lets a
    transaction through. Every report in this suite was CONFIRMED, so swapping STALE and
    INCONSISTENT in the publisher's mapping passed all 1349 tests: a stale asset would have
    been published as inconsistent and an inconsistent one admitted by a gate that allows
    stale.

    The timestamp is deliberately not the fixture instant either, so a mapping that happens
    to agree on canned values is not mistaken for one that agrees.
    """
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    client.publish(
        _signed_report(1, state=state, observed_at="2026-08-14T09:41:03Z"),
        report_uri="urn:touchstone:report:1",
    )

    published = backend.get_report(asset_key_bytes(ASSET_KEY_OF), 1)
    assert published.status == ordinal
    # Literal seconds, so neither the publisher's encoder nor the fake's decoder can move
    # the expectation with them.
    assert published.observed_at == 1786700463
    assert published.valid_until == 1786751999


def test_publisher_reads_sequence_before_submitting_and_refuses_duplicate(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    result = client.publish(
        _signed_report(1),
        report_uri="urn:touchstone:report:1",
    )

    assert result.reconciled is False
    assert len(backend.submissions) == 1
    with pytest.raises(DuplicateSequence, match="already published"):
        client.publish(
            _signed_report(1),
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
            report_uri="urn:touchstone:report:1",
        )
    result = client.publish(
        _signed_report(1),
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
        report_uri="urn:touchstone:report:1",
    )
    second = client.publish_correction(
        _signed_report(2, correction_of=1),
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
            report_uri="urn:touchstone:report:2",
        )
    with pytest.raises(ValueError, match="correction_of"):
        client.publish_correction(
            _signed_report(1),
            report_uri="urn:touchstone:report:1",
        )


def test_publisher_rejects_unsigned_envelope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="active reporting key"):
        _client(tmp_path, FakeBackend()).publish(
            {"report": _signed_report(1)["report"]},
            report_uri="urn:touchstone:report:1",
        )


def test_publisher_rejects_signed_freshness_extension(tmp_path: Path) -> None:
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = dict(_signed_report(1)["report"])
    report["valid_until"] = "2099-12-31T23:59:59Z"
    with pytest.raises(ValueError, match="evidence deadline"):
        _client(tmp_path, FakeBackend()).publish(
            signer.sign_report(report), report_uri="urn:touchstone:report:1"
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
            report_uri="urn:touchstone:report:1",
        )
    assert (tmp_path / "pending.json").exists(), (
        "the signed bytes must survive the crash"
    )
    assert backend.submissions == []

    backend.broadcast = backend_broadcast
    recovered = _client(tmp_path, backend).publish(
        _signed_report(1),
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
            report_uri="urn:touchstone:report:1",
        )

    assert not (tmp_path / "pending.json").exists()
    assert backend.broadcasts == []

    backend.refuse_before_signing = False
    result = client.publish(
        _signed_report(1),
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
            report_uri="urn:touchstone:report:1",
        )
    assert backend.submissions == []

    result = _client(tmp_path, backend).publish(
        _signed_report(1),
        report_uri="urn:touchstone:report:1",
    )

    assert result.reconciled is True
    assert len(backend.broadcasts) == 2, "the same bytes were sent again"
    assert len(set(backend.broadcasts)) == 1, "and they were the same bytes"
    assert len(backend.submissions) == 1, "landing exactly one publication"


def _stranded_pending(tmp_path: Path) -> tuple[FakeBackend, Path]:
    """Leave a journal holding signed bytes that were never mined."""
    backend = FakeBackend()
    backend.drop_first_broadcast = True
    with pytest.raises(PendingSubmission):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    return backend, tmp_path / "pending.json"


def test_an_edited_pending_record_is_refused(tmp_path: Path) -> None:
    """A hash matching its bytes proves only that they belong together.

    Edit both and they still agree, so the transaction is decoded and read back instead:
    the chain it commits to, the contract it calls, who signed it, and the nonce it was
    recorded under. An earlier test changed only the bytes and left the old hash, which
    proved far less than its name claimed.
    """
    backend, pending = _stranded_pending(tmp_path)
    foreign = FakeBackend(
        _manifest(registry_address=Web3.to_checksum_address("0x" + "dd" * 20))
    )
    substitute = foreign.prepare(
        b"\x00" * 32, _signed_report(1)["report"], "urn:touchstone:report:1", None
    )

    # Both fields replaced consistently: the hash genuinely is the digest of the bytes.
    tampered = strict_json_loads(pending.read_bytes())
    tampered["raw_transaction"] = substitute.raw.hex()
    tampered["transaction_hash"] = substitute.transaction_hash
    pending.write_bytes(canonical_json_bytes(tampered) + b"\n")

    with pytest.raises(PendingSubmission, match="signed for registry"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert len(backend.broadcasts) == 1, "nothing further was sent"


def test_a_journal_whose_hash_stops_matching_its_bytes_is_refused(
    tmp_path: Path,
) -> None:
    backend, pending = _stranded_pending(tmp_path)
    tampered = strict_json_loads(pending.read_bytes())
    tampered["raw_transaction"] = canonical_json_bytes({"nonce": 99}).hex()
    pending.write_bytes(canonical_json_bytes(tampered) + b"\n")

    with pytest.raises(PendingSubmission, match="signed bytes hash to"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert len(backend.broadcasts) == 1


def test_a_journalled_nonce_cannot_be_edited(tmp_path: Path) -> None:
    """The nonce is stored beside the bytes, so it must be checked against them."""
    backend, pending = _stranded_pending(tmp_path)
    tampered = strict_json_loads(pending.read_bytes())
    tampered["nonce"] = tampered["nonce"] + 7
    pending.write_bytes(canonical_json_bytes(tampered) + b"\n")

    with pytest.raises(PendingSubmission, match="signed for nonce"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert len(backend.broadcasts) == 1


def test_a_journal_from_another_deployment_is_refused(tmp_path: Path) -> None:
    """The case that matters: a second registry on the same chain.

    Preflight verifies the *new* deployment and passes, and the bytes then still call the
    old registry. Nothing in the hash or the manifest catches that on its own.
    """
    backend, pending = _stranded_pending(tmp_path)
    moved = FakeBackend(
        _manifest(registry_address=Web3.to_checksum_address("0x" + "dd" * 20))
    )
    client = PublisherClient(
        moved, TransparencyLog(tmp_path / "transparency.jsonl"), pending
    )

    with pytest.raises(PendingSubmission, match="signed for registry"):
        client.publish(_signed_report(1), report_uri="urn:touchstone:report:1")
    assert moved.broadcasts == []


def test_a_retired_reporting_key_cannot_publish_through_the_client(
    tmp_path: Path,
) -> None:
    """The rule has to live here, not in the command-line wrapper.

    It was in the wrapper, so calling the client directly published under a retired key
    without objection. Nothing that reaches the chain may depend on which entry point the
    caller happened to use.
    """
    successor = Ed25519Signer.from_seed(bytes(range(1, 33)))
    rotated = _manifest(
        reporting_keys=[
            {
                "kid": REPORTER.kid,
                "public_key": REPORTER.public_key_record()["public_key"],
                "state": "superseded",
                "not_after": "2026-08-15T12:00:00Z",
            },
            {
                "kid": successor.kid,
                "public_key": successor.public_key_record()["public_key"],
                "state": "active",
            },
        ]
    )
    backend = FakeBackend(rotated)

    with pytest.raises(ValueError, match="superseded"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert backend.broadcasts == []
    assert not (tmp_path / "pending.json").exists()


def test_another_publishers_transaction_is_not_adopted_as_ours(
    tmp_path: Path,
) -> None:
    """Matching content does not make a publication ours.

    Another authorized publisher can place an identical payload at the same sequence.
    Reconciliation compared the report body and not the publisher, so it would have
    recorded their transaction hash as though we had sent it.
    """
    backend = FakeBackend()
    backend.publisher_override = address(DEPLOYER_SECRET)

    with pytest.raises(SubmissionFailed, match="was published by"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )


def test_an_included_transaction_is_waited_for_rather_than_resent(
    tmp_path: Path,
) -> None:
    """Included but not yet confirmed is not the same as dropped.

    Collapsing the two made recovery rebroadcast an already-mined transaction, which a
    node answers with "nonce too low" — turning a publication that had in fact succeeded
    into a reported failure.
    """
    backend = FakeBackend()
    backend.withhold_confirmation = True
    with pytest.raises(PendingSubmission):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    sent_before = len(backend.broadcasts)
    assert len(backend.reports[next(iter(backend.reports))]) == 1

    with pytest.raises(PendingSubmission, match="included"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )

    assert len(backend.broadcasts) == sent_before, (
        "an included transaction must never be rebroadcast"
    )


def _assert_revalidation_order(reads: list[str]) -> None:
    """No chain read may precede an identity check, and the wait must be followed by one.

    Counting revalidations proved almost nothing: a read inserted between the receipt wait
    and the revalidation would still have satisfied it. What matters is adjacency — the
    first thing after waiting is proving the endpoint is still the one the manifest names.
    """
    assert reads, "the publication must read the chain at all"
    for index, step in enumerate(reads):
        if step != "revalidate":
            assert "revalidate" in reads[:index], (
                f"{step} ran before any identity check"
            )
    assert "wait_for_receipt" in reads, "this path must have waited for a receipt"
    after_wait = reads[reads.index("wait_for_receipt") + 1 :]
    assert after_wait[:2] == ["revalidate", "receipt_state"], (
        "after waiting, the endpoint must be proved again and the receipt read again — "
        f"revalidating and then judging the wait's own receipt proves nothing; got "
        f"{after_wait[:2]}"
    )


def test_endpoint_identity_is_reproved_before_every_decisive_read(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()

    _client(tmp_path, backend).publish(
        _signed_report(1), report_uri="urn:touchstone:report:1"
    )

    assert backend.revalidations >= 2
    _assert_revalidation_order(backend.reads)


def test_recovery_also_reproves_identity_after_its_wait(tmp_path: Path) -> None:
    """The reconciliation path needs the same ordering, and had no coverage at all."""
    backend = FakeBackend()
    backend.drop_first_broadcast = True
    with pytest.raises(PendingSubmission):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    backend.reads.clear()

    _client(tmp_path, backend).publish(
        _signed_report(1), report_uri="urn:touchstone:report:1"
    )

    _assert_revalidation_order(backend.reads)


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


def test_a_rotated_publisher_can_still_reconcile_what_the_old_one_published(
    tmp_path: Path,
) -> None:
    """Pinning reconciliation to the current address broke every rotation.

    A publication confirms, the process dies before it is recorded, the owner rotates the
    publisher, and the restart then refused its own settled publication forever — while
    the journal it could not clear blocked everything after it. The registry carries the
    lineage across a rotation precisely so this case works.
    """
    outgoing = FakeBackend()
    outgoing.time_out_once = True
    with pytest.raises(PendingSubmission):
        _client(tmp_path, outgoing).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert len(outgoing.submissions) == 1, "the publication did settle"

    # The owner rotates: a new address, the same lineage.
    successor = address(STRANGER_SECRET)
    rotated = FakeBackend(
        _manifest(
            publisher_address=successor, publisher_identity_address=REGISTRY_LINEAGE
        ),
        secret=STRANGER_SECRET,
    )
    rotated.reports = outgoing.reports
    rotated.receipts = outgoing.receipts
    rotated.intents = outgoing.intents
    rotated.submissions = outgoing.submissions
    rotated.lineage = {
        address(PUBLISHER_SECRET): REGISTRY_LINEAGE,
        successor: REGISTRY_LINEAGE,
    }

    result = PublisherClient(
        rotated,
        TransparencyLog(tmp_path / "transparency.jsonl"),
        tmp_path / "pending.json",
    ).publish(_signed_report(1), report_uri="urn:touchstone:report:1")

    assert result.reconciled is True
    assert len(rotated.submissions) == 1, "nothing was published a second time"
    assert not (tmp_path / "pending.json").exists()


def test_a_rotated_out_signer_cannot_still_send(tmp_path: Path) -> None:
    """Reconciling what an old key already published is fine. Sending for it is not."""
    stranded = FakeBackend()
    stranded.drop_first_broadcast = True
    with pytest.raises(PendingSubmission):
        _client(tmp_path, stranded).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert stranded.submissions == []

    successor = address(STRANGER_SECRET)
    rotated = FakeBackend(
        _manifest(
            publisher_address=successor, publisher_identity_address=REGISTRY_LINEAGE
        ),
        secret=STRANGER_SECRET,
    )
    rotated.lineage = {
        address(PUBLISHER_SECRET): REGISTRY_LINEAGE,
        successor: REGISTRY_LINEAGE,
    }

    with pytest.raises(
        PendingSubmission, match="no longer this deployment's publisher"
    ):
        PublisherClient(
            rotated,
            TransparencyLog(tmp_path / "transparency.jsonl"),
            tmp_path / "pending.json",
        ).publish(_signed_report(1), report_uri="urn:touchstone:report:1")
    assert rotated.broadcasts == []


def test_a_failed_receipt_is_not_believed_before_identity_is_reproved(
    tmp_path: Path,
) -> None:
    """Declaring failure destroys the journal, so it is a decision like any other.

    The ordering rule was written for the success path only: a status-0 receipt was read,
    believed, and the journal discarded, all before the endpoint was rechecked. A
    repointed endpoint could therefore both invent the failure and erase the record of
    what had actually been sent.
    """
    backend = FakeBackend()
    backend.failing_receipt = True

    with pytest.raises(SubmissionFailed, match="failed"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )

    after_wait = backend.reads[backend.reads.index("wait_for_receipt") + 1 :]
    assert after_wait and after_wait[0] == "revalidate", (
        "identity must be reproved before a receipt is allowed to mean failure"
    )


def test_revalidation_actually_verifies_rather_than_only_dropping_a_cache() -> None:
    """A revalidate that merely invalidates proves nothing on its own.

    Where the next step is a client-side decision rather than a chain read — reading a
    failed receipt, for instance — nothing would ever have re-verified the endpoint.

    Spied on behaviourally rather than by reading the source: asserting that the method
    body contains the text "self.preflight()" pins a spelling, and would pass just as
    happily if that call were unreachable.
    """
    from touchstone.publish import SignedRegistryBackend

    backend = SignedRegistryBackend.__new__(SignedRegistryBackend)
    stale = object()
    backend._preflight = stale
    seen_when_called = []

    def preflight():
        # What the cache held at the moment preflight ran. If revalidate returned the
        # cached value, or ran preflight without clearing first, this shows it.
        seen_when_called.append(backend._preflight)
        backend._preflight = "fresh"
        return "fresh"

    backend.preflight = preflight

    result = backend.revalidate()

    assert seen_when_called == [None], (
        "the cache must be dropped before preflight runs, not merely afterwards"
    )
    assert result == "fresh"
    assert backend._preflight == "fresh"


def test_a_reread_that_disagrees_with_the_wait_keeps_the_journal(
    tmp_path: Path,
) -> None:
    """Only the reading taken after revalidation may decide anything.

    The wait's receipt came from the endpoint as it was before the wait began. If the
    re-verified endpoint no longer has the transaction, that is not a settled publication,
    and the journal must survive so the next run can resolve it.
    """
    backend = FakeBackend()
    backend.state_after_wait = MISSING

    with pytest.raises(PendingSubmission, match="re-verified endpoint"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )

    assert (tmp_path / "pending.json").exists(), "the journal must be kept"
    prepared_once = backend.prepared

    # The next run resolves the same transaction; it does not sign a second one.
    backend.state_after_wait = None
    result = _client(tmp_path, backend).publish(
        _signed_report(1), report_uri="urn:touchstone:report:1"
    )

    assert result.reconciled is True
    assert backend.prepared == prepared_once, (
        "recovery must not prepare a new transaction"
    )
    assert len(set(backend.broadcasts)) == 1, "and must reuse the same signed bytes"
    assert len(backend.submissions) == 1


def test_a_reread_that_is_merely_included_also_keeps_the_journal(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    backend.state_after_wait = INCLUDED

    with pytest.raises(PendingSubmission, match="re-verified endpoint"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )
    assert (tmp_path / "pending.json").exists()
    broadcasts_before = len(backend.broadcasts)

    backend.state_after_wait = None
    result = _client(tmp_path, backend).publish(
        _signed_report(1), report_uri="urn:touchstone:report:1"
    )

    assert result.reconciled is True
    assert len(backend.broadcasts) == broadcasts_before, (
        "an included transaction that later confirms needs no rebroadcast"
    )


def test_only_the_reread_decides_success_or_failure(tmp_path: Path) -> None:
    """The defect this proves absent: a stale status-0 clearing a live publication.

    The wait sees a failure; the re-verified endpoint reports success. If the wait's
    receipt still controlled, this would raise and discard the journal for a publication
    that had in fact settled.
    """
    backend = FakeBackend()
    backend.failing_receipt = True  # what the wait sees
    backend.status_after_wait = 1  # what the re-verified endpoint reports

    result = _client(tmp_path, backend).publish(
        _signed_report(1), report_uri="urn:touchstone:report:1"
    )

    assert result.receipt["status"] == 1
    assert not (tmp_path / "pending.json").exists()


def test_the_published_report_is_the_one_that_was_verified(tmp_path: Path) -> None:
    """Verification and submission must see the same bytes.

    The caller keeps a reference to whatever it passed, and every backend call in between
    is a chance for that object to change — so a signature checked against one report
    could be published over another, leaving a transparency-log entry whose own signature
    no longer verifies.
    """
    backend = FakeBackend()
    signed = _signed_report(1)

    def mutate_during_the_publication() -> None:
        backend.revalidations += 1
        signed["report"]["evidence_root"] = "44" * 32

    backend.revalidate = mutate_during_the_publication

    _client(tmp_path, backend).publish(signed, report_uri="urn:touchstone:report:1")

    logged = TransparencyLog(tmp_path / "transparency.jsonl").verify()[0]
    assert logged["signed_report"]["report"]["evidence_root"] == "33" * 32, (
        "the report that was verified is the one that was published"
    )
    # And the entry still verifies against its own signature.
    from touchstone.signing import verify_signed_report

    verify_signed_report(
        logged["signed_report"], {REPORTER.kid: REPORTER.public_key_record()}
    )


class _ShiftingReceipt(Mapping):
    """A backend receipt whose status changes between reads, as a live node's would."""

    def __init__(self, receipt: Mapping[str, object]) -> None:
        self._receipt = dict(receipt)
        self.status_reads = 0

    def __getitem__(self, key: str) -> object:
        if key != "status":
            return self._receipt[key]
        self.status_reads += 1
        # Settled on the first read, failed on every one after it.
        return 1 if self.status_reads == 1 else 0

    def __iter__(self):
        return iter(self._receipt)

    def __len__(self) -> int:
        return len(self._receipt)


def _shifting_receipts(backend: FakeBackend) -> dict[str, object]:
    """Make every receipt this backend hands out change on its second status read."""
    shifting: dict[str, object] = {"receipt": None}
    real_receipt_state = backend.receipt_state

    def shifting_receipt(transaction_hash):
        state, receipt = real_receipt_state(transaction_hash)
        if receipt is not None and shifting["receipt"] is None:
            shifting["receipt"] = _ShiftingReceipt(receipt)
        return state, shifting["receipt"] if receipt is not None else None

    backend.receipt_state = shifting_receipt
    return shifting


def test_the_receipt_that_settles_a_dropped_republication_is_the_one_recorded(
    tmp_path: Path,
) -> None:
    """The rebroadcast branch settles through its own path and must derive one record too.

    Covering only a clean first publication left this branch and the already-onchain one
    free to regress independently, which is exactly how the defect survived being fixed
    in one place.
    """
    backend = FakeBackend()
    backend.drop_first_broadcast = True
    with pytest.raises(PendingSubmission, match="remains pending"):
        _client(tmp_path, backend).publish(
            _signed_report(1), report_uri="urn:touchstone:report:1"
        )

    client = _client(tmp_path, backend)
    shifting = _shifting_receipts(backend)
    result = client.publish(_signed_report(1), report_uri="urn:touchstone:report:1")

    assert result.reconciled is True
    assert shifting["receipt"].status_reads == 1, "the status was read exactly once"
    assert client.transparency_log.verify()[-1]["publication"]["receipt"]["status"] == 1


def test_the_receipt_that_settles_a_publication_is_the_receipt_recorded(
    tmp_path: Path,
) -> None:
    """The status that decided and the status written down were two separate reads.

    The publisher judged the transaction settled from one reading and then built the
    transparency-log entry from another, so a receipt that changed in between produced a
    durable record contradicting the decision that produced it — a log saying the
    transaction failed, written by the branch that concluded it had succeeded.
    """
    backend = FakeBackend()
    client = _client(tmp_path, backend)
    shifting = {"receipt": None}
    real_receipt_state = backend.receipt_state

    def shifting_receipt(transaction_hash):
        state, receipt = real_receipt_state(transaction_hash)
        if receipt is not None and shifting["receipt"] is None:
            shifting["receipt"] = _ShiftingReceipt(receipt)
        return state, shifting["receipt"] if receipt is not None else None

    backend.receipt_state = shifting_receipt

    result = client.publish(_signed_report(1), report_uri="urn:touchstone:report:1")

    assert shifting["receipt"].status_reads == 1, "the status was read exactly once"
    assert result.receipt["status"] == 1
    entry = client.transparency_log.verify()[-1]
    assert entry["publication"]["receipt"]["status"] == 1, (
        "and the log carries the reading that settled it"
    )


def test_a_confirmed_orphan_is_recorded_without_another_send(tmp_path: Path) -> None:
    backend = FakeBackend()
    signed = _signed_report(1)
    uri = "urn:touchstone:report:1"
    first = _client(tmp_path, backend).publish(signed, report_uri=uri)
    (tmp_path / "transparency.jsonl").unlink()

    recovered = _client(tmp_path, backend).recover_confirmed(
        signed,
        report_uri=uri,
        transaction_hash=first.transaction_hash,
    )

    assert recovered.reconciled
    assert recovered.transaction_hash == first.transaction_hash
    assert len(backend.submissions) == 1, "recovery must not send another transaction"
    assert len(backend.broadcasts) == 1, "recovery must only read the existing receipt"
    assert len(TransparencyLog(tmp_path / "transparency.jsonl").verify()) == 1


def test_a_journal_that_cannot_be_written_is_a_pending_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal is written before the broadcast, so failing it means nothing was sent.

    Saying so in this module's terms is what lets the caller treat it as the pre-broadcast
    failure it is, rather than as whatever the filesystem happened to raise.
    """
    client = PublisherClient(
        FakeBackend(),
        TransparencyLog(tmp_path / "transparency.jsonl"),
        tmp_path / "pending.json",
    )

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "open", refuse)

    with pytest.raises(PendingSubmission, match="cannot be written"):
        client.publish(_signed_report(1), report_uri="urn:touchstone:report:1")
