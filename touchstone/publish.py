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
from touchstone.keyring import PublisherKey, decoded_transaction
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

# What the chain currently says about a transaction we hold signed bytes for.
MISSING = "missing"
INCLUDED = "included"
CONFIRMED = "confirmed"
_ASSET_KEY = re.compile(r"eip155:[1-9][0-9]*:0x[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TX_HASH = re.compile(r"0x[0-9a-f]{64}")
_LOWER_HEX = re.compile(r"(?:[0-9a-f]{2})+")
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
        "inputs": [{"name": "", "type": "address"}],
        "name": "publisherIdentity",
        "outputs": [{"name": "", "type": "address"}],
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
    """The chain does not match the manifest, so nothing was signed.

    This is a *permanent* refusal: a wrong chain, unexpected bytecode, a revoked
    authorization, a foreign lineage, a rejected gas estimate. Retrying it changes
    nothing, and a caller that retried on it would hammer an endpoint that is telling it
    something true.
    """


class TransportUnavailable(PreflightFailed):
    """The endpoint could not be reached or did not answer. Nothing was signed.

    Separated from the permanent refusals because it is the only pre-broadcast failure
    where trying again is meaningful. It stays a subclass so existing handling still
    catches it — a transport failure is still a reason not to publish.
    """


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
class DeploymentIdentity:
    """The three facts that make a signed transaction belong to one deployment."""

    chain_id: int
    registry_address: str
    publisher_address: str


@dataclass(frozen=True, slots=True)
class PreparedTransaction:
    """Signed bytes that have not been broadcast, and the hash they will carry.

    Preparation and broadcast are separate because they fail differently. Everything up to
    and including signing either succeeds or refuses definitively, and a refusal must leave
    no trace. Once these bytes exist they may reach the wire, so they are journalled before
    they are sent and re-sending them is always idempotent.
    """

    transaction_hash: str
    raw: bytes
    nonce: int


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
    publisher_identity: str
    publisher_balance_wei: int


class RegistryBackend(Protocol):
    # The deployment a backend publishes to is the backend's own, not something a caller
    # supplies alongside it. Two manifests meant two answers to "which reporting key is
    # active", and the client believed whichever one it was handed.
    manifest: DeploymentManifest

    def revalidate(self) -> object: ...

    def receipt_state(
        self, transaction_hash: str
    ) -> tuple[str, Mapping[str, object] | None]: ...

    def identity(self) -> DeploymentIdentity: ...

    def publisher_lineage(self, address: str) -> str: ...

    def calldata(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ) -> bytes: ...

    def latest_sequence(self, asset_key: bytes) -> int: ...

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport: ...

    def prepare(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ) -> PreparedTransaction: ...

    def broadcast(self, prepared: PreparedTransaction) -> str: ...

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
                manifest.rpc_url,
                request_kwargs={"timeout": float(request_timeout)},
                # web3 retries five times by default and its allowlist includes
                # eth_sendRawTransaction, so a broadcast could be resent from inside the
                # provider — beneath the journal, beneath reconciliation, and invisible
                # to both. Identical signed bytes make that harmless in itself, but a
                # boundary with a bypass under it is not a boundary, and the retry
                # decision belongs to the caller that knows what is at stake.
                exception_retry_configuration=None,
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
            raise TransportUnavailable(
                f"cannot read {manifest.rpc_url}: {error}"
            ) from error
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
            identity = Web3.to_checksum_address(
                self.contract.functions.publisherIdentity(self.publisher_address).call()
            )
            balance = int(self.web3.eth.get_balance(self.publisher_address))
        except (Web3RPCError, OSError) as error:
            raise TransportUnavailable(f"registry did not answer: {error}") from error
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
        if identity != manifest.publisher_identity_address:
            # Authorization alone says only that *some* owner call let this address
            # publish. Lineage says it is the same publishing identity the manifest was
            # written for. An owner who calls authorizePublisher(B) instead of
            # rotatePublisher(A, B) creates a second, unrelated lineage that reads as
            # authorized and that no consumer gated on isPublisherFor would accept.
            raise PreflightFailed(
                f"{self.publisher_address} belongs to publisher lineage {identity}, "
                f"manifest declares {manifest.publisher_identity_address}"
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
            publisher_identity=identity,
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

    def calldata(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ) -> bytes:
        """The exact call this publication is. Used to sign it and to recognise it later."""
        return bytes.fromhex(
            self._function(asset_key, report, report_uri, correction_of)
            ._encode_transaction_data()[2:]
        )

    def _function(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ):
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
            return self.contract.functions.publish(*common)
        return self.contract.functions.publishCorrection(
            common[0], correction_of, *common[1:]
        )

    def prepare(
        self,
        asset_key: bytes,
        report: Mapping[str, object],
        report_uri: str,
        correction_of: int | None,
    ) -> PreparedTransaction:
        """Verify, price and sign. Refuses definitively; broadcasts nothing."""
        function = self._function(asset_key, report, report_uri, correction_of)

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
        gas = -(-estimated * (100 + GAS_MARGIN_PERCENT) // 100)
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
        return PreparedTransaction(
            transaction_hash=transaction_hash,
            raw=raw,
            nonce=int(transaction["nonce"]),
        )

    def broadcast(self, prepared: PreparedTransaction) -> str:
        """Send exact signed bytes. Safe to call again with the same bytes."""
        try:
            acknowledged = _transaction_hash(
                self.web3.eth.send_raw_transaction(prepared.raw)
            )
        except Web3RPCError as error:
            if _is_already_known(error):
                # The node already holds these exact bytes. Same nonce, same hash, same
                # publication — this is the rebroadcast succeeding, not a conflict.
                return prepared.transaction_hash
            if _is_nonce_too_low(error):
                # The nonce is spent. If it was spent by *this* transaction then the
                # publication already happened and there is nothing to resend; if it was
                # spent by another, these bytes can never be mined and saying so is the
                # only honest outcome.
                state, _ = self.receipt_state(prepared.transaction_hash)
                if state != MISSING:
                    return prepared.transaction_hash
                raise SubmissionFailed(
                    f"nonce {prepared.nonce} was consumed by another transaction, so "
                    f"{prepared.transaction_hash} can never be mined: {error}"
                ) from error
            raise SubmissionFailed(
                f"endpoint refused transaction {prepared.transaction_hash}: {error}"
            ) from error
        if acknowledged != prepared.transaction_hash:
            raise SubmissionFailed(
                f"endpoint acknowledged {acknowledged} for a transaction signed as "
                f"{prepared.transaction_hash}"
            )
        return prepared.transaction_hash

    def get_receipt(self, transaction_hash: str) -> Mapping[str, object] | None:
        state, receipt = self.receipt_state(transaction_hash)
        return receipt if state == CONFIRMED else None

    def receipt_state(
        self, transaction_hash: str
    ) -> tuple[str, Mapping[str, object] | None]:
        """Distinguish a transaction that is missing from one that is merely young.

        Collapsing the two was a real recovery failure, not a wording problem. An included
        transaction awaiting confirmations looked identical to a dropped one, so recovery
        rebroadcast it, the node answered "nonce too low" because it was already mined,
        and the publication ended in a failure that had in fact succeeded.
        """
        try:
            receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
        except TransactionNotFound:
            return MISSING, None
        return (CONFIRMED if self._confirmed(receipt) else INCLUDED), receipt

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
                # Not filtered by publisher address. The registry allows one report per
                # sequence, so at most one event matches; the publisher it names is
                # checked below against our *lineage* rather than our current address,
                # because a rotation changes the address and must not orphan a
                # publication we made before it.
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
        event_publisher = Web3.to_checksum_address(logs[0]["args"]["publisher"])
        if self.publisher_lineage(event_publisher) != (
            self.manifest.publisher_identity_address
        ):
            raise SubmissionFailed(
                f"sequence {sequence} was published by {event_publisher}, which belongs "
                f"to a different publisher lineage than this deployment"
            )
        transaction_hash = _transaction_hash(logs[0]["transactionHash"])
        receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
        return (transaction_hash, receipt) if self._confirmed(receipt) else None

    def publisher_lineage(self, address: str) -> str:
        """The publishing identity the registry recorded for an address.

        Rotation deliberately carries the lineage from the outgoing publisher to the
        incoming one, so this is what stays constant across a rotation when the address
        does not.
        """
        self._ensure_preflight()
        return Web3.to_checksum_address(
            self.contract.functions.publisherIdentity(
                Web3.to_checksum_address(address)
            ).call()
        )

    def identity(self) -> DeploymentIdentity:
        """What a transaction signed for this deployment must commit to."""
        return DeploymentIdentity(
            chain_id=self.manifest.chain_id,
            registry_address=self.manifest.registry_address,
            publisher_address=self.publisher_address,
        )

    def revalidate(self) -> PreflightReport:
        """Prove the endpoint's identity again, now, and cache the result.

        This used to only drop the cache, which made it useless exactly where it was most
        needed: a caller that revalidated and then made no further chain read — deciding a
        receipt had failed, say — proceeded on an identity nobody had rechecked. Verifying
        eagerly means the decision that follows is taken under a proved endpoint.
        """
        self._preflight = None
        return self.preflight()

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

    @property
    def manifest(self) -> DeploymentManifest:
        """The backend's deployment, never a separately supplied one.

        Accepting a manifest here alongside the backend let a caller hand over a stale
        copy in which a retired key was still active, while the backend published to the
        rotated deployment. The lifecycle rule then read from one manifest and the chain
        from another.
        """
        return self.backend.manifest

    def _active_key(self, signed_report: Mapping[str, object]) -> Mapping[str, object]:
        """Resolve the verifying key from the manifest, never from the caller.

        Taking a published key as an argument put the lifecycle decision outside this
        class: a caller could hand over a retired key's record and publish under it, and
        only the command-line wrapper happened to object. The key a publication verifies
        against is now the deployment's active key, or the publication does not happen.
        """
        kid = signed_report.get("kid") if isinstance(signed_report, Mapping) else None
        active = self.manifest.active_key
        if kid != active.kid:
            listed = self.manifest.key(kid) if isinstance(kid, str) else None
            state = listed.state if listed is not None else "unknown"
            raise ValueError(
                f"report is signed by {kid!r} ({state}); this deployment's active "
                f"reporting key is {active.kid}"
            )
        return {
            "algorithm": "Ed25519",
            "kid": active.kid,
            "public_key": active.public_key,
            "version": 1,
        }

    def publish(
        self,
        signed_report: Mapping[str, object],
        *,
        report_uri: str,
    ) -> PublicationResult:
        """Publish an ordinary report; corrections use ``publish_correction``."""
        report = _verified_report(signed_report, self._active_key(signed_report))
        if report.get("correction_of") is not None:
            raise ValueError("correction reports require publish_correction")
        return self._publish(
            signed_report, report, report_uri=report_uri, correction_of=None
        )

    def publish_correction(
        self,
        signed_report: Mapping[str, object],
        *,
        report_uri: str,
    ) -> PublicationResult:
        """Publish a correction through the registry's distinct correction function."""
        report = _verified_report(signed_report, self._active_key(signed_report))
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
        # One publication is one phase. The endpoint's identity is proved at its start and
        # again before the result is written down, so a repointed endpoint cannot be read
        # under an identity that was verified for an earlier phase.
        self.backend.revalidate()
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
            # Validate the journalled bytes even though the report is already onchain.
            # Skipping this recorded whatever hash the journal claimed, so an unrelated
            # confirmed transaction could be entered in the transparency log as the
            # publication's provenance.
            prepared = _prepared_from_pending(
                pending,
                self.backend.identity(),
                self.backend.calldata(asset_key, report, report_uri, correction_of),
            )
            # Everything from here decides that a publication is real and records it, so
            # it all runs after the endpoint has been proved again — not on reads taken
            # when this branch was entered.
            self.backend.revalidate()
            self.ensure_onchain_match(asset_key, report, report_uri)
            # The publishing transaction is whichever one the registry emitted for this
            # asset and sequence under our lineage — not whichever one the journal names.
            found = self.backend.find_receipt(asset_key, sequence, correction_of)
            if found is None:
                state, receipt = self.backend.receipt_state(prepared.transaction_hash)
                if state != CONFIRMED:
                    raise PendingSubmission(
                        f"sequence {sequence} is onchain but its publication event is "
                        f"not available and {prepared.transaction_hash} is {state}"
                    )
                transaction_hash = prepared.transaction_hash
            else:
                transaction_hash, receipt = found
                if transaction_hash != prepared.transaction_hash:
                    raise SubmissionFailed(
                        f"sequence {sequence} was published by {transaction_hash}, but "
                        f"the journal records {prepared.transaction_hash}"
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
            prepared = _prepared_from_pending(
                pending,
                self.backend.identity(),
                self.backend.calldata(asset_key, report, report_uri, correction_of),
            )
            state, receipt = self.backend.receipt_state(prepared.transaction_hash)
            if state == MISSING:
                # Nothing on the chain holds it. The journal has the exact signed bytes,
                # so re-sending them is idempotent: identical nonce, identical hash, and
                # no second publication is possible. This is the difference between a
                # transaction dropped from a mempool and one to abandon forever.
                journalled_publisher = pending.get("publisher_address")
                if journalled_publisher != self.manifest.publisher_address:
                    # Reconciling a publication an old key already made is fine; sending
                    # one it never managed to is not. The registry would reject it, and
                    # only the owner can decide what replaces it.
                    raise PendingSubmission(
                        f"the journalled transaction was signed by "
                        f"{journalled_publisher}, which is no longer this deployment's "
                        f"publisher ({self.manifest.publisher_address}); it was never "
                        f"mined and cannot be sent now"
                    )
                self.backend.broadcast(prepared)
            if state != CONFIRMED:
                # Included but not yet buried deep enough is not a reason to send
                # anything; it is a reason to wait.
                try:
                    self.backend.wait_for_receipt(
                        prepared.transaction_hash, self.receipt_timeout
                    )
                except TimeExhausted as error:
                    raise PendingSubmission(
                        f"transaction {prepared.transaction_hash} is {state} and has not "
                        f"reached the required confirmation depth"
                    ) from error
            transaction_hash = prepared.transaction_hash
            receipt = self._settled_receipt(transaction_hash)
            if _receipt_status(receipt) != 1:
                self._clear_pending()
                raise SubmissionFailed(f"transaction {transaction_hash} failed")
            if self.backend.latest_sequence(asset_key) != sequence:
                raise SubmissionFailed(
                    "successful receipt did not advance registry sequence"
                )
            self.ensure_onchain_match(asset_key, report, report_uri)
            return self._finalize(
                signed_report,
                receipt,
                transaction_hash,
                correction_of,
                reconciled=True,
            )

        # Preparation comes first and is journalled only once it has produced signed
        # bytes. Everything that can refuse definitively — preflight, gas estimation, the
        # fee ceiling, signing itself — happens before anything is written down, because a
        # journal entry means "this may be on the wire" and a refusal never is.
        prepared = self.backend.prepare(asset_key, report, report_uri, correction_of)
        identity = self.backend.identity()
        self._write_pending(
            {
                **expected_pending,
                "chain_id": identity.chain_id,
                "nonce": prepared.nonce,
                "publisher_address": identity.publisher_address,
                "raw_transaction": prepared.raw.hex(),
                "registry_address": identity.registry_address,
                "transaction_hash": prepared.transaction_hash,
            }
        )
        transaction_hash = self.backend.broadcast(prepared)
        try:
            self.backend.wait_for_receipt(transaction_hash, self.receipt_timeout)
        except TimeExhausted as error:
            raise PendingSubmission(
                f"transaction {transaction_hash} remains pending"
            ) from error
        # The wait's own receipt is deliberately discarded: it came from the endpoint as
        # it was before the wait. Everything the receipt decides — including declaring
        # failure, which destroys the only record of what was sent — is decided from a
        # reading taken after the endpoint has been proved again.
        receipt = self._settled_receipt(transaction_hash)
        if _receipt_status(receipt) != 1:
            self._clear_pending()
            raise SubmissionFailed(f"transaction {transaction_hash} failed")
        if self.backend.latest_sequence(asset_key) != sequence:
            raise SubmissionFailed(
                "successful receipt did not advance registry sequence"
            )
        self.ensure_onchain_match(asset_key, report, report_uri)
        return self._finalize(
            signed_report,
            receipt,
            transaction_hash,
            correction_of,
            reconciled=False,
        )

    def _settled_receipt(self, transaction_hash: str) -> Mapping[str, object]:
        """Re-prove the endpoint, then read the receipt again and require it settled.

        Revalidating and then judging the receipt the *wait* returned proved nothing: that
        receipt came from the endpoint as it was before the wait, so the decision was
        still taken on the old endpoint's word. A receipt read after the identity is
        re-proved is the only one that may decide anything — and deciding failure discards
        the journal, which is why anything short of a confirmed receipt keeps it.
        """
        self.backend.revalidate()
        state, receipt = self.backend.receipt_state(transaction_hash)
        if state != CONFIRMED or receipt is None:
            raise PendingSubmission(
                f"transaction {transaction_hash} is {state} against the re-verified "
                f"endpoint; the journal is kept so it can be resolved"
            )
        return receipt

    def ensure_onchain_match(
        self, asset_key: bytes, report: Mapping[str, object], report_uri: str
    ) -> None:
        onchain = self.backend.get_report(asset_key, report["sequence"])
        # Matching content is not enough to claim a publication as ours. Another
        # authorized publisher, including one on a different lineage, can place an
        # identical payload at the same sequence; reconciliation would then adopt their
        # transaction and record its hash as though we had sent it.
        onchain_publisher = Web3.to_checksum_address(onchain.publisher)
        if onchain_publisher != self.manifest.publisher_address:
            # A rotation changes the publishing address while the registry preserves its
            # lineage, precisely so a publication made before the rotation is still ours.
            # Comparing addresses alone made every rotation orphan whatever was in flight.
            lineage = self.backend.publisher_lineage(onchain_publisher)
            if lineage != self.manifest.publisher_identity_address:
                raise SubmissionFailed(
                    f"sequence {onchain.sequence} was published by {onchain_publisher}, "
                    f"whose lineage {lineage} is not this deployment's "
                    f"{self.manifest.publisher_identity_address}"
                )
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

    def pending_transaction(self) -> str | None:
        """The transaction hash this client is waiting on, if any.

        A service needs to know whether a publication is unresolved *before* it starts
        another one, and asking that question should not require it to reach into the
        journal and reimplement its validation.
        """
        pending = self._load_pending()
        return None if pending is None else pending["transaction_hash"]

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
        if (
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


def _is_already_known(error: Web3RPCError) -> bool:
    """Whether a node is saying it already holds these exact bytes.

    There is no standard code for it, so the text is matched. The phrasings below are the
    ones Geth, Erigon, Nethermind and Besu use; anything else is re-raised rather than
    guessed at, because treating an unknown refusal as success would report a publication
    that never happened.
    """
    message = str(error).lower()
    return any(
        phrase in message
        for phrase in (
            "already known",
            "already imported",
            "alreadyknown",
            "known transaction",
            "transaction already in",
            "duplicate transaction",
        )
    )


def _is_nonce_too_low(error: Web3RPCError) -> bool:
    """Whether a node is saying this nonce has already been used."""
    message = str(error).lower()
    return "nonce too low" in message or "nonce is too low" in message


def _prepared_from_pending(
    pending: Mapping[str, object],
    identity: DeploymentIdentity,
    expected_calldata: bytes,
) -> PreparedTransaction:
    """Rebuild a journalled transaction and prove it is this deployment's.

    Recomputing the hash from the bytes only proves the two belong together; edit both
    and they still agree. So the transaction is decoded and read back: the chain it is
    bound to, the contract it calls, who signed it, that it moves no value, and the nonce
    it was recorded under. Any of those disagreeing means these bytes were meant for
    somewhere else — another registry on the same chain is the case that matters, because
    preflight would happily verify the *new* deployment and then broadcast a transaction
    aimed at the old one.
    """
    raw = pending.get("raw_transaction")
    nonce = pending.get("nonce")
    if not isinstance(raw, str) or _LOWER_HEX.fullmatch(raw) is None or not raw:
        raise PendingSubmission("pending submission has no signed transaction bytes")
    if type(nonce) is not int or nonce < 0:
        raise PendingSubmission("pending submission has an invalid nonce")
    encoded = bytes.fromhex(raw)
    recomputed = _transaction_hash(Web3.keccak(encoded))
    if recomputed != pending["transaction_hash"]:
        raise PendingSubmission(
            f"pending submission claims {pending['transaction_hash']} but its signed "
            f"bytes hash to {recomputed}"
        )
    try:
        decoded = decoded_transaction(encoded)
    except ValueError as error:
        raise PendingSubmission(str(error)) from error
    for field, found, expected in (
        ("chain", decoded["chain_id"], identity.chain_id),
        ("registry", decoded["to"], identity.registry_address),
        # Against the address the journal was written for, not against whoever publishes
        # today. A rotation between writing and recovering is legitimate, and the
        # signature cannot change to match it; whether the old signer may still *send* is
        # decided separately, at the point of rebroadcast.
        ("sender", decoded["sender"], pending.get("publisher_address")),
        ("nonce", decoded["nonce"], nonce),
    ):
        if found != expected:
            raise PendingSubmission(
                f"pending submission was signed for {field} {found}, but this "
                f"deployment is {expected}"
            )
    if decoded["value"] != 0:
        raise PendingSubmission("a publication must not transfer value")
    if decoded["data"] != expected_calldata:
        # Right chain, right registry, right signer, right nonce — and a different call.
        # Everything checked so far describes where the transaction goes, not what it
        # does, so without this a journal could carry any call the publisher ever signed.
        raise PendingSubmission(
            "the journalled transaction does not call this publication; its calldata "
            "is not the publish this report describes"
        )
    # Required, not "checked if present". An optional check is one an editor can delete,
    # and the same shape was already a defect once in the role addresses. The decode above
    # is the real defence — it reads the deployment out of the signature, which no journal
    # edit can influence — but a field worth recording is worth insisting on.
    for field, expected in (
        ("chain_id", identity.chain_id),
        ("registry_address", identity.registry_address),
    ):
        if pending.get(field) != expected:
            raise PendingSubmission(
                f"pending submission was journalled for {field} {pending.get(field)!r}, "
                f"but this deployment is {expected!r}"
            )
    return PreparedTransaction(transaction_hash=recomputed, raw=encoded, nonce=nonce)


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
