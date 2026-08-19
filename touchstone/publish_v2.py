"""Offline transaction preparation and reconciliation for Touchstone Registry v2."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
import time as clock
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.exceptions import (
    BlockNotFound,
    MethodUnavailable,
    TimeExhausted,
    TransactionNotFound,
    Web3RPCError,
)

from touchstone.deployment import (
    RegistryV2DeploymentManifest,
    runtime_bytecode_sha256,
)
from touchstone.keyring import PublisherKey, decoded_transaction, verification_keys
from touchstone.quantities import finite_positive
from touchstone.registry_v2 import (
    RegistryV2Error,
    V2_REGISTRY_ABI,
    attestation_from_report,
    sign_attestation,
    verify_attestation,
)
from touchstone.signing import frozen_snapshot, strict_json_loads, verify_signed_report
from touchstone.rpc_quorum import QuorumError, QuorumRPC


GAS_MARGIN_PERCENT = 25
FALLBACK_PRIORITY_FEE_WEI = 1_000_000_000
MISSING = "missing"
INCLUDED = "included"
CONFIRMED = "confirmed"
_CONFIRMATION_POLL_SECONDS = 1.0
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LOWER_HEX_32 = re.compile(r"[0-9a-f]{64}")

_REPORT_COMPONENTS = [
    {"name": "reportDigest", "type": "bytes32"},
    {"name": "policyId", "type": "bytes32"},
    {"name": "policyRoot", "type": "bytes32"},
    {"name": "controlSetRoot", "type": "bytes32"},
    {"name": "evidenceRoot", "type": "bytes32"},
    {"name": "epochKey", "type": "bytes32"},
    {"name": "status", "type": "uint8"},
    {"name": "observedAt", "type": "uint64"},
    {"name": "validUntil", "type": "uint64"},
    {"name": "publisher", "type": "address"},
    {"name": "sequence", "type": "uint64"},
    {"name": "parentDigest", "type": "bytes32"},
    {"name": "reportURI", "type": "string"},
]
_REPORT_INPUT_COMPONENTS = [
    {"name": "assetKey", "type": "bytes32"},
    *_REPORT_COMPONENTS[:6],
    *_REPORT_COMPONENTS[6:],
]
_REPORT_OUTPUT_TYPE = (
    "(bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint8,uint64,uint64,"
    "address,uint64,bytes32,string)"
)
REGISTRY_V2_BACKEND_ABI = [
    *V2_REGISTRY_ABI,
    {
        "inputs": [
            {"name": "correctedSequence", "type": "uint64"},
            {
                "components": _REPORT_INPUT_COMPONENTS,
                "name": "input",
                "type": "tuple",
            },
            {"name": "attestationSignature", "type": "bytes"},
        ],
        "name": "publishCorrection",
        "outputs": [],
        "stateMutability": "nonpayable",
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
        "inputs": [],
        "name": "legacyRegistry",
        "outputs": [{"name": "", "type": "address"}],
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
        "inputs": [{"name": "publisher", "type": "address"}],
        "name": "isPublisherAuthorized",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "publisher", "type": "address"}],
        "name": "publisherIdentity",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "assetKey", "type": "bytes32"}],
        "name": "latestSequence",
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "epochKey", "type": "bytes32"},
        ],
        "name": "epochSequence",
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "sequence", "type": "uint64"},
        ],
        "name": "getReport",
        "outputs": [{"components": _REPORT_COMPONENTS, "name": "", "type": "tuple"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "sequence", "type": "uint64"},
        ],
        "name": "correctionTarget",
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class RegistryV2PublicationError(RuntimeError):
    """A v2 publication could not be prepared or reconciled safely."""


class RegistryV2PreflightFailed(RegistryV2PublicationError):
    """The endpoint does not match the declared deployment."""


class RegistryV2FeeCeilingExceeded(RegistryV2PublicationError):
    """The prepared transaction could spend more than the declared ceiling."""


class RegistryV2ReconciliationFailed(RegistryV2PublicationError):
    """The stored report does not exactly match the signed attestation."""


class RegistryV2SubmissionFailed(RegistryV2PublicationError):
    """A signed v2 transaction failed or contradicted its acknowledgement."""


class TransactionSigner(Protocol):
    address: str

    def sign_transaction(self, transaction: dict[str, object]) -> tuple[str, bytes]: ...


@dataclass(frozen=True, slots=True)
class RelayerKey:
    """A gas-paying secp256k1 key with no registry authority requirement."""

    account: LocalAccount
    address: str

    @classmethod
    def from_hex(cls, encoded: str) -> RelayerKey:
        if not isinstance(encoded, str) or _LOWER_HEX_32.fullmatch(encoded) is None:
            raise ValueError(
                "relayer key must be exactly 64 lowercase hexadecimal characters"
            )
        account = Account.from_key(bytes.fromhex(encoded))
        return cls(account=account, address=Web3.to_checksum_address(account.address))

    def sign_transaction(self, transaction: dict[str, object]) -> tuple[str, bytes]:
        signed = self.account.sign_transaction(dict(transaction))
        return "0x" + signed.hash.hex().removeprefix("0x").lower(), bytes(
            signed.raw_transaction
        )


@dataclass(frozen=True, slots=True)
class RegistryV2Preflight:
    chain_id: int
    block_number: int
    registry_address: str
    registry_runtime_bytecode_sha256: str
    legacy_registry_address: str
    legacy_runtime_bytecode_sha256: str
    owner_address: str
    publisher_address: str
    publisher_identity_address: str
    relayer_address: str
    relayer_balance_wei: int


@dataclass(frozen=True, slots=True)
class PreparedRegistryV2Transaction:
    transaction_hash: str
    raw: bytes
    nonce: int
    gas: int
    maximum_fee_wei: int
    report_input: tuple[object, ...]
    attestation: Mapping[str, object]
    correction_of: int


@dataclass(frozen=True, slots=True)
class RegistryV2ChainReport:
    report_digest: str
    policy_id: str
    policy_root: str
    control_set_root: str
    evidence_root: str
    epoch_key: str
    status: int
    observed_at: int
    valid_until: int
    publisher: str
    sequence: int
    parent_digest: str
    report_uri: str
    correction_of: int


class RegistryV2Backend:
    """Verify, derive, sign, and reconcile v2 publications without broadcasting."""

    def __init__(
        self,
        manifest: RegistryV2DeploymentManifest,
        attestation_key: PublisherKey,
        *,
        relayer_key: TransactionSigner | None = None,
        web3: object | None = None,
        request_timeout: float = 30.0,
        quorum: QuorumRPC | None = None,
    ) -> None:
        if not manifest.is_active:
            raise RegistryV2PreflightFailed(
                f"{manifest.network} deployment is marked {manifest.deployment_state!r}"
            )
        if attestation_key.address != manifest.publisher_address:
            raise RegistryV2PreflightFailed(
                "attestation key is not the manifest publisher"
            )
        self.manifest = manifest
        self.attestation_key = attestation_key
        if relayer_key is None:
            raise RegistryV2PreflightFailed("v2 publication requires a dedicated relayer")
        self.relayer_key = relayer_key
        if self.relayer_key.address != manifest.relayer_address:
            raise RegistryV2PreflightFailed(
                "relayer key is not the manifest relayer"
            )
        if self.relayer_key.address in {
            manifest.owner_address,
            manifest.publisher_address,
            manifest.publisher_identity_address,
            manifest.deployer_address,
            manifest.operations_address,
        }:
            raise RegistryV2PreflightFailed(
                "v2 relayer must differ from every privileged or funding identity"
            )
        if not manifest.is_local and quorum is None:
            raise RegistryV2PreflightFailed(
                "public v2 publication requires independent RPC quorum"
            )
        self.web3 = web3 or Web3(
            Web3.HTTPProvider(
                manifest.rpc_url,
                request_kwargs={
                    "timeout": finite_positive(request_timeout, "request_timeout")
                },
                exception_retry_configuration=None,
            )
        )
        self.contract = self.web3.eth.contract(
            address=manifest.registry_address, abi=REGISTRY_V2_BACKEND_ABI
        )
        self.encoder = Web3().eth.contract(
            address=manifest.registry_address, abi=REGISTRY_V2_BACKEND_ABI
        )
        self.quorum = quorum
        self._preflight: RegistryV2Preflight | None = None

    def preflight(self) -> RegistryV2Preflight:
        manifest = self.manifest
        try:
            chain_id = int(self.web3.eth.chain_id)
            block_number = int(self.web3.eth.block_number)
            v2_code = bytes(self.web3.eth.get_code(manifest.registry_address))
            legacy_code = bytes(
                self.web3.eth.get_code(manifest.legacy_registry_address)
            )
            expected_chain_id = int(self.contract.functions.expectedChainId().call())
            legacy_registry = Web3.to_checksum_address(
                self.contract.functions.legacyRegistry().call()
            )
            owner = Web3.to_checksum_address(self.contract.functions.owner().call())
            authorized = bool(
                self.contract.functions.isPublisherAuthorized(
                    self.attestation_key.address
                ).call()
            )
            identity = Web3.to_checksum_address(
                self.contract.functions.publisherIdentity(
                    self.attestation_key.address
                ).call()
            )
            relayer_balance = int(self.web3.eth.get_balance(self.relayer_key.address))
        except (OSError, TypeError, ValueError, Web3RPCError) as error:
            raise RegistryV2PreflightFailed(
                f"v2 deployment preflight could not read the endpoint: {error}"
            ) from error
        if self.quorum is not None:
            self._quorum_preflight(v2_code, legacy_code)
        if chain_id != manifest.chain_id:
            raise RegistryV2PreflightFailed(
                f"endpoint reports chain {chain_id}, manifest declares {manifest.chain_id}"
            )
        if expected_chain_id != manifest.chain_id:
            raise RegistryV2PreflightFailed(
                "v2 registry expectedChainId does not match the manifest"
            )
        if not v2_code or (
            runtime_bytecode_sha256(v2_code)
            != manifest.registry_runtime_bytecode_sha256
        ):
            raise RegistryV2PreflightFailed(
                "v2 registry runtime bytecode does not match the deployment"
            )
        if not legacy_code or (
            runtime_bytecode_sha256(legacy_code)
            != manifest.legacy_registry_runtime_bytecode_sha256
        ):
            raise RegistryV2PreflightFailed(
                "legacy registry runtime bytecode does not match the manifest"
            )
        if legacy_registry != manifest.legacy_registry_address:
            raise RegistryV2PreflightFailed(
                "v2 registry legacyRegistry does not match the manifest"
            )
        if owner != manifest.owner_address:
            raise RegistryV2PreflightFailed(
                "v2 registry owner does not match deployment"
            )
        if not authorized:
            raise RegistryV2PreflightFailed(
                "manifest publisher is not authorized by the v2 registry"
            )
        if identity != manifest.publisher_identity_address:
            raise RegistryV2PreflightFailed(
                "v2 publisher identity does not match the manifest lineage"
            )
        if relayer_balance <= 0:
            raise RegistryV2PreflightFailed("relayer has no gas balance")
        result = RegistryV2Preflight(
            chain_id=chain_id,
            block_number=block_number,
            registry_address=manifest.registry_address,
            registry_runtime_bytecode_sha256=runtime_bytecode_sha256(v2_code),
            legacy_registry_address=legacy_registry,
            legacy_runtime_bytecode_sha256=runtime_bytecode_sha256(legacy_code),
            owner_address=owner,
            publisher_address=self.attestation_key.address,
            publisher_identity_address=identity,
            relayer_address=self.relayer_key.address,
            relayer_balance_wei=relayer_balance,
        )
        self._preflight = result
        return result

    def revalidate(self) -> RegistryV2Preflight:
        self._preflight = None
        return self.preflight()

    def prepare(
        self,
        signed_report: bytes | str | Mapping[str, object],
        *,
        report_uri: str,
        correction_of: int = 0,
    ) -> PreparedRegistryV2Transaction:
        preflight = self.revalidate()
        try:
            if isinstance(signed_report, (bytes, str)):
                signed_report = strict_json_loads(signed_report)
            else:
                signed_report = frozen_snapshot(signed_report, "signed_report")
            if not isinstance(signed_report, Mapping):
                raise RegistryV2PublicationError(
                    "signed report envelope must be an object"
                )
            active = self.manifest.active_key
            if signed_report.get("kid") != active.kid:
                raise RegistryV2PublicationError(
                    "new v2 reports require the active reporting key"
                )
            report = verify_signed_report(
                signed_report, verification_keys(self.manifest)
            )
        except (InvalidSignature, TypeError, ValueError) as error:
            raise RegistryV2PublicationError(
                f"Ed25519 report verification failed: {error}"
            ) from error
        if not isinstance(report, Mapping):
            raise RegistryV2PublicationError("signed report must contain an object")
        asset_key_text = report.get("asset_key")
        if not isinstance(asset_key_text, str) or not asset_key_text:
            raise RegistryV2PublicationError("report asset_key must be nonempty text")
        asset_key = bytes(Web3.keccak(text=asset_key_text))
        sequence = report.get("sequence")
        if type(sequence) is not int or sequence < 1:
            raise RegistryV2PublicationError("report sequence must be positive")
        report_correction = report.get("correction_of")
        expected_correction = 0 if report_correction is None else report_correction
        if type(expected_correction) is not int or expected_correction < 0:
            raise RegistryV2PublicationError("report correction_of is invalid")
        if correction_of != expected_correction:
            raise RegistryV2PublicationError(
                "requested correction target does not match the signed report"
            )
        latest = int(
            self._read_function(
                self.contract.functions.latestSequence(asset_key), "uint64"
            )
        )
        if sequence != latest + 1:
            raise RegistryV2PublicationError(
                f"report sequence {sequence} is not next after onchain {latest}"
            )
        parent_digest = (
            "0" * 64
            if latest == 0
            else _hex32(
                self._read_function(
                    self.contract.functions.getReport(asset_key, latest),
                    _REPORT_OUTPUT_TYPE,
                )[0]
            )
        )
        try:
            unsigned = attestation_from_report(
                report,
                publisher=self.attestation_key.address,
                parent_digest=parent_digest,
                correction_of=correction_of,
                report_uri=report_uri,
                chain_id=self.manifest.chain_id,
                verifying_contract=self.manifest.registry_address,
            )
            attestation = sign_attestation(self.attestation_key.account.key, **unsigned)
        except RegistryV2Error as error:
            raise RegistryV2PublicationError(str(error)) from error
        input_values = report_input(attestation)
        epoch_key = input_values[6]
        if correction_of:
            if correction_of > latest:
                raise RegistryV2PublicationError(
                    "correction target is not an existing report"
                )
            corrected = self._read_function(
                self.contract.functions.getReport(asset_key, correction_of),
                _REPORT_OUTPUT_TYPE,
            )
            if _hex32(corrected[5]) != _hex32(epoch_key):
                raise RegistryV2PublicationError(
                    "correction epoch does not match its target"
                )
        else:
            epoch_sequence = int(
                self._read_function(
                    self.contract.functions.epochSequence(asset_key, epoch_key),
                    "uint64",
                )
            )
            if epoch_sequence != 0:
                raise RegistryV2PublicationError(
                    f"epoch is already published at sequence {epoch_sequence}"
                )
        calldata = self.calldata(attestation)
        base_transaction: dict[str, object] = {
            "from": self.relayer_key.address,
            "to": self.manifest.registry_address,
            "value": 0,
            "data": calldata,
        }
        try:
            estimate = int(self.web3.eth.estimate_gas(base_transaction))
            gas = (estimate * (100 + GAS_MARGIN_PERCENT) + 99) // 100
            fee_fields = self._fee_fields()
            nonce = self._pending_nonce()
        except (OSError, QuorumError, TypeError, ValueError, Web3RPCError) as error:
            raise RegistryV2PublicationError(
                f"transaction preflight failed: {error}"
            ) from error
        fee_per_gas = fee_fields.get("maxFeePerGas", fee_fields.get("gasPrice"))
        assert fee_per_gas is not None
        maximum_fee = gas * fee_per_gas
        ceiling = self.manifest.max_fee_wei
        if ceiling is None:
            raise RegistryV2FeeCeilingExceeded("manifest has no v2 fee ceiling")
        if maximum_fee > ceiling:
            raise RegistryV2FeeCeilingExceeded(
                f"maximum fee {maximum_fee} exceeds manifest ceiling {ceiling}"
            )
        if maximum_fee > preflight.relayer_balance_wei:
            raise RegistryV2FeeCeilingExceeded(
                "relayer balance is below the transaction maximum fee"
            )
        transaction = {
            "chainId": self.manifest.chain_id,
            "nonce": nonce,
            "to": self.manifest.registry_address,
            "value": 0,
            "data": calldata,
            "gas": gas,
            **fee_fields,
        }
        transaction_hash, raw = self.relayer_key.sign_transaction(transaction)
        self._verify_signed_transaction(
            transaction_hash, raw, nonce=nonce, calldata=calldata
        )
        return PreparedRegistryV2Transaction(
            transaction_hash=transaction_hash,
            raw=raw,
            nonce=nonce,
            gas=gas,
            maximum_fee_wei=maximum_fee,
            report_input=input_values,
            attestation=attestation,
            correction_of=correction_of,
        )

    def reconcile(self, attestation: Mapping[str, object]) -> RegistryV2ChainReport:
        self.revalidate()
        try:
            verify_attestation(attestation)
        except RegistryV2Error as error:
            raise RegistryV2ReconciliationFailed(str(error)) from error
        if attestation.get("chain_id") != self.manifest.chain_id or (
            attestation.get("verifying_contract") != self.manifest.registry_address
        ):
            raise RegistryV2ReconciliationFailed(
                "attestation targets a different deployment"
            )
        asset_key = bytes.fromhex(str(attestation["asset_key"]))
        sequence = int(attestation["sequence"])
        stored = self._read_function(
            self.contract.functions.getReport(asset_key, sequence), _REPORT_OUTPUT_TYPE
        )
        expected = report_input(attestation)[1:]
        normalized = (
            *(_hex32(value) for value in stored[:6]),
            int(stored[6]),
            int(stored[7]),
            int(stored[8]),
            Web3.to_checksum_address(stored[9]),
            int(stored[10]),
            _hex32(stored[11]),
            str(stored[12]),
        )
        expected_normalized = (
            *(_hex32(value) for value in expected[:6]),
            int(expected[6]),
            int(expected[7]),
            int(expected[8]),
            Web3.to_checksum_address(expected[9]),
            int(expected[10]),
            _hex32(expected[11]),
            str(expected[12]),
        )
        if normalized != expected_normalized:
            raise RegistryV2ReconciliationFailed(
                "onchain report does not match the signed attestation"
            )
        lineage = Web3.to_checksum_address(
            str(
                self._read_function(
                    self.contract.functions.publisherIdentity(normalized[9]), "address"
                )
            )
        )
        if lineage != self.manifest.publisher_identity_address:
            raise RegistryV2ReconciliationFailed(
                "onchain report publisher belongs to another lineage"
            )
        correction_of = int(attestation["correction_of"])
        stored_correction = int(
            self._read_function(
                self.contract.functions.correctionTarget(asset_key, sequence), "uint64"
            )
        )
        if stored_correction != correction_of:
            raise RegistryV2ReconciliationFailed(
                "onchain correction target does not match the signed attestation"
            )
        return RegistryV2ChainReport(
            report_digest=normalized[0],
            policy_id=normalized[1],
            policy_root=normalized[2],
            control_set_root=normalized[3],
            evidence_root=normalized[4],
            epoch_key=normalized[5],
            status=normalized[6],
            observed_at=normalized[7],
            valid_until=normalized[8],
            publisher=normalized[9],
            sequence=normalized[10],
            parent_digest=normalized[11],
            report_uri=normalized[12],
            correction_of=stored_correction,
        )

    def calldata(self, attestation: Mapping[str, object]) -> bytes:
        """Encode exactly the EIP-712-authorized call for journal recovery."""
        try:
            verify_attestation(attestation)
        except RegistryV2Error as error:
            raise RegistryV2PublicationError(str(error)) from error
        if attestation.get("chain_id") != self.manifest.chain_id or (
            attestation.get("verifying_contract") != self.manifest.registry_address
        ):
            raise RegistryV2PublicationError(
                "attestation targets a different deployment"
            )
        values = report_input(attestation)
        signature = bytes.fromhex(str(attestation["signature"]))
        correction_of = int(attestation["correction_of"])
        function = (
            self.encoder.functions.publishCorrection(
                correction_of, values, signature
            )
            if correction_of
            else self.encoder.functions.publish(values, signature)
        )
        return bytes.fromhex(function._encode_transaction_data()[2:])

    def broadcast(self, prepared: PreparedRegistryV2Transaction) -> str:
        """Broadcast exact signed bytes, accepting only their predetermined hash."""
        try:
            acknowledged = _transaction_hash(
                self.web3.eth.send_raw_transaction(prepared.raw)
            )
        except Web3RPCError as error:
            message = str(error).lower()
            if any(
                phrase in message
                for phrase in (
                    "already known",
                    "already imported",
                    "alreadyknown",
                    "known transaction",
                    "transaction already in",
                    "duplicate transaction",
                )
            ):
                return prepared.transaction_hash
            if "nonce too low" in message or "nonce is too low" in message:
                state, _ = self.receipt_state(prepared.transaction_hash)
                if state != MISSING:
                    return prepared.transaction_hash
            raise RegistryV2SubmissionFailed(
                f"v2 transaction broadcast failed: {error}"
            ) from error
        if acknowledged != prepared.transaction_hash:
            raise RegistryV2SubmissionFailed(
                f"node acknowledged {acknowledged}, expected "
                f"{prepared.transaction_hash}"
            )
        return acknowledged

    def receipt_state(
        self, transaction_hash: str
    ) -> tuple[str, Mapping[str, object] | None]:
        try:
            receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
        except TransactionNotFound:
            return MISSING, None
        return (CONFIRMED if self._confirmed(receipt) else INCLUDED), receipt

    def wait_for_receipt(
        self, transaction_hash: str, timeout: float
    ) -> Mapping[str, object]:
        timeout = finite_positive(timeout, "receipt_timeout")
        deadline = clock.monotonic() + timeout
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
                raise RegistryV2SubmissionFailed(
                    f"transaction {transaction_hash} left the chain before confirming"
                ) from error
        return receipt

    def _confirmed(self, receipt: Mapping[str, object]) -> bool:
        block_number = receipt.get("blockNumber")
        if block_number is None:
            return False
        depth = int(self.web3.eth.block_number) - int(block_number) + 1
        if depth < self.manifest.confirmations:
            return False
        try:
            canonical = self.web3.eth.get_block(int(block_number))
        except BlockNotFound as error:
            raise RegistryV2SubmissionFailed(
                f"block {block_number} is no longer retrievable"
            ) from error
        if bytes(canonical["hash"]) != bytes(receipt["blockHash"]):
            raise RegistryV2SubmissionFailed(
                f"block {block_number} was reorganised away from the receipt's block"
            )
        return True

    def _quorum_preflight(self, v2_code: bytes, legacy_code: bytes) -> None:
        assert self.quorum is not None
        try:
            chain_id = _quorum_quantity(
                self.quorum.call("eth_chainId", []), "chain id"
            )
            remote_v2 = _quorum_bytes(
                self.quorum.call(
                    "eth_getCode", [self.manifest.registry_address, "latest"]
                ),
                "v2 runtime bytecode",
            )
            remote_legacy = _quorum_bytes(
                self.quorum.call(
                    "eth_getCode",
                    [self.manifest.legacy_registry_address, "latest"],
                ),
                "legacy runtime bytecode",
            )
            expected_chain = int(
                self._quorum_function(
                    self.contract.functions.expectedChainId(), "uint256"
                )
            )
            legacy = Web3.to_checksum_address(
                str(
                    self._quorum_function(
                        self.contract.functions.legacyRegistry(), "address"
                    )
                )
            )
            owner = Web3.to_checksum_address(
                str(self._quorum_function(self.contract.functions.owner(), "address"))
            )
            authorized = bool(
                self._quorum_function(
                    self.contract.functions.isPublisherAuthorized(
                        self.attestation_key.address
                    ),
                    "bool",
                )
            )
            identity = Web3.to_checksum_address(
                str(
                    self._quorum_function(
                        self.contract.functions.publisherIdentity(
                            self.attestation_key.address
                        ),
                        "address",
                    )
                )
            )
            relayer_balance = _quorum_quantity(
                self.quorum.call(
                    "eth_getBalance", [self.relayer_key.address, "latest"]
                ),
                "relayer balance",
            )
        except (QuorumError, TypeError, ValueError) as error:
            raise RegistryV2PreflightFailed(
                f"independent RPC quorum failed: {error}"
            ) from error
        expected = self.manifest
        if chain_id != expected.chain_id:
            raise RegistryV2PreflightFailed(
                f"quorum endpoint reports chain {chain_id}"
            )
        if remote_v2 != v2_code or remote_legacy != legacy_code:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints disagree on runtime bytecode"
            )
        if expected_chain != expected.chain_id:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints disagree on registry chain"
            )
        if legacy != expected.legacy_registry_address:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints disagree on legacy registry"
            )
        if owner != expected.owner_address:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints disagree on registry owner"
            )
        if not authorized:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints report publisher unauthorized"
            )
        if identity != expected.publisher_identity_address:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints disagree on publisher lineage"
            )
        if relayer_balance <= 0:
            raise RegistryV2PreflightFailed(
                "independent RPC endpoints report no relayer gas"
            )

    def _read_function(self, function: object, output_type: str) -> object:
        if self.quorum is None:
            return function.call()
        try:
            return self._quorum_function(function, output_type)
        except (QuorumError, TypeError, ValueError) as error:
            raise RegistryV2PreflightFailed(
                f"independent RPC quorum read failed: {error}"
            ) from error

    def _quorum_function(self, function: object, output_type: str) -> object:
        assert self.quorum is not None
        raw = self.quorum.call(
            "eth_call",
            [
                {
                    "to": self.manifest.registry_address,
                    "data": function._encode_transaction_data(),
                },
                "latest",
            ],
        )
        return self.web3.codec.decode(
            [output_type], _quorum_bytes(raw, "eth_call result")
        )[0]

    def _fee_fields(self) -> dict[str, int]:
        block = self.web3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas")
        if base_fee is None:
            return {"gasPrice": int(self.web3.eth.gas_price)}
        try:
            priority_fee = int(self.web3.eth.max_priority_fee)
        except (MethodUnavailable, ValueError, Web3RPCError):
            priority_fee = FALLBACK_PRIORITY_FEE_WEI
        return {
            "maxFeePerGas": 2 * int(base_fee) + priority_fee,
            "maxPriorityFeePerGas": priority_fee,
        }

    def _pending_nonce(self) -> int:
        if self.quorum is not None:
            return _quorum_quantity(
                self.quorum.call(
                    "eth_getTransactionCount",
                    [self.relayer_key.address, "pending"],
                ),
                "relayer pending nonce",
            )
        return int(
            self.web3.eth.get_transaction_count(self.relayer_key.address, "pending")
        )

    def _verify_signed_transaction(
        self,
        transaction_hash: str,
        raw: bytes,
        *,
        nonce: int,
        calldata: bytes,
    ) -> None:
        expected_hash = "0x" + Web3.keccak(raw).hex().removeprefix("0x").lower()
        if transaction_hash != expected_hash:
            raise RegistryV2PublicationError(
                "relayer returned a transaction hash that does not match its raw bytes"
            )
        try:
            decoded = decoded_transaction(raw)
        except ValueError as error:
            raise RegistryV2PublicationError(
                f"signed transaction could not be verified: {error}"
            ) from error
        expected = {
            "chain_id": self.manifest.chain_id,
            "nonce": nonce,
            "to": self.manifest.registry_address,
            "value": 0,
            "data": calldata,
            "sender": self.relayer_key.address,
        }
        if decoded != expected:
            raise RegistryV2PublicationError(
                "signed transaction fields do not match the prepared v2 publication"
            )


def report_input(attestation: Mapping[str, object]) -> tuple[object, ...]:
    return (
        bytes.fromhex(str(attestation["asset_key"])),
        bytes.fromhex(str(attestation["report_digest"])),
        bytes.fromhex(str(attestation["policy_id"])),
        bytes.fromhex(str(attestation["policy_root"])),
        bytes.fromhex(str(attestation["control_set_root"])),
        bytes.fromhex(str(attestation["evidence_root"])),
        bytes.fromhex(str(attestation["epoch_key"])),
        int(attestation["status"]),
        int(attestation["observed_at"]),
        int(attestation["valid_until"]),
        Web3.to_checksum_address(str(attestation["publisher"])),
        int(attestation["sequence"]),
        bytes.fromhex(str(attestation["parent_digest"])),
        str(attestation["report_uri"]),
    )


def _hex32(value: object) -> str:
    if isinstance(value, bytes) and len(value) == 32:
        return value.hex()
    text = value.hex() if hasattr(value, "hex") else str(value)
    normalized = text.removeprefix("0x").lower()
    if _DIGEST.fullmatch(normalized) is None:
        raise RegistryV2ReconciliationFailed("onchain bytes32 value is invalid")
    return normalized


def _transaction_hash(value: object) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    normalized = "0x" + text.removeprefix("0x").lower()
    if re.fullmatch(r"0x[0-9a-f]{64}", normalized) is None:
        raise RegistryV2SubmissionFailed("transaction hash must be 32 bytes")
    return normalized


def _quorum_quantity(value: object, context: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"0x[0-9a-fA-F]+", value) is None:
        raise ValueError(f"quorum {context} is not a hex quantity")
    return int(value, 16)


def _quorum_bytes(value: object, context: str) -> bytes:
    if not isinstance(value, str) or re.fullmatch(r"0x[0-9a-fA-F]*", value) is None:
        raise ValueError(f"quorum {context} is not hex bytes")
    return bytes.fromhex(value[2:])
