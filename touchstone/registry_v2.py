"""EIP-712 attestations and calldata for Touchstone Registry v2."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import re

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.exceptions import BadSignature
from web3 import Web3

from touchstone.signing import canonical_json_bytes


ATTESTATION_FIELDS = frozenset(
    {
        "report_digest",
        "asset_key",
        "policy_id",
        "policy_root",
        "control_set_root",
        "evidence_root",
        "approval_digest",
        "epoch_key",
        "status",
        "observed_at",
        "publisher",
        "valid_until",
        "sequence",
        "parent_digest",
        "correction_of",
        "report_uri",
        "signature",
    }
)
ATTESTATION_DOMAIN = {"name": "Touchstone Registry", "version": "2"}
ATTESTATION_TYPES = {
    "Attestation": [
        {"name": "assetKey", "type": "bytes32"},
        {"name": "reportDigest", "type": "bytes32"},
        {"name": "policyId", "type": "bytes32"},
        {"name": "policyRoot", "type": "bytes32"},
        {"name": "controlSetRoot", "type": "bytes32"},
        {"name": "evidenceRoot", "type": "bytes32"},
        {"name": "approvalDigest", "type": "bytes32"},
        {"name": "epochKey", "type": "bytes32"},
        {"name": "status", "type": "uint8"},
        {"name": "observedAt", "type": "uint64"},
        {"name": "validUntil", "type": "uint64"},
        {"name": "publisher", "type": "address"},
        {"name": "sequence", "type": "uint64"},
        {"name": "parentDigest", "type": "bytes32"},
        {"name": "correctionOf", "type": "uint64"},
        {"name": "reportURI", "type": "string"},
    ]
}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SIGNATURE = re.compile(r"[0-9a-f]{130}")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_STATUS = {
    "CONFIRMED": 0,
    "STALE": 1,
    "INCONSISTENT": 2,
    "UNVERIFIABLE": 3,
}


class RegistryV2Error(ValueError):
    """A v2 attestation or publication input is not valid."""


def report_digest(report: Mapping[str, object]) -> str:
    """Hash the exact canonical report object the Ed25519 envelope signs."""
    if not isinstance(report, Mapping):
        raise RegistryV2Error("report must be a mapping")
    return hashlib.sha256(canonical_json_bytes(dict(report))).hexdigest()


def registry_asset_key(report_asset_key: str) -> str:
    """Derive the registry bytes32 key from the full report asset identifier."""
    if not isinstance(report_asset_key, str) or not report_asset_key:
        raise RegistryV2Error("report asset_key must be nonempty text")
    return Web3.keccak(text=report_asset_key).hex().removeprefix("0x")


def policy_id_digest(policy_id: str, version: int) -> str:
    """Derive the bytes32 identity for one immutable policy version."""
    if not isinstance(policy_id, str) or re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", policy_id
    ) is None:
        raise RegistryV2Error("policy_id must be lowercase words joined by hyphens")
    if type(version) is not int or version < 1:
        raise RegistryV2Error("policy_version must be a positive integer")
    return Web3.keccak(text=f"{policy_id}:{version}").hex().removeprefix("0x")


def attestation_from_report(
    report: Mapping[str, object],
    *,
    publisher: str,
    parent_digest: str,
    correction_of: int,
    report_uri: str,
    chain_id: int,
    verifying_contract: str,
) -> dict[str, object]:
    """Derive every onchain attestation field from one policy report."""
    if not isinstance(report, Mapping):
        raise RegistryV2Error("report must be a mapping")
    policy = report.get("policy")
    if not isinstance(policy, Mapping):
        raise RegistryV2Error("Registry v2 requires a policy-bound report")
    policy_id = policy.get("policy_id")
    policy_version = policy.get("policy_version")
    policy_root = policy.get("policy_digest")
    asset_key = report.get("asset_key")
    epoch_id = report.get("epoch_id")
    state = report.get("state")
    if not isinstance(asset_key, str):
        raise RegistryV2Error("report asset_key must be text")
    if not isinstance(epoch_id, str) or not epoch_id:
        raise RegistryV2Error("report epoch_id must be nonempty text")
    if state not in _STATUS:
        raise RegistryV2Error("report state is not a Registry v2 status")
    value = {
        "asset_key": registry_asset_key(asset_key),
        "report_digest": report_digest(report),
        "policy_id": policy_id_digest(policy_id, policy_version),
        "policy_root": policy_root,
        "control_set_root": report.get("control_set_root"),
        "evidence_root": report.get("evidence_root"),
        "approval_digest": report.get("approval_ledger_sha256"),
        "epoch_key": Web3.keccak(text=epoch_id).hex().removeprefix("0x"),
        "status": _STATUS[state],
        "observed_at": _unix_timestamp(report.get("observed_at"), "observed_at"),
        "valid_until": _unix_timestamp(report.get("valid_until"), "valid_until"),
        "publisher": publisher,
        "sequence": report.get("sequence"),
        "parent_digest": parent_digest,
        "correction_of": correction_of,
        "report_uri": report_uri,
        "chain_id": chain_id,
        "verifying_contract": verifying_contract,
    }
    _validate_fields(value, include_signature=False)
    return value


def attestation_typed_data(value: Mapping[str, object]) -> dict[str, object]:
    """Return the EIP-712 payload for an unsigned v2 attestation."""
    _validate_fields(value, include_signature=False)
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **ATTESTATION_TYPES,
        },
        "primaryType": "Attestation",
        "domain": {
            **ATTESTATION_DOMAIN,
            "chainId": value["chain_id"],
            "verifyingContract": value["verifying_contract"],
        },
        "message": {
            "assetKey": "0x" + value["asset_key"],
            "reportDigest": "0x" + value["report_digest"],
            "policyId": "0x" + value["policy_id"],
            "policyRoot": "0x" + value["policy_root"],
            "controlSetRoot": "0x" + value["control_set_root"],
            "evidenceRoot": "0x" + value["evidence_root"],
            "approvalDigest": "0x" + value["approval_digest"],
            "epochKey": "0x" + value["epoch_key"],
            "status": value["status"],
            "observedAt": value["observed_at"],
            "validUntil": value["valid_until"],
            "publisher": value["publisher"],
            "sequence": value["sequence"],
            "parentDigest": "0x" + value["parent_digest"],
            "correctionOf": value["correction_of"],
            "reportURI": value["report_uri"],
        },
    }


def sign_attestation(
    private_key: str | bytes,
    *,
    asset_key: str,
    report_digest: str,
    policy_id: str,
    policy_root: str,
    control_set_root: str,
    evidence_root: str,
    approval_digest: str,
    epoch_key: str,
    status: int,
    observed_at: int,
    publisher: str,
    valid_until: int,
    sequence: int,
    parent_digest: str,
    correction_of: int,
    report_uri: str,
    chain_id: int,
    verifying_contract: str,
) -> dict[str, object]:
    """Sign the v2 attestation with explicit secp256k1 key material."""
    value = {
        "asset_key": asset_key,
        "report_digest": report_digest,
        "policy_id": policy_id,
        "policy_root": policy_root,
        "control_set_root": control_set_root,
        "evidence_root": evidence_root,
        "approval_digest": approval_digest,
        "epoch_key": epoch_key,
        "status": status,
        "observed_at": observed_at,
        "publisher": publisher,
        "valid_until": valid_until,
        "sequence": sequence,
        "parent_digest": parent_digest,
        "correction_of": correction_of,
        "report_uri": report_uri,
        "chain_id": chain_id,
        "verifying_contract": verifying_contract,
    }
    try:
        account = Account.from_key(private_key)
        signature = Account.sign_message(
            encode_typed_data(full_message=attestation_typed_data(value)), private_key
        ).signature
    except (TypeError, ValueError) as error:
        raise RegistryV2Error(f"attestation signing failed: {error}") from error
    if account.address.lower() != publisher.lower():
        raise RegistryV2Error("publisher does not match the signing key")
    return {**value, "signature": signature.hex()}


def verify_attestation(value: Mapping[str, object]) -> str:
    """Recover and return the v2 attestation publisher address."""
    _validate_fields(value, include_signature=True)
    signature_text = value["signature"]
    if not isinstance(signature_text, str) or _SIGNATURE.fullmatch(signature_text) is None:
        raise RegistryV2Error("attestation signature must be lowercase hexadecimal")
    try:
        recovered = Account.recover_message(
            encode_typed_data(full_message=attestation_typed_data(value)),
            signature=bytes.fromhex(signature_text),
        )
    except (BadSignature, TypeError, ValueError) as error:
        raise RegistryV2Error("attestation signature is invalid") from error
    if recovered.lower() != value["publisher"].lower():
        raise RegistryV2Error("attestation publisher does not match its signature")
    return recovered


def attestation_eip712_digest(value: Mapping[str, object]) -> str:
    """Return the digest that RegistryV2 passes to ``ecrecover``."""
    signable = encode_typed_data(full_message=attestation_typed_data(value))
    digest = Web3.keccak(b"\x19" + signable.version + signable.header + signable.body)
    return digest.hex().removeprefix("0x")


V2_REGISTRY_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "assetKey", "type": "bytes32"},
                    {"name": "reportDigest", "type": "bytes32"},
                    {"name": "policyId", "type": "bytes32"},
                    {"name": "policyRoot", "type": "bytes32"},
                    {"name": "controlSetRoot", "type": "bytes32"},
                    {"name": "evidenceRoot", "type": "bytes32"},
                    {"name": "approvalDigest", "type": "bytes32"},
                    {"name": "epochKey", "type": "bytes32"},
                    {"name": "status", "type": "uint8"},
                    {"name": "observedAt", "type": "uint64"},
                    {"name": "validUntil", "type": "uint64"},
                    {"name": "publisher", "type": "address"},
                    {"name": "sequence", "type": "uint64"},
                    {"name": "parentDigest", "type": "bytes32"},
                    {"name": "reportURI", "type": "string"},
                ],
                "name": "input",
                "type": "tuple",
            },
            {"name": "attestationSignature", "type": "bytes"},
        ],
        "name": "publish",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def publish_calldata(
    *,
    registry_address: str,
    report_input: tuple[object, ...],
    signature: str,
) -> bytes:
    """Encode the exact v2 relayer call without contacting a chain."""
    if not isinstance(signature, str) or _SIGNATURE.fullmatch(signature) is None:
        raise RegistryV2Error("attestation signature must be lowercase hexadecimal")
    contract = Web3().eth.contract(
        address=Web3.to_checksum_address(registry_address), abi=V2_REGISTRY_ABI
    )
    if len(report_input) != 15:
        raise RegistryV2Error("v2 report input must contain 15 fields")
    normalized_input = (
        _bytes32(report_input[0], "asset_key"),
        _bytes32(report_input[1], "report_digest"),
        _bytes32(report_input[2], "policy_id"),
        _bytes32(report_input[3], "policy_root"),
        _bytes32(report_input[4], "control_set_root"),
        _bytes32(report_input[5], "evidence_root"),
        _bytes32(report_input[6], "approval_digest"),
        _bytes32(report_input[7], "epoch_key"),
        report_input[8],
        report_input[9],
        report_input[10],
        # The struct's order is the authority. When `approvalDigest` was inserted at field
        # seven this mapping was updated above and not below, so the publisher lived at the
        # sequence's index and a checksum was attempted on the integer 3 — caught by the
        # calldata test rather than by a revert on a live chain, which is the whole reason
        # that test builds real transaction bytes.
        Web3.to_checksum_address(report_input[11]),
        report_input[12],
        _bytes32(report_input[13], "parent_digest"),
        report_input[14],
    )
    try:
        return bytes(
            bytes.fromhex(
                contract.functions.publish(normalized_input, bytes.fromhex(signature))
                ._encode_transaction_data()[2:]
            )
        )
    except (TypeError, ValueError) as error:
        raise RegistryV2Error(f"v2 publication input is invalid: {error}") from error


def _validate_fields(value: Mapping[str, object], *, include_signature: bool) -> None:
    if not isinstance(value, Mapping):
        raise RegistryV2Error("attestation must be a mapping")
    expected = (
        ATTESTATION_FIELDS
        if include_signature
        else ATTESTATION_FIELDS - {"signature"}
    )
    expected |= {"chain_id", "verifying_contract"}
    supplied = set(value)
    missing = expected - supplied
    if missing:
        raise RegistryV2Error("attestation is missing: " + ", ".join(sorted(missing)))
    for field in (
        "asset_key",
        "report_digest",
        "policy_id",
        "policy_root",
        "control_set_root",
        "evidence_root",
        "approval_digest",
        "epoch_key",
        "parent_digest",
    ):
        if not isinstance(value[field], str) or _DIGEST.fullmatch(value[field]) is None:
            raise RegistryV2Error(f"{field} must be a lowercase SHA-256 digest")
    for field in (
        "asset_key",
        "report_digest",
        "policy_id",
        "approval_digest",
        "epoch_key",
    ):
        if value[field] == "0" * 64:
            raise RegistryV2Error(f"{field} must not be zero")
    if not isinstance(value["publisher"], str) or _ADDRESS.fullmatch(value["publisher"]) is None:
        raise RegistryV2Error("publisher must be an Ethereum address")
    if type(value["status"]) is not int or not 0 <= value["status"] <= 3:
        raise RegistryV2Error("status must identify a Registry v2 status")
    for field in ("observed_at", "valid_until", "sequence", "correction_of"):
        if type(value[field]) is not int or not 0 <= value[field] <= 2**64 - 1:
            raise RegistryV2Error(f"{field} must fit uint64")
    if value["valid_until"] < value["observed_at"]:
        raise RegistryV2Error("valid_until must not precede observed_at")
    if value["sequence"] == 0:
        raise RegistryV2Error("sequence must be positive")
    if value["correction_of"] >= value["sequence"]:
        raise RegistryV2Error("correction_of must precede sequence")
    if not isinstance(value["report_uri"], str) or not value["report_uri"]:
        raise RegistryV2Error("report_uri must be nonempty text")
    if type(value.get("chain_id")) is not int or value["chain_id"] <= 0:
        raise RegistryV2Error("chain_id must be a positive integer")
    if (
        not isinstance(value.get("verifying_contract"), str)
        or _ADDRESS.fullmatch(value["verifying_contract"]) is None
    ):
        raise RegistryV2Error("verifying_contract must be an Ethereum address")


def _bytes32(value: object, field: str) -> bytes:
    if isinstance(value, bytes) and len(value) == 32:
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", value):
        return bytes.fromhex(value.removeprefix("0x"))
    raise RegistryV2Error(f"{field} must be 32 bytes")


def _unix_timestamp(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RegistryV2Error(f"{field} must be a normalized UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise RegistryV2Error(f"{field} must be a normalized UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise RegistryV2Error(f"{field} must be a normalized UTC timestamp")
    return int(parsed.timestamp())
