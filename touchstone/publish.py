"""Idempotent local-node publication for the fixed TouchstoneRegistry ABI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import urlsplit

from web3 import Web3
from web3.exceptions import TimeExhausted, TransactionNotFound
from cryptography.exceptions import InvalidSignature

from touchstone.controls import AssetState
from touchstone.signing import (
    canonical_json_bytes,
    strict_json_loads,
    verify_signed_report,
)
from touchstone.translog import TransparencyLog


DEFAULT_LOCAL_RPC_URL = "http://127.0.0.1:8545"
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


class Web3RegistryBackend:
    """Minimal web3.py adapter for a local TouchstoneRegistry deployment."""

    def __init__(
        self,
        contract_address: str,
        publisher_address: str,
        *,
        rpc_url: str = DEFAULT_LOCAL_RPC_URL,
    ) -> None:
        _validate_local_rpc_url(rpc_url)
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.web3.is_connected():
            raise ConnectionError(f"cannot connect to local node at {rpc_url}")
        self.publisher_address = Web3.to_checksum_address(publisher_address)
        self.contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(contract_address), abi=REGISTRY_ABI
        )

    def latest_sequence(self, asset_key: bytes) -> int:
        return int(self.contract.functions.latestSequence(asset_key).call())

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport:
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
            transaction = self.contract.functions.publish(*common)
        else:
            transaction = self.contract.functions.publishCorrection(
                common[0], correction_of, *common[1:]
            )
        return _transaction_hash(transaction.transact({"from": self.publisher_address}))

    def get_receipt(self, transaction_hash: str) -> Mapping[str, object] | None:
        try:
            return self.web3.eth.get_transaction_receipt(transaction_hash)
        except TransactionNotFound:
            return None

    def wait_for_receipt(
        self, transaction_hash: str, timeout: float
    ) -> Mapping[str, object]:
        return self.web3.eth.wait_for_transaction_receipt(
            transaction_hash, timeout=timeout
        )

    def find_receipt(
        self, asset_key: bytes, sequence: int, correction_of: int | None
    ) -> tuple[str, Mapping[str, object]] | None:
        event = (
            self.contract.events.Published()
            if correction_of is None
            else self.contract.events.Corrected()
        )
        logs = tuple(
            event.get_logs(
                argument_filters={"assetKey": asset_key, "sequence": sequence},
                from_block=0,
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
        return transaction_hash, self.web3.eth.get_transaction_receipt(transaction_hash)


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


def _validate_local_rpc_url(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("rpc_url must identify the local loopback host")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("rpc_url must identify the local loopback host") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("rpc_url must identify the local loopback host")


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
