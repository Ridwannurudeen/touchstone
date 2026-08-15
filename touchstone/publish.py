"""Idempotent publication for the fixed TouchstoneRegistry ABI.

Every transaction is signed locally and broadcast as raw bytes. No node is ever asked to
sign on this project's behalf, on any network including the local development chain, so
there is exactly one code path to reason about and it is the one that runs in production.
An unlocked account would be a second path that skips every check below.

Before a signature is produced, the endpoint is made to agree with the committed
deployment manifest: its chain id, the runtime bytecode actually deployed at the registry
address, the chain id the registry itself was constructed with, the publisher's onchain
authorization, and that the publisher is not the owner. Each of those can drift silently —
an endpoint failing over to another network, an address reused for a new contract, an
authorization revoked during an incident — and each would otherwise produce a
correctly-signed transaction that means nothing or lands somewhere unintended.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import os
from pathlib import Path
import re
import time as clock
from typing import Protocol

from web3 import Web3
from web3.exceptions import (
    BlockNotFound,
    ContractLogicError,
    MethodUnavailable,
    TimeExhausted,
    TransactionNotFound,
    Web3RPCError,
)
from cryptography.exceptions import InvalidSignature

from touchstone.controls import AssetState
from touchstone.deployment import DeploymentManifest, runtime_bytecode_sha256
from touchstone.keyring import PublisherKey
from touchstone.signing import (
    canonical_json_bytes,
    strict_json_loads,
    verify_signed_report,
)
from touchstone.translog import TransparencyLog


# Headroom over the estimate, because the estimate is taken against the pending state and
# the transaction executes against a later one. Unused gas is refunded, so the only cost of
# margin is a larger balance requirement; too little margin costs a failed publication.
GAS_MARGIN_PERCENT = 25
# Used only where the endpoint will not quote a priority fee itself.
FALLBACK_PRIORITY_FEE_WEI = 1_000_000_000
_CONFIRMATION_POLL_SECONDS = 1.0
_ASSET_KEY = re.compile(r"eip155:[1-9][0-9]*:0x[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TX_HASH = re.compile(r"0x[0-9a-f]{64}")
_STATUS = {
    AssetState.CONFIRMED.value: 0,
    AssetState.STALE.value: 1,
    AssetState.INCONSISTENT.value: 2,
    AssetState.UNVERIFIABLE.value: 3,
}
REGISTRY_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "assetKey", "type": "bytes32"},
            {"indexed": True, "name": "sequence", "type": "uint64"},
            {"indexed": True, "name": "publisher", "type": "address"},
            {"indexed": False, "name": "controlSetRoot", "type": "bytes32"},
            {"indexed": False, "name": "evidenceRoot", "type": "bytes32"},
            {"indexed": False, "name": "status", "type": "uint8"},
            {"indexed": False, "name": "observedAt", "type": "uint64"},
            {"indexed": False, "name": "validUntil", "type": "uint64"},
            {"indexed": False, "name": "reportURI", "type": "string"},
        ],
        "name": "Published",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "assetKey", "type": "bytes32"},
            {"indexed": True, "name": "sequence", "type": "uint64"},
            {"indexed": False, "name": "correctedSequence", "type": "uint64"},
            {"indexed": True, "name": "publisher", "type": "address"},
            {"indexed": False, "name": "controlSetRoot", "type": "bytes32"},
            {"indexed": False, "name": "evidenceRoot", "type": "bytes32"},
            {"indexed": False, "name": "status", "type": "uint8"},
            {"indexed": False, "name": "observedAt", "type": "uint64"},
            {"indexed": False, "name": "validUntil", "type": "uint64"},
            {"indexed": False, "name": "reportURI", "type": "string"},
        ],
        "name": "Corrected",
        "type": "event",
    },
    {
        "inputs": [{"name": "", "type": "bytes32"}],
        "name": "latestSequence",
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "expectedChainId",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "", "type": "address"}],
        "name": "isPublisherAuthorized",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "sequence", "type": "uint64"},
        ],
        "name": "getReport",
        "outputs": [
            {
                "components": [
                    {"name": "controlSetRoot", "type": "bytes32"},
                    {"name": "evidenceRoot", "type": "bytes32"},
                    {"name": "status", "type": "uint8"},
                    {"name": "observedAt", "type": "uint64"},
                    {"name": "validUntil", "type": "uint64"},
                    {"name": "publisher", "type": "address"},
                    {"name": "sequence", "type": "uint64"},
                    {"name": "reportURI", "type": "string"},
                ],
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "controlSetRoot", "type": "bytes32"},
            {"name": "evidenceRoot", "type": "bytes32"},
            {"name": "status", "type": "uint8"},
            {"name": "observedAt", "type": "uint64"},
            {"name": "validUntil", "type": "uint64"},
            {"name": "sequence", "type": "uint64"},
            {"name": "reportURI", "type": "string"},
        ],
        "name": "publish",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "correctedSequence", "type": "uint64"},
            {"name": "controlSetRoot", "type": "bytes32"},
            {"name": "evidenceRoot", "type": "bytes32"},
            {"name": "status", "type": "uint8"},
            {"name": "observedAt", "type": "uint64"},
            {"name": "validUntil", "type": "uint64"},
            {"name": "sequence", "type": "uint64"},
            {"name": "reportURI", "type": "string"},
        ],
        "name": "publishCorrection",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


class PublicationError(RuntimeError):
    """Base class for publication and reconciliation failures."""


class DuplicateSequence(PublicationError):
    """The requested sequence is already present without a matching pending send."""


class SequenceMismatch(PublicationError):
    """The requested sequence is not exactly the next onchain sequence."""


class PendingSubmission(PublicationError):
    """A known transaction is pending and must not be submitted again."""


class SubmissionFailed(PublicationError):
    """A known transaction was mined unsuccessfully or reconciled inconsistently."""


class PreflightFailed(PublicationError):
    """The chain does not match the manifest, so nothing was signed."""


class FeeCeilingExceeded(PublicationError):
    """The worst-case fee for this transaction exceeds the manifest's ceiling."""


@dataclass(frozen=True, slots=True)
class ChainReport:
    control_set_root: str
    evidence_root: str
    status: int
    observed_at: int
    valid_until: int
    publisher: str
    sequence: int
    report_uri: str


@dataclass(frozen=True, slots=True)
class PublicationResult:
    transaction_hash: str
    receipt: dict[str, object]
    reconciled: bool
    log_entry_hash: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """What the chain answered when it was checked against the manifest."""

    chain_id: int
    block_number: int
    registry_address: str
    registry_runtime_bytecode_sha256: str
    registry_expected_chain_id: int
    registry_owner: str
    publisher_address: str
    publisher_authorized: bool
    publisher_balance_wei: int


class RegistryBackend(Protocol):
    def latest_sequence(self, asset_key: bytes) -> int: ...

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport: ...

    def submit(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ) -> str: ...

    def get_receipt(self, transaction_hash: str) -> Mapping[str, object] | None: ...

    def wait_for_receipt(
        self, transaction_hash: str, timeout: float
    ) -> Mapping[str, object]: ...

    def find_receipt(
        self, asset_key: bytes, sequence: int, correction_of: int | None
    ) -> tuple[str, Mapping[str, object]] | None: ...


class SignedRegistryBackend:
    """Publish to a manifest-pinned registry using locally signed raw transactions.

    The same class serves the local development chain and a public network. There is no
    local-only shortcut, because a shortcut is a path that never gets audited until the
    day it runs against something real.
    """

    def __init__(
        self,
        manifest: DeploymentManifest,
        publisher_key: PublisherKey,
        *,
        request_timeout: float = 30.0,
    ) -> None:
        if publisher_key.address != manifest.publisher_address:
            raise PreflightFailed(
                f"publisher key {publisher_key.address} is not the manifest's "
                f"{manifest.publisher_address}"
            )
        self.manifest = manifest
        self.publisher_key = publisher_key
        self.publisher_address = publisher_key.address
        self.web3 = Web3(
            Web3.HTTPProvider(
                manifest.rpc_url, request_kwargs={"timeout": float(request_timeout)}
            )
        )
        self.contract = self.web3.eth.contract(
            address=manifest.registry_address, abi=REGISTRY_ABI
        )
        self._preflight: PreflightReport | None = None

    def preflight(self) -> PreflightReport:
        """Make the endpoint agree with the manifest, or refuse.

        Every check here answers a way a correctly-signed transaction could still be
        wrong: sent to another network, sent to an address that no longer holds this
        contract, sent to a registry built for a different chain, sent from an identity
        whose authority was revoked, or sent from the owner — an identity powerful enough
        that it should never be the one running unattended.
        """
        manifest = self.manifest
        try:
            chain_id = int(self.web3.eth.chain_id)
            block_number = int(self.web3.eth.block_number)
            code = bytes(self.web3.eth.get_code(manifest.registry_address))
        except (Web3RPCError, OSError) as error:
            raise PreflightFailed(f"cannot read {manifest.rpc_url}: {error}") from error
        if chain_id != manifest.chain_id:
            raise PreflightFailed(
                f"endpoint reports chain {chain_id}, manifest declares "
                f"{manifest.chain_id}"
            )
        if not code:
            raise PreflightFailed(
                f"no contract is deployed at {manifest.registry_address}"
            )
        digest = runtime_bytecode_sha256(code)
        if digest != manifest.registry_runtime_bytecode_sha256:
            raise PreflightFailed(
                f"{manifest.registry_address} holds runtime bytecode {digest}, manifest "
                f"declares {manifest.registry_runtime_bytecode_sha256}"
            )
        try:
            registry_chain_id = int(self.contract.functions.expectedChainId().call())
            owner = Web3.to_checksum_address(self.contract.functions.owner().call())
            authorized = bool(
                self.contract.functions.isPublisherAuthorized(
                    self.publisher_address
                ).call()
            )
            balance = int(self.web3.eth.get_balance(self.publisher_address))
        except (Web3RPCError, OSError) as error:
            raise PreflightFailed(f"registry did not answer: {error}") from error
        if registry_chain_id != manifest.chain_id:
            raise PreflightFailed(
                f"registry was constructed for chain {registry_chain_id}, manifest "
                f"declares {manifest.chain_id}"
            )
        if owner == self.publisher_address:
            raise PreflightFailed(
                "the publisher is the registry owner; publishing must not run with the "
                "authority to revoke and rotate publishers"
            )
        if manifest.deployer_address is not None and owner != manifest.deployer_address:
            raise PreflightFailed(
                f"registry owner is {owner}, manifest declares deployer "
                f"{manifest.deployer_address}"
            )
        if not authorized:
            raise PreflightFailed(
                f"{self.publisher_address} is not an authorized publisher"
            )
        if balance <= 0:
            raise PreflightFailed(f"{self.publisher_address} holds no gas")
        report = PreflightReport(
            chain_id=chain_id,
            block_number=block_number,
            registry_address=manifest.registry_address,
            registry_runtime_bytecode_sha256=digest,
            registry_expected_chain_id=registry_chain_id,
            registry_owner=owner,
            publisher_address=self.publisher_address,
            publisher_authorized=authorized,
            publisher_balance_wei=balance,
        )
        self._preflight = report
        return report

    def latest_sequence(self, asset_key: bytes) -> int:
        self._ensure_preflight()
        return int(self.contract.functions.latestSequence(asset_key).call())

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport:
        self._ensure_preflight()
        value = self.contract.functions.getReport(asset_key, sequence).call()
        return ChainReport(
            control_set_root=_bytes32_hex(value[0]),
            evidence_root=_bytes32_hex(value[1]),
            status=int(value[2]),
            observed_at=int(value[3]),
            valid_until=int(value[4]),
            publisher=value[5],
            sequence=int(value[6]),
            report_uri=value[7],
        )

    def submit(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ) -> str:
        common = (
            asset_key,
            bytes.fromhex(report["control_set_root"]),
            bytes.fromhex(report["evidence_root"]),
            _STATUS[report["state"]],
            _unix_timestamp(report["observed_at"], "observed_at"),
            _unix_timestamp(report["valid_until"], "valid_until"),
            report["sequence"],
            report_uri,
        )
        if correction_of is None:
            function = self.contract.functions.publish(*common)
        else:
            function = self.contract.functions.publishCorrection(
                common[0], correction_of, *common[1:]
            )

        # Re-run in full rather than reusing a cached result: authorization can be
        # revoked between reading the sequence and signing, and revocation during an
        # incident is precisely when this must not go through.
        preflight = self.preflight()
        try:
            estimated = int(function.estimate_gas({"from": self.publisher_address}))
        except (ContractLogicError, Web3RPCError) as error:
            raise PreflightFailed(
                f"the registry would reject this publication: {error}"
            ) from error
        gas = estimated * (100 + GAS_MARGIN_PERCENT) // 100
        fees = self._fee_fields()
        worst_case_fee = gas * fees.get("maxFeePerGas", fees.get("gasPrice", 0))
        if (
            self.manifest.max_fee_wei is not None
            and worst_case_fee > self.manifest.max_fee_wei
        ):
            raise FeeCeilingExceeded(
                f"worst-case fee {worst_case_fee} wei exceeds the manifest ceiling "
                f"{self.manifest.max_fee_wei} wei"
            )
        if preflight.publisher_balance_wei < worst_case_fee:
            raise PreflightFailed(
                f"{self.publisher_address} holds {preflight.publisher_balance_wei} wei, "
                f"below the worst-case fee of {worst_case_fee} wei"
            )

        transaction = function.build_transaction(
            {
                "from": self.publisher_address,
                "chainId": self.manifest.chain_id,
                "gas": gas,
                "nonce": self.web3.eth.get_transaction_count(
                    self.publisher_address, "pending"
                ),
                **fees,
            }
        )
        transaction_hash, raw = self.publisher_key.sign_transaction(transaction)
        broadcast = _transaction_hash(self.web3.eth.send_raw_transaction(raw))
        if broadcast != transaction_hash:
            raise SubmissionFailed(
                f"endpoint acknowledged {broadcast} for a transaction signed as "
                f"{transaction_hash}"
            )
        return transaction_hash

    def get_receipt(self, transaction_hash: str) -> Mapping[str, object] | None:
        try:
            receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
        except TransactionNotFound:
            return None
        return receipt if self._confirmed(receipt) else None

    def wait_for_receipt(
        self, transaction_hash: str, timeout: float
    ) -> Mapping[str, object]:
        """Wait for inclusion, then for the manifest's confirmation depth.

        A receipt only says a transaction was included in some block. Until that block is
        buried it can be reorganised away, taking the publication with it while the
        journal records it as settled, so the wait is not over when the receipt arrives.
        """
        deadline = clock.monotonic() + float(timeout)
        receipt = self.web3.eth.wait_for_transaction_receipt(
            transaction_hash, timeout=timeout
        )
        while not self._confirmed(receipt):
            if clock.monotonic() >= deadline:
                raise TimeExhausted(
                    f"transaction {transaction_hash} did not reach "
                    f"{self.manifest.confirmations} confirmations"
                )
            clock.sleep(_CONFIRMATION_POLL_SECONDS)
            try:
                receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
            except TransactionNotFound as error:
                raise SubmissionFailed(
                    f"transaction {transaction_hash} left the chain before confirming"
                ) from error
        return receipt

    def _confirmed(self, receipt: Mapping[str, object]) -> bool:
        """Whether this receipt is buried deep enough and still on the canonical chain."""
        block_number = receipt["blockNumber"]
        if block_number is None:
            return False
        depth = int(self.web3.eth.block_number) - int(block_number) + 1
        if depth < self.manifest.confirmations:
            return False
        try:
            canonical = self.web3.eth.get_block(int(block_number))
        except BlockNotFound as error:
            raise SubmissionFailed(
                f"block {block_number} is no longer retrievable"
            ) from error
        if bytes(canonical["hash"]) != bytes(receipt["blockHash"]):
            raise SubmissionFailed(
                f"block {block_number} was reorganised away from the receipt's block"
            )
        return True

    def find_receipt(
        self, asset_key: bytes, sequence: int, correction_of: int | None
    ) -> tuple[str, Mapping[str, object]] | None:
        self._ensure_preflight()
        event = (
            self.contract.events.Published()
            if correction_of is None
            else self.contract.events.Corrected()
        )
        logs = tuple(
            event.get_logs(
                argument_filters={"assetKey": asset_key, "sequence": sequence},
                # A public network is not scanned from genesis. The manifest records the
                # block the registry was deployed in; nothing before it can be relevant.
                from_block=self.manifest.deployment_block,
                to_block="latest",
            )
        )
        if not logs:
            return None
        if len(logs) != 1:
            raise SubmissionFailed("multiple publication events match one sequence")
        if (
            correction_of is not None
            and logs[0]["args"]["correctedSequence"] != correction_of
        ):
            raise SubmissionFailed("correction event references the wrong sequence")
        transaction_hash = _transaction_hash(logs[0]["transactionHash"])
        receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
        return (transaction_hash, receipt) if self._confirmed(receipt) else None

    def _ensure_preflight(self) -> PreflightReport:
        """Refuse to read the registry until the endpoint has been shown to be the one
        the manifest describes. A read from the wrong chain is not a harmless read: it
        decides which sequence gets published next."""
        return self._preflight if self._preflight is not None else self.preflight()

    def _fee_fields(self) -> dict[str, int]:
        """Price the transaction from the chain rather than from an assumption.

        Whether a network prices in EIP-1559 terms is a property of that network, so it is
        read from the latest block instead of being declared in the manifest — a manifest
        that says 1559 about a chain that does not support it produces transactions no
        node will accept, and the manifest would be believed over the chain.
        """
        block = self.web3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas")
        if base_fee is None:
            return {"gasPrice": int(self.web3.eth.gas_price)}
        try:
            priority_fee = int(self.web3.eth.max_priority_fee)
        except (Web3RPCError, MethodUnavailable, ValueError):
            priority_fee = FALLBACK_PRIORITY_FEE_WEI
        # Two base fees of headroom absorbs the protocol's maximum 12.5% per-block rise
        # for several blocks; the excess is never spent, only reserved.
        return {
            "maxFeePerGas": 2 * int(base_fee) + priority_fee,
            "maxPriorityFeePerGas": priority_fee,
        }


class PublisherClient:
    """Reconcile before sending and persist a transaction hash across restarts."""

    def __init__(
        self,
        backend: RegistryBackend,
        transparency_log: TransparencyLog,
        pending_path: str | os.PathLike[str],
        *,
        receipt_timeout: float = 120.0,
    ) -> None:
        self.backend = backend
        self.transparency_log = transparency_log
        self.pending_path = Path(pending_path)
        if receipt_timeout <= 0:
            raise ValueError("receipt_timeout must be positive")
        self.receipt_timeout = float(receipt_timeout)

    def publish(
        self,
        signed_report: Mapping[str, object],
        *,
        published_key: Mapping[str, object],
        report_uri: str,
    ) -> PublicationResult:
        """Publish an ordinary report; corrections use ``publish_correction``."""
        report = _verified_report(signed_report, published_key)
        if report.get("correction_of") is not None:
            raise ValueError("correction reports require publish_correction")
        return self._publish(
            signed_report, report, report_uri=report_uri, correction_of=None
        )

    def publish_correction(
        self,
        signed_report: Mapping[str, object],
        *,
        published_key: Mapping[str, object],
        report_uri: str,
    ) -> PublicationResult:
        """Publish a correction through the registry's distinct correction function."""
        report = _verified_report(signed_report, published_key)
        correction_of = report.get("correction_of")
        if type(correction_of) is not int:
            raise ValueError("correction report must identify correction_of")
        return self._publish(
            signed_report,
            report,
            report_uri=report_uri,
            correction_of=correction_of,
        )

    def _publish(
        self,
        signed_report: Mapping[str, object],
        report: Mapping[str, object],
        *,
        report_uri: str,
        correction_of: int | None,
    ) -> PublicationResult:
        if not isinstance(report_uri, str) or not report_uri:
            raise ValueError("report_uri must be nonempty text")
        asset_key = _asset_key_bytes(report.get("asset_key"))
        sequence = report.get("sequence")
        if type(sequence) is not int or sequence < 1:
            raise ValueError("report sequence must be a positive integer")
        _validate_publishable_report(report)
        pending = self._load_pending()
        expected_pending = _pending_record(
            report, report_uri=report_uri, correction_of=correction_of
        )
        if pending is not None and any(
            pending.get(key) != value for key, value in expected_pending.items()
        ):
            raise PendingSubmission("another persisted submission is unresolved")

        current = self.backend.latest_sequence(asset_key)
        if current > sequence:
            raise DuplicateSequence(
                f"onchain sequence {current} is already beyond requested {sequence}"
            )
        if current == sequence:
            if pending is None:
                raise DuplicateSequence(f"sequence {sequence} is already published")
            self._ensure_onchain_match(asset_key, report, report_uri)
            transaction_hash = pending["transaction_hash"]
            if transaction_hash is None:
                found = self.backend.find_receipt(asset_key, sequence, correction_of)
                if found is None:
                    raise PendingSubmission(
                        "chain advanced but its publication event is unavailable"
                    )
                transaction_hash, receipt = found
            else:
                receipt = self.backend.get_receipt(transaction_hash)
                if receipt is None:
                    raise PendingSubmission(
                        "chain advanced but the persisted transaction receipt is unavailable"
                    )
            if _receipt_status(receipt) != 1:
                raise SubmissionFailed(f"transaction {transaction_hash} failed")
            return self._finalize(
                signed_report,
                receipt,
                transaction_hash,
                correction_of,
                reconciled=True,
            )
        if sequence != current + 1:
            raise SequenceMismatch(
                f"onchain sequence is {current}; next report must be {current + 1}"
            )

        if pending is not None:
            transaction_hash = pending["transaction_hash"]
            if transaction_hash is None:
                raise PendingSubmission(
                    "broadcast outcome is unknown; refusing to resubmit before chain reconciliation"
                )
            receipt = self.backend.get_receipt(transaction_hash)
            if receipt is None:
                raise PendingSubmission(
                    f"transaction {transaction_hash} is still pending; refusing to resubmit"
                )
            if _receipt_status(receipt) != 1:
                self._clear_pending()
                raise SubmissionFailed(f"transaction {transaction_hash} failed")
            if self.backend.latest_sequence(asset_key) != sequence:
                raise SubmissionFailed(
                    "successful receipt did not advance registry sequence"
                )
            self._ensure_onchain_match(asset_key, report, report_uri)
            return self._finalize(
                signed_report,
                receipt,
                transaction_hash,
                correction_of,
                reconciled=True,
            )

        self._write_pending({**expected_pending, "transaction_hash": None})
        transaction_hash = self.backend.submit(
            asset_key, report, report_uri, correction_of
        )
        self._write_pending({**expected_pending, "transaction_hash": transaction_hash})
        try:
            receipt = self.backend.wait_for_receipt(
                transaction_hash, self.receipt_timeout
            )
        except TimeExhausted as error:
            raise PendingSubmission(
                f"transaction {transaction_hash} remains pending"
            ) from error
        if _receipt_status(receipt) != 1:
            self._clear_pending()
            raise SubmissionFailed(f"transaction {transaction_hash} failed")
        if self.backend.latest_sequence(asset_key) != sequence:
            raise SubmissionFailed(
                "successful receipt did not advance registry sequence"
            )
        self._ensure_onchain_match(asset_key, report, report_uri)
        return self._finalize(
            signed_report,
            receipt,
            transaction_hash,
            correction_of,
            reconciled=False,
        )

    def _ensure_onchain_match(
        self, asset_key: bytes, report: Mapping[str, object], report_uri: str
    ) -> None:
        onchain = self.backend.get_report(asset_key, report["sequence"])
        expected = (
            report["control_set_root"],
            report["evidence_root"],
            _STATUS[report["state"]],
            _unix_timestamp(report["observed_at"], "observed_at"),
            _unix_timestamp(report["valid_until"], "valid_until"),
            report["sequence"],
            report_uri,
        )
        actual = (
            onchain.control_set_root,
            onchain.evidence_root,
            onchain.status,
            onchain.observed_at,
            onchain.valid_until,
            onchain.sequence,
            onchain.report_uri,
        )
        if actual != expected:
            raise SubmissionFailed("onchain report does not match the signed report")

    def _finalize(
        self,
        signed_report: Mapping[str, object],
        receipt: Mapping[str, object],
        transaction_hash: str,
        correction_of: int | None,
        *,
        reconciled: bool,
    ) -> PublicationResult:
        entries = self.transparency_log.verify()
        existing = next(
            (
                entry
                for entry in entries
                if entry["publication"]["transaction_hash"] == transaction_hash
            ),
            None,
        )
        receipt_record = _receipt_record(receipt)
        if existing is None:
            supersedes = None
            if correction_of is not None:
                report = signed_report["report"]
                supersedes = next(
                    (
                        entry["entry_hash"]
                        for entry in entries
                        if entry["signed_report"]["report"].get("asset_key")
                        == report.get("asset_key")
                        and entry["signed_report"]["report"].get("sequence")
                        == correction_of
                    ),
                    None,
                )
                if supersedes is None:
                    raise SubmissionFailed(
                        "correction target has no transparency-log entry"
                    )
            existing = self.transparency_log.append(
                signed_report,
                transaction_hash=transaction_hash,
                receipt=receipt_record,
                supersedes=supersedes,
            )
        self._clear_pending()
        return PublicationResult(
            transaction_hash=transaction_hash,
            receipt=receipt_record,
            reconciled=reconciled,
            log_entry_hash=existing["entry_hash"],
        )

    def _load_pending(self) -> dict[str, object] | None:
        if not self.pending_path.exists():
            return None
        try:
            value = strict_json_loads(self.pending_path.read_bytes())
        except (OSError, TypeError, ValueError) as error:
            raise PendingSubmission(
                f"cannot read pending submission: {error}"
            ) from error
        if not isinstance(value, dict):
            raise PendingSubmission("pending submission must be an object")
        transaction_hash = value.get("transaction_hash")
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or _TX_HASH.fullmatch(transaction_hash) is None
        ):
            raise PendingSubmission(
                "pending submission has an invalid transaction hash"
            )
        return value

    def _write_pending(self, value: Mapping[str, object]) -> None:
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pending_path.with_name(self.pending_path.name + ".tmp")
        with temporary.open("wb") as output:
            output.write(canonical_json_bytes(dict(value)) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.pending_path)

    def _clear_pending(self) -> None:
        self.pending_path.unlink(missing_ok=True)


def _verified_report(
    signed_report: Mapping[str, object], published_key: Mapping[str, object]
) -> Mapping[str, object]:
    if not isinstance(signed_report, Mapping):
        raise ValueError("signed_report must be a mapping")
    kid = signed_report.get("kid")
    if not isinstance(kid, str):
        raise ValueError("signed_report kid is invalid")
    try:
        report = verify_signed_report(signed_report, {kid: published_key})
    except InvalidSignature as error:
        raise ValueError("signed_report signature is invalid") from error
    if not isinstance(report, Mapping):
        raise ValueError("signed_report.report must be a mapping")
    if report.get("publisher_kid") != kid:
        raise ValueError("report publisher kid does not match signature kid")
    return report


def _validate_publishable_report(report: Mapping[str, object]) -> None:
    for field in ("control_set_root", "evidence_root"):
        value = report.get(field)
        if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
            raise ValueError(f"report {field} must be a lowercase SHA-256 digest")
    if report.get("state") not in _STATUS:
        raise ValueError("report state is invalid")
    observed_at = _normalized_timestamp(report.get("observed_at"), "observed_at")
    valid_until = _normalized_timestamp(report.get("valid_until"), "valid_until")
    transition = report.get("state_transition")
    if not isinstance(transition, Mapping):
        raise ValueError("report state_transition must be a mapping")
    try:
        as_of = date.fromisoformat(transition["as_of"])
        deadline = date.fromisoformat(transition["evidence_deadline"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("report state transition dates are invalid") from error
    if observed_at.date() != as_of:
        raise ValueError("report observed_at does not match its evaluated epoch")
    expected_valid = max(
        datetime.combine(deadline, time(23, 59, 59), tzinfo=timezone.utc),
        observed_at,
    )
    if valid_until != expected_valid:
        raise ValueError("report valid_until does not match its evidence deadline")


def _asset_key_bytes(value: object) -> bytes:
    if not isinstance(value, str) or _ASSET_KEY.fullmatch(value) is None:
        raise ValueError("asset_key must be a canonical eip155 identifier")
    return bytes(Web3.keccak(text=value))


def _unix_timestamp(value: object, field: str) -> int:
    parsed = _normalized_timestamp(value, field)
    timestamp = int(parsed.timestamp())
    if not 0 <= timestamp <= 2**64 - 1:
        raise ValueError(f"{field} does not fit uint64")
    return timestamp


def _normalized_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a normalized UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{field} must be a normalized UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{field} must be a normalized UTC timestamp")
    return parsed


def _pending_record(
    report: Mapping[str, object], *, report_uri: str, correction_of: int | None
) -> dict[str, object]:
    return {
        "asset_key": report["asset_key"],
        "correction_of": correction_of,
        "report_sha256": hashlib.sha256(canonical_json_bytes(dict(report))).hexdigest(),
        "report_uri": report_uri,
        "sequence": report["sequence"],
    }


def _receipt_status(receipt: Mapping[str, object]) -> int:
    value = receipt.get("status")
    if type(value) is not int:
        raise SubmissionFailed("transaction receipt has no integer status")
    return value


def _receipt_record(receipt: Mapping[str, object]) -> dict[str, object]:
    return {
        "block_hash": _optional_hex(receipt.get("blockHash")),
        "block_number": receipt.get("blockNumber"),
        "gas_used": receipt.get("gasUsed"),
        "status": _receipt_status(receipt),
    }


def _optional_hex(value: object) -> str | None:
    if value is None:
        return None
    return _transaction_hash(value)


def _transaction_hash(value: object) -> str:
    if isinstance(value, bytes):
        encoded = "0x" + value.hex()
    elif hasattr(value, "hex"):
        encoded = value.hex()
        if not encoded.startswith("0x"):
            encoded = "0x" + encoded
    else:
        encoded = str(value)
    encoded = encoded.lower()
    if _TX_HASH.fullmatch(encoded) is None:
        raise ValueError("transaction hash must be 32 bytes")
    return encoded


def _bytes32_hex(value: object) -> str:
    if isinstance(value, bytes):
        raw = bytes(value)
    else:
        raw = bytes(value)
    if len(raw) != 32:
        raise ValueError("onchain bytes32 value has the wrong length")
    return raw.hex()
