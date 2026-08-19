"""Restart-safe RegistryV2 publication over exact locally signed bytes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from cryptography.exceptions import InvalidSignature
from web3 import Web3
from web3.exceptions import TimeExhausted

from touchstone.keyring import decoded_transaction, verification_keys
from touchstone.publish_v2 import (
    CONFIRMED,
    INCLUDED,
    MISSING,
    PreparedRegistryV2Transaction,
    RegistryV2Backend,
    RegistryV2ChainReport,
    RegistryV2PublicationError,
)
from touchstone.quantities import finite_positive
from touchstone.registry_v2 import (
    RegistryV2Error,
    attestation_from_report,
    verify_attestation,
)
from touchstone.signing import (
    canonical_json_bytes,
    frozen_snapshot,
    strict_json_loads,
    verify_signed_report,
)


JOURNAL_VERSION = 1
_TX_HASH = re.compile(r"0x[0-9a-f]{64}")
_RAW_TRANSACTION = re.compile(r"(?:[0-9a-f]{2})+")
_JOURNAL_FIELDS = frozenset(
    {
        "journal_version",
        "chain_id",
        "registry_address",
        "relayer_address",
        "publisher_identity_address",
        "asset_key",
        "sequence",
        "report_sha256",
        "report_uri",
        "correction_of",
        "transaction_hash",
        "raw_transaction",
        "nonce",
        "gas",
        "maximum_fee_wei",
        "attestation",
    }
)


class RegistryV2PendingSubmission(RegistryV2PublicationError):
    """A durable V2 transaction remains unresolved or cannot be trusted."""


class RegistryV2SubmissionFailed(RegistryV2PublicationError):
    """A mined V2 transaction failed or did not reconcile."""


@dataclass(frozen=True, slots=True)
class RegistryV2PublicationResult:
    transaction_hash: str
    receipt: Mapping[str, object]
    reconciled: bool
    report: RegistryV2ChainReport


class RegistryV2PublisherClient:
    """Persist before broadcast and recover only the exact attested publication."""

    def __init__(
        self,
        backend: RegistryV2Backend,
        pending_path: str | os.PathLike[str],
        *,
        receipt_timeout: float = 120.0,
    ) -> None:
        self.backend = backend
        self.pending_path = Path(pending_path).resolve()
        self.receipt_timeout = finite_positive(receipt_timeout, "receipt_timeout")

    def publish(
        self, signed_report: Mapping[str, object], *, report_uri: str
    ) -> RegistryV2PublicationResult:
        report = signed_report.get("report") if isinstance(signed_report, Mapping) else None
        if isinstance(report, Mapping) and report.get("correction_of") is not None:
            raise ValueError("correction reports require publish_correction")
        return self._publish(signed_report, report_uri=report_uri, correction_of=0)

    def publish_correction(
        self, signed_report: Mapping[str, object], *, report_uri: str
    ) -> RegistryV2PublicationResult:
        report = signed_report.get("report") if isinstance(signed_report, Mapping) else None
        correction_of = report.get("correction_of") if isinstance(report, Mapping) else None
        if type(correction_of) is not int or correction_of < 1:
            raise ValueError("correction report must identify correction_of")
        return self._publish(
            signed_report, report_uri=report_uri, correction_of=correction_of
        )

    def pending_transaction(self) -> str | None:
        pending = self._load_pending()
        return None if pending is None else str(pending["transaction_hash"])

    def _publish(
        self,
        signed_report: Mapping[str, object],
        *,
        report_uri: str,
        correction_of: int,
    ) -> RegistryV2PublicationResult:
        signed_report = frozen_snapshot(signed_report, "signed_report")
        if not isinstance(report_uri, str) or not report_uri:
            raise ValueError("report_uri must be nonempty text")
        request = {
            "report_sha256": hashlib.sha256(
                canonical_json_bytes(dict(signed_report))
            ).hexdigest(),
            "report_uri": report_uri,
            "correction_of": correction_of,
        }
        self.backend.revalidate()
        pending = self._load_pending()
        reconciled = pending is not None
        if pending is None:
            prepared = self.backend.prepare(
                signed_report,
                report_uri=report_uri,
                correction_of=correction_of,
            )
            attestation = dict(prepared.attestation)
            self._write_pending(
                {
                    "journal_version": JOURNAL_VERSION,
                    "chain_id": self.backend.manifest.chain_id,
                    "registry_address": self.backend.manifest.registry_address,
                    "relayer_address": self.backend.relayer_key.address,
                    "publisher_identity_address": (
                        self.backend.manifest.publisher_identity_address
                    ),
                    "asset_key": attestation["asset_key"],
                    "sequence": attestation["sequence"],
                    **request,
                    "transaction_hash": prepared.transaction_hash,
                    "raw_transaction": prepared.raw.hex(),
                    "nonce": prepared.nonce,
                    "gas": prepared.gas,
                    "maximum_fee_wei": prepared.maximum_fee_wei,
                    "attestation": attestation,
                }
            )
            self.backend.broadcast(prepared)
        else:
            if any(pending.get(field) != value for field, value in request.items()):
                raise RegistryV2PendingSubmission(
                    "another persisted v2 submission is unresolved"
                )
            prepared = self._prepared_from_pending(pending, signed_report)
            state, _ = self.backend.receipt_state(prepared.transaction_hash)
            if state == MISSING:
                attestation_publisher = Web3.to_checksum_address(
                    str(prepared.attestation["publisher"])
                )
                if pending.get("relayer_address") != self.backend.relayer_key.address or (
                    attestation_publisher != self.backend.manifest.publisher_address
                ):
                    raise RegistryV2PendingSubmission(
                        "a missing v2 transaction cannot be rebroadcast after publisher "
                        "or relayer rotation"
                    )
                self.backend.broadcast(prepared)
            elif state == INCLUDED:
                pass
            elif state != CONFIRMED:
                raise RegistryV2PendingSubmission(
                    f"transaction {prepared.transaction_hash} has unknown state {state!r}"
                )
        state, _ = self.backend.receipt_state(prepared.transaction_hash)
        if state != CONFIRMED:
            try:
                self.backend.wait_for_receipt(
                    prepared.transaction_hash, self.receipt_timeout
                )
            except TimeExhausted as error:
                raise RegistryV2PendingSubmission(
                    f"transaction {prepared.transaction_hash} remains pending"
                ) from error
        return self._finalize(prepared, reconciled=reconciled)

    def _prepared_from_pending(
        self,
        pending: Mapping[str, object],
        signed_report: Mapping[str, object],
    ) -> PreparedRegistryV2Transaction:
        if set(pending) != _JOURNAL_FIELDS:
            raise RegistryV2PendingSubmission(
                "pending v2 submission has an invalid field set"
            )
        if pending.get("journal_version") != JOURNAL_VERSION:
            raise RegistryV2PendingSubmission(
                "pending v2 submission has an unsupported version"
            )
        for field, expected in (
            ("chain_id", self.backend.manifest.chain_id),
            ("registry_address", self.backend.manifest.registry_address),
            (
                "publisher_identity_address",
                self.backend.manifest.publisher_identity_address,
            ),
        ):
            if pending.get(field) != expected:
                raise RegistryV2PendingSubmission(
                    f"pending v2 submission targets a different {field}"
                )
        try:
            report = verify_signed_report(
                signed_report, verification_keys(self.backend.manifest)
            )
        except (InvalidSignature, TypeError, ValueError) as error:
            raise RegistryV2PendingSubmission(
                f"pending report verification failed: {error}"
            ) from error
        if not isinstance(report, Mapping):
            raise RegistryV2PendingSubmission("pending report must be an object")
        kid = signed_report.get("kid")
        reporting_key = (
            self.backend.manifest.key(kid) if isinstance(kid, str) else None
        )
        if reporting_key is None or not reporting_key.verifiable:
            raise RegistryV2PendingSubmission(
                "pending report was not signed by a verifiable reporting key"
            )
        attestation = pending.get("attestation")
        if not isinstance(attestation, Mapping):
            raise RegistryV2PendingSubmission(
                "pending v2 submission has no attestation"
            )
        attestation = frozen_snapshot(attestation, "attestation")
        try:
            recovered = verify_attestation(attestation)
            expected = attestation_from_report(
                report,
                publisher=str(attestation.get("publisher")),
                parent_digest=str(attestation.get("parent_digest")),
                correction_of=int(pending["correction_of"]),
                report_uri=str(pending["report_uri"]),
                chain_id=self.backend.manifest.chain_id,
                verifying_contract=self.backend.manifest.registry_address,
            )
        except (RegistryV2Error, TypeError, ValueError) as error:
            raise RegistryV2PendingSubmission(
                f"pending attestation verification failed: {error}"
            ) from error
        if Web3.to_checksum_address(recovered) != Web3.to_checksum_address(
            str(attestation["publisher"])
        ):
            raise RegistryV2PendingSubmission(
                "pending attestation was signed by another publisher"
            )
        if dict(attestation) != {**expected, "signature": attestation["signature"]}:
            raise RegistryV2PendingSubmission(
                "pending attestation does not describe the signed report"
            )
        if pending.get("asset_key") != attestation["asset_key"] or (
            pending.get("sequence") != attestation["sequence"]
        ):
            raise RegistryV2PendingSubmission(
                "pending report identity does not match its attestation"
            )
        transaction_hash = pending.get("transaction_hash")
        raw_text = pending.get("raw_transaction")
        nonce = pending.get("nonce")
        gas = pending.get("gas")
        maximum_fee = pending.get("maximum_fee_wei")
        if not isinstance(transaction_hash, str) or _TX_HASH.fullmatch(transaction_hash) is None:
            raise RegistryV2PendingSubmission(
                "pending v2 submission has an invalid transaction hash"
            )
        if not isinstance(raw_text, str) or _RAW_TRANSACTION.fullmatch(raw_text) is None:
            raise RegistryV2PendingSubmission(
                "pending v2 submission has invalid signed bytes"
            )
        if any(type(value) is not int or value < 0 for value in (nonce, gas, maximum_fee)):
            raise RegistryV2PendingSubmission(
                "pending v2 submission has invalid transaction quantities"
            )
        raw = bytes.fromhex(raw_text)
        recomputed = "0x" + Web3.keccak(raw).hex().removeprefix("0x").lower()
        if recomputed != transaction_hash:
            raise RegistryV2PendingSubmission(
                "pending transaction hash does not match its signed bytes"
            )
        try:
            decoded = decoded_transaction(raw)
        except ValueError as error:
            raise RegistryV2PendingSubmission(str(error)) from error
        journalled_relayer = pending.get("relayer_address")
        if not isinstance(journalled_relayer, str) or not Web3.is_checksum_address(
            journalled_relayer
        ):
            raise RegistryV2PendingSubmission(
                "pending v2 submission has an invalid relayer address"
            )
        expected_transaction = {
            "chain_id": self.backend.manifest.chain_id,
            "nonce": nonce,
            "to": self.backend.manifest.registry_address,
            "value": 0,
            "data": self.backend.calldata(attestation),
            "sender": journalled_relayer,
        }
        if decoded != expected_transaction:
            raise RegistryV2PendingSubmission(
                "pending signed transaction does not match the attested publication"
            )
        return PreparedRegistryV2Transaction(
            transaction_hash=transaction_hash,
            raw=raw,
            nonce=nonce,
            gas=gas,
            maximum_fee_wei=maximum_fee,
            report_input=(),
            attestation=attestation,
            correction_of=int(pending["correction_of"]),
        )

    def _finalize(
        self,
        prepared: PreparedRegistryV2Transaction,
        *,
        reconciled: bool,
    ) -> RegistryV2PublicationResult:
        self.backend.revalidate()
        state, receipt = self.backend.receipt_state(prepared.transaction_hash)
        if state != CONFIRMED or receipt is None:
            raise RegistryV2PendingSubmission(
                f"transaction {prepared.transaction_hash} is {state} after revalidation"
            )
        status = receipt.get("status")
        if type(status) is not int or status != 1:
            raise RegistryV2SubmissionFailed(
                f"transaction {prepared.transaction_hash} did not succeed"
            )
        report = self.backend.reconcile(prepared.attestation)
        self._clear_pending()
        return RegistryV2PublicationResult(
            transaction_hash=prepared.transaction_hash,
            receipt=receipt,
            reconciled=reconciled,
            report=report,
        )

    def _load_pending(self) -> dict[str, object] | None:
        if not self.pending_path.exists():
            return None
        try:
            value = strict_json_loads(self.pending_path.read_bytes())
        except (OSError, TypeError, ValueError) as error:
            raise RegistryV2PendingSubmission(
                f"cannot read pending v2 submission: {error}"
            ) from error
        if not isinstance(value, dict):
            raise RegistryV2PendingSubmission(
                "pending v2 submission must be an object"
            )
        return value

    def _write_pending(self, value: Mapping[str, object]) -> None:
        try:
            self.pending_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.pending_path.with_name(self.pending_path.name + ".tmp")
            with temporary.open("wb") as output:
                output.write(canonical_json_bytes(dict(value)) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.pending_path)
            if os.name != "nt":
                directory = os.open(self.pending_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as error:
            raise RegistryV2PendingSubmission(
                f"the pending v2 journal cannot be written: {error}"
            ) from error

    def _clear_pending(self) -> None:
        try:
            self.pending_path.unlink(missing_ok=True)
        except OSError as error:
            raise RegistryV2PendingSubmission(
                f"the pending v2 journal cannot be cleared: {error}"
            ) from error
