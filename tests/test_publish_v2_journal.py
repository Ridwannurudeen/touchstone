"""Crash-safe Registry v2 publication and command-line recovery behavior."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import sys

import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import TimeExhausted

from touchstone.deployment import RegistryV2DeploymentManifest
from touchstone.keyring import decoded_transaction
from touchstone.publish_v2 import (
    CONFIRMED,
    INCLUDED,
    MISSING,
    PreparedRegistryV2Transaction,
    RegistryV2Preflight,
    RegistryV2PreflightFailed,
    RegistryV2PublicationError,
    RegistryV2ReconciliationFailed,
    RelayerKey,
)
from touchstone.publish_v2_journal import (
    RegistryV2PendingSubmission,
    RegistryV2PublicationResult,
    RegistryV2PublisherClient,
    RegistryV2SubmissionFailed,
)
from touchstone.registry_v2 import attestation_from_report, sign_attestation
from touchstone.signing import Ed25519Signer, canonical_json_bytes
import touchstone.publish_v2_journal as journal_module

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import publish_v2_epoch  # noqa: E402


PUBLISHER_SECRET = "11" * 32
RELAYER_SECRET = "22" * 32
PUBLISHER = Web3.to_checksum_address(Account.from_key(PUBLISHER_SECRET).address)
RELAYER = Web3.to_checksum_address(Account.from_key(RELAYER_SECRET).address)
OWNER = Web3.to_checksum_address("0x" + "33" * 20)
OPERATIONS = Web3.to_checksum_address("0x" + "44" * 20)
LEGACY = Web3.to_checksum_address("0x" + "55" * 20)
REGISTRY = Web3.to_checksum_address("0x" + "66" * 20)
OTHER_REGISTRY = Web3.to_checksum_address("0x" + "99" * 20)
ROTATED_PUBLISHER = Web3.to_checksum_address("0x" + "77" * 20)
CALLDATA = bytes.fromhex("01234567" + "ab" * 32)
RECEIPT = {
    "blockHash": bytes.fromhex("ab" * 32),
    "blockNumber": 17,
    "status": 1,
    "transactionIndex": 0,
}


def _manifest(
    reporter: Ed25519Signer, *, chain_id: int = 31337
) -> RegistryV2DeploymentManifest:
    key = reporter.public_key_record()
    local = chain_id == 31337
    return RegistryV2DeploymentManifest.from_mapping(
        {
            "manifest_version": 2,
            "registry_version": 2,
            "network": "hardhat-local" if local else "xlayer-mainnet",
            "chain_id": chain_id,
            "rpc_url": (
                "http://127.0.0.1:8545" if local else "https://rpc.xlayer.example"
            ),
            "registry_address": REGISTRY,
            "registry_runtime_bytecode_sha256": hashlib.sha256(
                b"registry-v2-runtime"
            ).hexdigest(),
            "legacy_registry_address": LEGACY,
            "legacy_registry_runtime_bytecode_sha256": hashlib.sha256(
                b"legacy-runtime"
            ).hexdigest(),
            "owner_address": OWNER,
            "relayer_address": RELAYER,
            "publisher_address": PUBLISHER,
            "publisher_identity_address": PUBLISHER,
            "deployer_address": OWNER,
            "operations_address": OPERATIONS,
            "confirmations": 2,
            "max_fee_wei": 10**15,
            "deployment_block": 1,
            "deployment_state": "active",
            "deployment_transaction": "0x" + "aa" * 32,
            "authorization_transaction": "0x" + "bb" * 32,
            "reporting_keys": [
                {
                    "kid": key["kid"],
                    "public_key": key["public_key"],
                    "state": "active",
                }
            ],
        }
    )


def _manifest_after_reporting_key_rollover(
    manifest: RegistryV2DeploymentManifest,
    successor: Ed25519Signer,
    *,
    retired_state: str,
) -> RegistryV2DeploymentManifest:
    value = manifest.to_mapping()
    retired = value["reporting_keys"][0]
    successor_key = successor.public_key_record()
    value["reporting_keys"] = [
        {
            **retired,
            "state": retired_state,
            "not_after": "2026-08-19T10:30:00Z",
        },
        {
            "kid": successor_key["kid"],
            "public_key": successor_key["public_key"],
            "state": "active",
        },
    ]
    return RegistryV2DeploymentManifest.from_mapping(value)


@pytest.fixture
def reporter() -> Ed25519Signer:
    return Ed25519Signer.from_seed(b"\x77" * 32)


@pytest.fixture
def manifest(reporter: Ed25519Signer) -> RegistryV2DeploymentManifest:
    return _manifest(reporter)


@pytest.fixture
def signed_report(reporter: Ed25519Signer) -> dict[str, object]:
    return reporter.sign_report(
        {
            "asset_key": "eip155:1:0x" + "ab" * 20 + "#policy:nav-settlement:2",
            "approval_ledger_sha256": "56" * 32,
            "control_set_root": "66" * 32,
            "epoch_id": "2026-08-19",
            "evidence_root": "77" * 32,
            "observed_at": "2026-08-19T10:00:00Z",
            "policy": {
                "control_ids": ["nav"],
                "policy_digest": "55" * 32,
                "policy_id": "nav-settlement",
                "policy_version": 2,
            },
            "sequence": 1,
            "state": "UNVERIFIABLE",
            "valid_until": "2026-08-20T10:00:00Z",
        }
    )


@pytest.fixture
def prepared(
    manifest: RegistryV2DeploymentManifest,
    signed_report: dict[str, object],
) -> PreparedRegistryV2Transaction:
    report = signed_report["report"]
    assert isinstance(report, dict)
    unsigned = attestation_from_report(
        report,
        publisher=PUBLISHER,
        parent_digest="0" * 64,
        correction_of=0,
        report_uri="ipfs://reports/ustb-2026-08-19.json",
        chain_id=manifest.chain_id,
        verifying_contract=manifest.registry_address,
    )
    attestation = sign_attestation(bytes.fromhex(PUBLISHER_SECRET), **unsigned)
    relayer = RelayerKey.from_hex(RELAYER_SECRET)
    transaction_hash, raw = relayer.sign_transaction(
        {
            "chainId": manifest.chain_id,
            "nonce": 7,
            "to": manifest.registry_address,
            "value": 0,
            "data": CALLDATA,
            "gas": 125_000,
            "gasPrice": 20,
        }
    )
    return PreparedRegistryV2Transaction(
        transaction_hash=transaction_hash,
        raw=raw,
        nonce=7,
        gas=125_000,
        maximum_fee_wei=2_500_000,
        report_input=(),
        attestation=attestation,
        correction_of=0,
    )


class FakeBackend:
    def __init__(
        self,
        manifest: RegistryV2DeploymentManifest,
        prepared: PreparedRegistryV2Transaction,
    ) -> None:
        self.manifest = manifest
        self.relayer_key = RelayerKey.from_hex(RELAYER_SECRET)
        self.prepared = prepared
        self.receipt = dict(RECEIPT)
        self.states: list[tuple[str, dict[str, object] | None]] = [
            (MISSING, None),
            (CONFIRMED, self.receipt),
        ]
        self.wait_error: Exception | None = None
        self.receipt_state_error: Exception | None = None
        self.reconcile_error: Exception | None = None
        self.before_broadcast = None
        self.prepare_calls: list[tuple[dict[str, object], str, int]] = []
        self.broadcasts: list[PreparedRegistryV2Transaction] = []
        self.waits: list[tuple[str, float]] = []
        self.reconciliations: list[dict[str, object]] = []
        self.calldata_calls = 0
        self.revalidations = 0

    def revalidate(self) -> object:
        self.revalidations += 1
        return object()

    def prepare(
        self,
        signed_report: dict[str, object],
        *,
        report_uri: str,
        correction_of: int = 0,
    ) -> PreparedRegistryV2Transaction:
        self.prepare_calls.append((signed_report, report_uri, correction_of))
        return self.prepared

    def calldata(self, *args: object, **kwargs: object) -> bytes:
        del args, kwargs
        self.calldata_calls += 1
        return CALLDATA

    def broadcast(
        self, prepared: PreparedRegistryV2Transaction
    ) -> str:
        if self.before_broadcast is not None:
            self.before_broadcast()
        self.broadcasts.append(prepared)
        return prepared.transaction_hash

    def receipt_state(
        self, transaction_hash: str
    ) -> tuple[str, dict[str, object] | None]:
        assert transaction_hash == self.prepared.transaction_hash
        if self.receipt_state_error is not None:
            raise self.receipt_state_error
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def wait_for_receipt(
        self, transaction_hash: str, timeout: float
    ) -> dict[str, object]:
        assert transaction_hash == self.prepared.transaction_hash
        self.waits.append((transaction_hash, timeout))
        if self.wait_error is not None:
            raise self.wait_error
        return self.receipt

    def reconcile(self, attestation: object) -> object:
        assert isinstance(attestation, dict)
        self.reconciliations.append(attestation)
        if self.reconcile_error is not None:
            raise self.reconcile_error
        return SimpleNamespace(report_digest=attestation["report_digest"])


@pytest.fixture
def backend(
    manifest: RegistryV2DeploymentManifest,
    prepared: PreparedRegistryV2Transaction,
) -> FakeBackend:
    return FakeBackend(manifest, prepared)


@pytest.fixture
def pending_path(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / "pending-v2.json"


def _client(backend: FakeBackend, pending_path: Path) -> RegistryV2PublisherClient:
    return RegistryV2PublisherClient(backend, pending_path, receipt_timeout=4.5)


def _leave_pending(
    client: RegistryV2PublisherClient,
    backend: FakeBackend,
    signed_report: dict[str, object],
) -> dict[str, object]:
    backend.wait_error = TimeExhausted("still pending")
    with pytest.raises(RegistryV2PendingSubmission, match="pending|confirm"):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )
    backend.wait_error = None
    return json.loads(client.pending_path.read_text(encoding="utf-8"))


def test_journal_is_fsynced_and_atomically_installed_before_broadcast(
    backend: FakeBackend,
    pending_path: Path,
    prepared: PreparedRegistryV2Transaction,
    signed_report: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    real_fsync = journal_module.os.fsync
    real_replace = journal_module.os.replace

    def fsync(file_descriptor: int) -> None:
        events.append(
            "directory_fsync"
            if stat.S_ISDIR(os.fstat(file_descriptor).st_mode)
            else "file_fsync"
        )
        real_fsync(file_descriptor)

    def replace(source: str | Path, destination: str | Path) -> None:
        assert Path(source).exists()
        assert Path(destination) == pending_path
        assert "file_fsync" in events
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(journal_module.os, "fsync", fsync)
    monkeypatch.setattr(journal_module.os, "replace", replace)

    def before_broadcast() -> None:
        assert pending_path.exists()
        assert events[-1] == (
            "replace" if os.name == "nt" else "directory_fsync"
        )
        events.append("broadcast")

    backend.before_broadcast = before_broadcast
    pending = _leave_pending(_client(backend, pending_path), backend, signed_report)

    expected_events = ["file_fsync", "replace"]
    if os.name != "nt":
        expected_events.append("directory_fsync")
    expected_events.append("broadcast")
    assert events == expected_events
    assert pending["transaction_hash"] == prepared.transaction_hash
    assert pending["raw_transaction"] == prepared.raw.hex()
    assert pending["nonce"] == prepared.nonce
    assert pending["gas"] == prepared.gas
    assert pending["maximum_fee_wei"] == prepared.maximum_fee_wei
    assert pending["attestation"] == prepared.attestation
    assert pending["report_sha256"] == hashlib.sha256(
        canonical_json_bytes(signed_report)
    ).hexdigest()
    assert pending["report_uri"] == "ipfs://reports/ustb-2026-08-19.json"
    assert pending["correction_of"] == 0
    assert pending["journal_version"] == 1
    assert pending["asset_key"] == prepared.attestation["asset_key"]
    assert pending["sequence"] == prepared.attestation["sequence"]
    assert pending["chain_id"] == backend.manifest.chain_id
    assert pending["registry_address"] == backend.manifest.registry_address
    assert pending["relayer_address"] == RELAYER
    assert pending["attestation"]["publisher"] == backend.manifest.publisher_address


def test_journal_write_failure_prevents_broadcast(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(source: str | Path, destination: str | Path) -> None:
        del source, destination
        raise OSError("disk is read-only")

    monkeypatch.setattr(journal_module.os, "replace", fail_replace)

    with pytest.raises(RegistryV2PendingSubmission, match="journal.*written|read-only"):
        _client(backend, pending_path).publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert backend.broadcasts == []
    assert not pending_path.exists()


def test_success_requires_confirmed_status_one_and_exact_reconciliation(
    backend: FakeBackend,
    pending_path: Path,
    prepared: PreparedRegistryV2Transaction,
    signed_report: dict[str, object],
) -> None:
    result = _client(backend, pending_path).publish(
        signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
    )

    assert isinstance(result, RegistryV2PublicationResult)
    assert result.transaction_hash == prepared.transaction_hash
    assert result.receipt["status"] == 1
    assert result.reconciled is False
    assert backend.waits == [(prepared.transaction_hash, 4.5)]
    assert backend.reconciliations == [dict(prepared.attestation)]
    assert not pending_path.exists()


def test_failed_receipt_keeps_the_only_recovery_record(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    backend.receipt["status"] = 0
    backend.states = [(CONFIRMED, backend.receipt)]

    with pytest.raises(RegistryV2SubmissionFailed, match="succeed|failed|status"):
        _client(backend, pending_path).publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert pending_path.exists()
    assert backend.reconciliations == []


def test_included_receipt_is_not_treated_as_confirmed(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    backend.states = [(INCLUDED, backend.receipt)]

    with pytest.raises(RegistryV2PendingSubmission, match="included|confirm"):
        _client(backend, pending_path).publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert pending_path.exists()
    assert backend.reconciliations == []


def test_missing_transaction_rebroadcasts_the_exact_journalled_bytes_once(
    backend: FakeBackend,
    pending_path: Path,
    prepared: PreparedRegistryV2Transaction,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    backend.states = [(MISSING, None), (CONFIRMED, backend.receipt)]

    result = client.publish(
        signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
    )

    assert result.reconciled is True
    assert len(backend.broadcasts) == 2
    assert backend.broadcasts[0].raw == prepared.raw
    assert backend.broadcasts[1].raw == prepared.raw
    assert backend.broadcasts[1].transaction_hash == prepared.transaction_hash
    assert len(backend.prepare_calls) == 1
    assert not pending_path.exists()


def test_included_transaction_waits_without_rebroadcasting(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    backend.states = [
        (INCLUDED, backend.receipt),
        (INCLUDED, backend.receipt),
        (CONFIRMED, backend.receipt),
    ]
    broadcasts_before_recovery = len(backend.broadcasts)

    result = client.publish(
        signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
    )

    assert result.reconciled is True
    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert backend.waits[-1] == (backend.prepared.transaction_hash, 4.5)
    assert not pending_path.exists()


def test_confirmed_transaction_reconciles_without_waiting_or_rebroadcasting(
    backend: FakeBackend,
    pending_path: Path,
    prepared: PreparedRegistryV2Transaction,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    backend.states = [(CONFIRMED, backend.receipt)]
    broadcasts_before_recovery = len(backend.broadcasts)
    waits_before_recovery = len(backend.waits)

    result = client.publish(
        signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
    )

    assert result.transaction_hash == prepared.transaction_hash
    assert result.reconciled is True
    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert len(backend.waits) == waits_before_recovery
    assert backend.reconciliations == [dict(prepared.attestation)]
    assert not pending_path.exists()


def test_confirmed_transaction_reconciles_after_publisher_rotation(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    rotated = backend.manifest.to_mapping()
    rotated["publisher_address"] = ROTATED_PUBLISHER
    backend.manifest = RegistryV2DeploymentManifest.from_mapping(rotated)
    backend.states = [(CONFIRMED, backend.receipt)]
    broadcasts_before_recovery = len(backend.broadcasts)

    result = client.publish(
        signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
    )

    assert result.reconciled is True
    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert not pending_path.exists()


def test_missing_transaction_is_not_rebroadcast_after_publisher_rotation(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    rotated = backend.manifest.to_mapping()
    rotated["publisher_address"] = ROTATED_PUBLISHER
    backend.manifest = RegistryV2DeploymentManifest.from_mapping(rotated)
    backend.states = [(MISSING, None)]
    broadcasts_before_recovery = len(backend.broadcasts)

    with pytest.raises(RegistryV2PendingSubmission, match="rotation"):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert pending_path.exists()


def test_confirmed_pending_report_still_reconciles_after_reporting_key_rollover(
    backend: FakeBackend,
    pending_path: Path,
    prepared: PreparedRegistryV2Transaction,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    successor = Ed25519Signer.from_seed(b"\x88" * 32)
    backend.manifest = _manifest_after_reporting_key_rollover(
        backend.manifest, successor, retired_state="superseded"
    )
    backend.states = [(CONFIRMED, backend.receipt)]
    broadcasts_before_recovery = len(backend.broadcasts)

    result = client.publish(
        signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
    )

    retired_kid = signed_report["kid"]
    assert isinstance(retired_kid, str)
    assert backend.manifest.key(retired_kid).state == "superseded"
    assert result.reconciled is True
    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert backend.reconciliations == [dict(prepared.attestation)]
    assert not pending_path.exists()


def test_revoked_reporting_key_refuses_recovery_and_keeps_pending_journal(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    _leave_pending(client, backend, signed_report)
    journal = pending_path.read_bytes()
    successor = Ed25519Signer.from_seed(b"\x88" * 32)
    backend.manifest = _manifest_after_reporting_key_rollover(
        backend.manifest, successor, retired_state="revoked"
    )
    backend.states = [(CONFIRMED, backend.receipt)]
    broadcasts_before_recovery = len(backend.broadcasts)

    with pytest.raises(
        RegistryV2PendingSubmission, match="verification|verifiable|reporting key"
    ):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    retired_kid = signed_report["kid"]
    assert isinstance(retired_kid, str)
    assert backend.manifest.key(retired_kid).state == "revoked"
    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert backend.reconciliations == []
    assert pending_path.read_bytes() == journal


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [
        ("chain_id", 1),
        ("registry_address", OTHER_REGISTRY),
        ("relayer_address", PUBLISHER),
        ("transaction_hash", "0x" + "00" * 32),
        ("report_sha256", "00" * 32),
    ],
)
def test_foreign_or_inconsistently_bound_journal_is_refused_without_broadcast(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
    field: str,
    foreign_value: object,
) -> None:
    client = _client(backend, pending_path)
    pending = _leave_pending(client, backend, signed_report)
    pending[field] = foreign_value
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    broadcasts_before_recovery = len(backend.broadcasts)

    with pytest.raises(RegistryV2PendingSubmission):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert pending_path.exists()


def test_journal_refuses_raw_bytes_that_do_not_hash_to_the_recorded_transaction(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    pending = _leave_pending(client, backend, signed_report)
    pending["raw_transaction"] = "01" + str(pending["raw_transaction"])[2:]
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    broadcasts_before_recovery = len(backend.broadcasts)

    with pytest.raises(RegistryV2PendingSubmission, match="bytes|hash|transaction"):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert len(backend.broadcasts) == broadcasts_before_recovery
    assert pending_path.exists()


def test_journal_refuses_attestation_tampering_even_when_transaction_is_confirmed(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    pending = _leave_pending(client, backend, signed_report)
    attestation = dict(pending["attestation"])
    attestation["report_uri"] = "ipfs://reports/foreign.json"
    pending["attestation"] = attestation
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    backend.states = [(CONFIRMED, backend.receipt)]

    with pytest.raises(RegistryV2PendingSubmission, match="attestation|calldata|report"):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert backend.reconciliations == []
    assert pending_path.exists()


@pytest.mark.parametrize("contents", [b"[]", b"{not-json", b"null"])
def test_malformed_journal_is_refused_and_preserved(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
    contents: bytes,
) -> None:
    pending_path.parent.mkdir(parents=True)
    pending_path.write_bytes(contents)

    with pytest.raises(RegistryV2PendingSubmission, match="pending|journal"):
        _client(backend, pending_path).publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert pending_path.read_bytes() == contents
    assert backend.broadcasts == []


def test_indeterminate_receipt_error_keeps_journal_for_retry(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    original = _leave_pending(client, backend, signed_report)
    backend.receipt_state_error = OSError("RPC disconnected")

    with pytest.raises(OSError, match="RPC disconnected"):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert json.loads(pending_path.read_text(encoding="utf-8")) == original


def test_reconciliation_mismatch_never_clears_confirmed_journal(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    original = _leave_pending(client, backend, signed_report)
    backend.states = [(CONFIRMED, backend.receipt)]
    backend.reconcile_error = RegistryV2ReconciliationFailed(
        "onchain report does not match the signed attestation"
    )

    with pytest.raises(RegistryV2ReconciliationFailed, match="does not match"):
        client.publish(
            signed_report, report_uri="ipfs://reports/ustb-2026-08-19.json"
        )

    assert backend.reconciliations == [dict(backend.prepared.attestation)]
    assert json.loads(pending_path.read_text(encoding="utf-8")) == original


def test_pending_transaction_reports_absent_and_valid_state(
    backend: FakeBackend,
    pending_path: Path,
    signed_report: dict[str, object],
) -> None:
    client = _client(backend, pending_path)
    assert client.pending_transaction() is None
    _leave_pending(client, backend, signed_report)
    assert client.pending_transaction() == backend.prepared.transaction_hash


def test_publish_correction_binds_the_signed_target_in_the_journal(
    reporter: Ed25519Signer,
    manifest: RegistryV2DeploymentManifest,
    pending_path: Path,
) -> None:
    report = {
        "asset_key": "eip155:1:0x" + "ab" * 20 + "#policy:nav-settlement:2",
        "approval_ledger_sha256": "56" * 32,
        "control_set_root": "66" * 32,
        "correction_of": 1,
        "epoch_id": "2026-08-19",
        "evidence_root": "88" * 32,
        "observed_at": "2026-08-19T11:00:00Z",
        "policy": {
            "control_ids": ["nav"],
            "policy_digest": "55" * 32,
            "policy_id": "nav-settlement",
            "policy_version": 2,
        },
        "sequence": 2,
        "state": "CONFIRMED",
        "valid_until": "2026-08-20T11:00:00Z",
    }
    signed = reporter.sign_report(report)
    unsigned = attestation_from_report(
        report,
        publisher=PUBLISHER,
        parent_digest="99" * 32,
        correction_of=1,
        report_uri="ipfs://reports/ustb-correction.json",
        chain_id=manifest.chain_id,
        verifying_contract=manifest.registry_address,
    )
    attestation = sign_attestation(bytes.fromhex(PUBLISHER_SECRET), **unsigned)
    relayer = RelayerKey.from_hex(RELAYER_SECRET)
    transaction_hash, raw = relayer.sign_transaction(
        {
            "chainId": manifest.chain_id,
            "nonce": 8,
            "to": manifest.registry_address,
            "value": 0,
            "data": CALLDATA,
            "gas": 125_000,
            "gasPrice": 20,
        }
    )
    prepared = PreparedRegistryV2Transaction(
        transaction_hash=transaction_hash,
        raw=raw,
        nonce=8,
        gas=125_000,
        maximum_fee_wei=2_500_000,
        report_input=(),
        attestation=attestation,
        correction_of=1,
    )
    backend = FakeBackend(manifest, prepared)
    client = _client(backend, pending_path)

    pending = _leave_correction_pending(client, backend, signed)

    assert backend.prepare_calls == [
        (signed, "ipfs://reports/ustb-correction.json", 1)
    ]
    assert pending["correction_of"] == 1
    assert pending["attestation"] == attestation


def _leave_correction_pending(
    client: RegistryV2PublisherClient,
    backend: FakeBackend,
    signed_report: dict[str, object],
) -> dict[str, object]:
    backend.wait_error = TimeExhausted("still pending")
    with pytest.raises(RegistryV2PendingSubmission):
        client.publish_correction(
            signed_report, report_uri="ipfs://reports/ustb-correction.json"
        )
    return json.loads(client.pending_path.read_text(encoding="utf-8"))


def _write_manifest(path: Path, manifest: RegistryV2DeploymentManifest) -> Path:
    path.write_text(json.dumps(manifest.to_mapping()), encoding="utf-8")
    return path


class FakeCLIBackend:
    instances: list["FakeCLIBackend"] = []

    def __init__(self, manifest: object, key: object, **kwargs: object) -> None:
        self.manifest = manifest
        self.key = key
        self.quorum = kwargs.get("quorum")
        if not self.manifest.is_local and self.quorum is None:
            raise RegistryV2PreflightFailed(
                "public v2 publication requires independent RPC quorum"
            )
        self.__class__.instances.append(self)

    def preflight(self) -> RegistryV2Preflight:
        return RegistryV2Preflight(
            chain_id=self.manifest.chain_id,
            block_number=27,
            registry_address=self.manifest.registry_address,
            registry_runtime_bytecode_sha256=(
                self.manifest.registry_runtime_bytecode_sha256
            ),
            legacy_registry_address=self.manifest.legacy_registry_address,
            legacy_runtime_bytecode_sha256=(
                self.manifest.legacy_registry_runtime_bytecode_sha256
            ),
            owner_address=self.manifest.owner_address,
            publisher_address=self.manifest.publisher_address,
            publisher_identity_address=self.manifest.publisher_identity_address,
            relayer_address=RELAYER,
            relayer_balance_wei=10**18,
        )


def _cli_arguments(
    manifest: Path,
    workspace: Path,
    *,
    preflight: bool,
    signed_report: Path | None = None,
    correction: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        manifest=str(manifest),
        workspace=str(workspace),
        preflight=preflight,
        signed_report=None if signed_report is None else str(signed_report),
        report_uri=None if signed_report is None else "ipfs://reports/ustb.json",
        correction=correction,
    )


def _patch_cli_boundaries(
    monkeypatch: pytest.MonkeyPatch, quorum: object | None
) -> None:
    FakeCLIBackend.instances.clear()
    monkeypatch.setenv(publish_v2_epoch.RELAYER_KEY_ENV, RELAYER_SECRET)
    monkeypatch.setattr(publish_v2_epoch, "RegistryV2Backend", FakeCLIBackend)
    monkeypatch.setattr(
        publish_v2_epoch.PublisherKey, "from_env", lambda manifest: object()
    )
    monkeypatch.setattr(publish_v2_epoch, "assert_role_separation", lambda: None)
    monkeypatch.setattr(
        publish_v2_epoch, "exclusive_lock", lambda path: nullcontext()
    )
    monkeypatch.setattr(
        publish_v2_epoch.QuorumRPC, "from_env", lambda: quorum
    )


def test_cli_preflight_allows_local_chain_without_rpc_quorum(
    reporter: Ed25519Signer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json", _manifest(reporter))
    _patch_cli_boundaries(monkeypatch, None)

    result = publish_v2_epoch.run(
        _cli_arguments(manifest_path, tmp_path / "pending.json", preflight=True)
    )

    assert result["published"] is False
    assert result["chain_id"] == 31337
    assert result["registry"] == REGISTRY
    assert FakeCLIBackend.instances[0].quorum is None


def test_cli_requires_explicit_relayer_environment_key(
    reporter: Ed25519Signer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json", _manifest(reporter))
    _patch_cli_boundaries(monkeypatch, None)
    monkeypatch.delenv(publish_v2_epoch.RELAYER_KEY_ENV)

    with pytest.raises(
        RegistryV2PublicationError,
        match=publish_v2_epoch.RELAYER_KEY_ENV,
    ):
        publish_v2_epoch.run(
            _cli_arguments(manifest_path, tmp_path / "workspace", preflight=True)
        )

    assert FakeCLIBackend.instances == []


def test_cli_preflight_refuses_public_chain_without_rpc_quorum(
    reporter: Ed25519Signer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _write_manifest(
        tmp_path / "manifest.json", _manifest(reporter, chain_id=196)
    )
    _patch_cli_boundaries(monkeypatch, None)

    with pytest.raises(RegistryV2PreflightFailed, match="quorum"):
        publish_v2_epoch.run(
            _cli_arguments(manifest_path, tmp_path / "pending.json", preflight=True)
        )

    assert FakeCLIBackend.instances == []


def test_cli_preflight_passes_configured_quorum_on_public_chain(
    reporter: Ed25519Signer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _write_manifest(
        tmp_path / "manifest.json", _manifest(reporter, chain_id=196)
    )
    quorum = object()
    _patch_cli_boundaries(monkeypatch, quorum)

    result = publish_v2_epoch.run(
        _cli_arguments(manifest_path, tmp_path / "pending.json", preflight=True)
    )

    assert result["published"] is False
    assert result["chain_id"] == 196
    assert FakeCLIBackend.instances[0].quorum is quorum


@pytest.mark.parametrize(
    ("correction", "method"), [(False, "publish"), (True, "publish_correction")]
)
def test_cli_parses_signed_report_and_selects_publication_path(
    reporter: Ed25519Signer,
    signed_report: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    correction: bool,
    method: str,
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json", _manifest(reporter))
    report_path = tmp_path / "signed-report.json"
    report_path.write_bytes(canonical_json_bytes(signed_report))
    workspace = tmp_path / "workspace"
    calls: list[tuple[str, dict[str, object], str]] = []

    class FakeCLIClient:
        def __init__(
            self, backend: object, pending_path: str | Path, receipt_timeout: float = 120.0
        ) -> None:
            del backend, receipt_timeout
            assert Path(pending_path) == workspace / "pending-v2.json"

        def publish(
            self, value: dict[str, object], *, report_uri: str
        ) -> object:
            calls.append(("publish", value, report_uri))
            return self._result()

        def publish_correction(
            self, value: dict[str, object], *, report_uri: str
        ) -> object:
            calls.append(("publish_correction", value, report_uri))
            return self._result()

        @staticmethod
        def _result() -> object:
            return SimpleNamespace(
                transaction_hash="0x" + "12" * 32,
                receipt=dict(RECEIPT),
                reconciled=False,
                report=SimpleNamespace(sequence=1, report_digest="34" * 32),
            )

    _patch_cli_boundaries(monkeypatch, None)
    monkeypatch.setattr(
        publish_v2_epoch, "RegistryV2PublisherClient", FakeCLIClient
    )

    result = publish_v2_epoch.run(
        _cli_arguments(
            manifest_path,
            workspace,
            preflight=False,
            signed_report=report_path,
            correction=correction,
        )
    )

    assert calls == [(method, signed_report, "ipfs://reports/ustb.json")]
    assert result["published"] is True
    assert result["transaction_hash"] == "0x" + "12" * 32
    assert result["receipt"] == RECEIPT


def test_prepared_raw_transaction_fixture_is_bound_to_relayer_and_deployment(
    prepared: PreparedRegistryV2Transaction,
    manifest: RegistryV2DeploymentManifest,
) -> None:
    decoded = decoded_transaction(prepared.raw)

    assert decoded["chain_id"] == manifest.chain_id
    assert decoded["to"] == manifest.registry_address
    assert decoded["sender"] == RELAYER
    assert decoded["nonce"] == prepared.nonce
    assert decoded["data"] == CALLDATA
    assert decoded["value"] == 0
