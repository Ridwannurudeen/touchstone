"""Durable operational state, and the one publication a restart must finish first.

Two files, both written atomically, both meaningful only because they survive a crash.

The **operation** records a publication this service intends to complete: the exact signed
report, the URI it goes under, whether it is a correction, and the slot it belongs to. It
is written before the publisher is asked to do anything and removed only once that
publication is settled. On startup it is resolved before any new evidence is fetched or
signed, because a service that starts a second publication while an earlier one is
unresolved is a service that can publish twice for one epoch.

The **state** is the projection: the last state actual evidence supported, and the date
through which that remains a fair thing to say. After the deadline the projection becomes
``STALE`` on its own, without new evidence and without a new signed report — the absence of
evidence is not evidence, and this file is the place that distinction is kept.

Neither file is a source of truth about the chain. The chain is, and the publisher's own
journal reconciles against it; these two only remember what this service was in the middle
of doing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path

from web3 import Web3

from touchstone.controls import AssetState
from touchstone.publish import DuplicateSequence, PublicationResult, PublisherClient
from touchstone.signing import (
    canonical_json_bytes,
    frozen_snapshot,
    strict_json_loads,
)


OPERATION_VERSION = "touchstone.pending-operation.v1"
STATE_VERSION = "touchstone.operational-state.v1"

_OPERATION_FIELDS = frozenset(
    {
        "asset_key",
        "correction_of",
        "report_uri",
        "scheduled_for",
        "sequence",
        "signed_report",
        "version",
    }
)
_STATE_FIELDS = frozenset(
    {
        "asset_key",
        "evidence_deadline",
        "observed_state",
        "sequence",
        "updated_at",
        "version",
    }
)


class OperationsError(RuntimeError):
    """The durable operational record cannot be read, or contradicts itself."""


class UnresolvedPublication(OperationsError):
    """A publication was left in flight and could not be settled on this attempt."""


@dataclass(frozen=True, slots=True)
class PendingOperation:
    """The publication a restart owes the chain before it does anything else."""

    asset_key: str
    sequence: int
    report_uri: str
    correction_of: int | None
    scheduled_for: str
    signed_report: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OperationalState:
    """What evidence last supported, and how long that remains fair to say."""

    asset_key: str
    observed_state: str
    evidence_deadline: date
    sequence: int
    updated_at: str

    def projected(self, on: date) -> AssetState:
        """The state to show today.

        Past the deadline this becomes ``STALE`` with no new evidence and no new report.
        Nothing has been observed to be wrong — that would be ``INCONSISTENT`` — only that
        what is on record has aged out of the window it was published for.
        """
        if not isinstance(on, date) or isinstance(on, datetime):
            raise OperationsError("a projection date must be a plain date")
        if on > self.evidence_deadline:
            return AssetState.STALE
        return AssetState(self.observed_state)


class OperationsStore:
    """Atomic per-asset state and the single in-flight operation."""

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.operation_path = self.directory / "operation.json"
        self.now = now

    # ------------------------------------------------------------------ operation
    def begin_operation(
        self,
        signed_report: Mapping[str, object],
        *,
        report_uri: str,
        correction_of: int | None,
        scheduled_for: datetime,
        expected_asset_key: str | None = None,
    ) -> PendingOperation:
        """Write down the whole publication before asking anyone to perform it."""
        # One snapshot for the whole record. Every field below is read from the caller's
        # mapping at a different moment — the asset key here, the sequence there, the
        # envelope again at serialisation — so a caller that still holds a reference could
        # produce a durable operation whose top-level sequence and embedded report
        # disagree. `load_operation` would then reject the store's own record as
        # contradictory, and a real mutation would invalidate the signature too.
        signed_report = frozen_snapshot(signed_report, "signed_report")
        existing = self.load_operation()
        if existing is not None:
            raise UnresolvedPublication(
                f"an operation for {existing.asset_key} sequence {existing.sequence} is "
                "still unresolved; it must be settled before another begins"
            )
        report = _report_of(signed_report)
        if expected_asset_key is not None and report["asset_key"] != expected_asset_key:
            raise OperationsError(
                f"the report is for {report['asset_key']!r}, but this service is "
                f"configured for {expected_asset_key!r}"
            )
        if correction_of != report.get("correction_of"):
            raise OperationsError(
                f"correction_of={correction_of!r} contradicts the report, which says "
                f"{report.get('correction_of')!r}"
            )
        operation = PendingOperation(
            asset_key=report["asset_key"],
            sequence=report["sequence"],
            report_uri=report_uri,
            correction_of=correction_of,
            scheduled_for=_stamp(scheduled_for),
            signed_report=signed_report,
        )
        _write_atomic(self.operation_path, _operation_record(operation))
        return operation

    def load_operation(self) -> PendingOperation | None:
        value = _read(self.operation_path)
        if value is None:
            return None
        if set(value) != _OPERATION_FIELDS:
            raise OperationsError(
                f"operation fields must be exactly {sorted(_OPERATION_FIELDS)}"
            )
        if value["version"] != OPERATION_VERSION:
            raise OperationsError("operation version is unsupported")
        report = _report_of(value["signed_report"])
        if (
            report["asset_key"] != value["asset_key"]
            or report["sequence"] != value["sequence"]
        ):
            raise OperationsError(
                "the recorded operation does not describe the report it carries"
            )
        # The report says whether it is a correction and of what. An operation that
        # disagrees would send a plain report through the correction entry point, or the
        # reverse, on the strength of an unsigned local field.
        if value["correction_of"] != report.get("correction_of"):
            raise OperationsError(
                f"the operation says correction_of={value['correction_of']!r} while its "
                f"report says {report.get('correction_of')!r}"
            )
        return PendingOperation(
            asset_key=value["asset_key"],
            sequence=value["sequence"],
            report_uri=value["report_uri"],
            correction_of=value["correction_of"],
            scheduled_for=value["scheduled_for"],
            signed_report=value["signed_report"],
        )

    def clear_operation(self) -> None:
        self.operation_path.unlink(missing_ok=True)

    def resolve(
        self,
        client: PublisherClient,
        *,
        expected_asset_key: str | None = None,
    ) -> PublicationResult | None:
        """Settle any in-flight publication. Call before fetching or signing anything.

        Two crashes have to come out the same way. If the publisher never finished, this
        re-enters it and the publisher's own journal decides whether that means waiting,
        rebroadcasting the identical bytes, or reconciling. If the publisher finished but
        this store was never cleared, the registry reports the sequence as already
        published — and that is only *our* publication if the transparency log holds this
        exact report under it, which is what distinguishes finishing from colliding.
        """
        operation = self.load_operation()
        if operation is None:
            return None
        # Validated here, inside the call that loads and then publishes it. A caller that
        # checked a *previously* loaded operation checked a different object: this method
        # re-reads the file, so anything that changed in between was published unchecked.
        if expected_asset_key is not None and operation.asset_key != expected_asset_key:
            raise UnresolvedPublication(
                f"the recorded operation is for {operation.asset_key!r}, but this "
                f"service is configured for {expected_asset_key!r}"
            )
        publish = (
            client.publish_correction
            if operation.correction_of is not None
            else client.publish
        )
        try:
            result = publish(operation.signed_report, report_uri=operation.report_uri)
        except DuplicateSequence as error:
            self._prove_settled(client, operation, error)
            self._settle(operation)
            return None
        self._settle(operation)
        return result

    def _settle(self, operation: PendingOperation) -> None:
        """Record what the publication observed, and only then forget it was pending.

        The order is the point. Clearing first left a window where the chain and the
        transparency log were final, the operation was gone, and the projection still
        showed the previous epoch — with nothing left to reconcile from. Writing the state
        first means a crash in between simply leaves the operation to be settled again,
        which is idempotent.
        """
        self.save_state(operation.signed_report, updated_at=self.now())
        self.clear_operation()

    def _prove_settled(
        self,
        client: PublisherClient,
        operation: PendingOperation,
        error: Exception,
    ) -> None:
        """Establish that *this* operation is what settled, not merely something at it.

        A duplicate sequence proves only that the slot is taken. The transparency log
        shows this service published this report; the chain shows under which URI and by
        whom. Checking the log alone left a real gap: after the publisher finalised, an
        operation carrying the same report under a *different* URI was cleared as done,
        while the chain held the first URI. The report URI is not inside the signed
        report, so the chain is the only place to settle that question.
        """
        if self._logged_entry(client, operation) is None:
            raise UnresolvedPublication(
                f"sequence {operation.sequence} for {operation.asset_key} is already "
                f"onchain but this service has no record of publishing it: {error}"
            ) from error
        asset_key = bytes(Web3.keccak(text=operation.asset_key))
        report = _report_of(operation.signed_report)
        try:
            # Every field, not a chosen few. The publisher already owns this comparison —
            # roots, status, both timestamps, sequence and URI — and checking only the URI
            # and the lineage here let a report with different roots pass as settled,
            # after which the operation was cleared and local state saved for something
            # the chain does not contain.
            client.ensure_onchain_match(asset_key, report, operation.report_uri)
            onchain = client.backend.get_report(asset_key, operation.sequence)
        except UnresolvedPublication:
            raise
        except Exception as read_error:  # noqa: BLE001 - unproven is not settled
            raise UnresolvedPublication(
                f"sequence {operation.sequence} is onchain but does not match this "
                f"operation: {read_error}"
            ) from read_error
        lineage = client.backend.publisher_lineage(onchain.publisher)
        if lineage != client.manifest.publisher_identity_address:
            raise UnresolvedPublication(
                f"sequence {operation.sequence} was published by {onchain.publisher}, "
                f"whose lineage {lineage} is not this deployment's"
            )

    def _logged_entry(
        self, client: PublisherClient, operation: PendingOperation
    ) -> Mapping[str, object] | None:
        """The log entry for *this* publication, not merely one at the same sequence.

        Matching on asset and sequence alone accepted somebody else's entry — or our own
        for a different report — as proof that this operation had settled, and cleared it.
        The signed report is compared whole, because that is the thing the sequence was
        supposed to identify.
        """
        expected = canonical_json_bytes(dict(operation.signed_report))
        for entry in client.transparency_log.verify():
            if canonical_json_bytes(dict(entry["signed_report"])) == expected:
                return entry
        return None

    # ---------------------------------------------------------------------- state
    def state_path(self, asset_key: str) -> Path:
        return self.directory / f"state-{_slug(asset_key)}.json"

    def save_state(
        self, signed_report: Mapping[str, object], *, updated_at: datetime
    ) -> OperationalState:
        """Record what this report observed, and the date it remains fair to say it."""
        # The same multi-read shape as begin_operation: state, deadline, asset key and
        # sequence are four reads of a mapping the caller still owns.
        signed_report = frozen_snapshot(signed_report, "signed_report")
        report = _report_of(signed_report)
        transition = report.get("state_transition")
        if not isinstance(transition, Mapping):
            raise OperationsError("a report must carry its state transition")
        try:
            deadline = date.fromisoformat(transition["evidence_deadline"])
        except (KeyError, TypeError, ValueError) as error:
            raise OperationsError("the evidence deadline is not a date") from error
        state = OperationalState(
            asset_key=report["asset_key"],
            observed_state=report["state"],
            evidence_deadline=deadline,
            sequence=report["sequence"],
            updated_at=_stamp(updated_at),
        )
        _write_atomic(self.state_path(state.asset_key), _state_record(state))
        return state

    def load_state(self, asset_key: str) -> OperationalState | None:
        value = _read(self.state_path(asset_key))
        if value is None:
            return None
        if set(value) != _STATE_FIELDS:
            raise OperationsError(
                f"state fields must be exactly {sorted(_STATE_FIELDS)}"
            )
        if value["version"] != STATE_VERSION:
            raise OperationsError("state version is unsupported")
        try:
            deadline = date.fromisoformat(value["evidence_deadline"])
        except (TypeError, ValueError) as error:
            raise OperationsError("stored evidence deadline is not a date") from error
        if value["observed_state"] not in {member.value for member in AssetState}:
            raise OperationsError("stored observed state is not a known asset state")
        return OperationalState(
            asset_key=value["asset_key"],
            observed_state=value["observed_state"],
            evidence_deadline=deadline,
            sequence=value["sequence"],
            updated_at=value["updated_at"],
        )


def _operation_record(operation: PendingOperation) -> dict[str, object]:
    return {
        "asset_key": operation.asset_key,
        "correction_of": operation.correction_of,
        "report_uri": operation.report_uri,
        "scheduled_for": operation.scheduled_for,
        "sequence": operation.sequence,
        "signed_report": operation.signed_report,
        "version": OPERATION_VERSION,
    }


def _state_record(state: OperationalState) -> dict[str, object]:
    return {
        "asset_key": state.asset_key,
        "evidence_deadline": state.evidence_deadline.isoformat(),
        "observed_state": state.observed_state,
        "sequence": state.sequence,
        "updated_at": state.updated_at,
        "version": STATE_VERSION,
    }


def _report_of(signed_report: object) -> Mapping[str, object]:
    if not isinstance(signed_report, Mapping):
        raise OperationsError("a signed report must be an object")
    report = signed_report.get("report")
    if not isinstance(report, Mapping):
        raise OperationsError("a signed report must carry its report")
    if not isinstance(report.get("asset_key"), str):
        raise OperationsError("a report must name its asset")
    if type(report.get("sequence")) is not int:
        raise OperationsError("a report must carry an integer sequence")
    return report


def _write_atomic(path: Path, value: Mapping[str, object]) -> None:
    """Replace a file in one step, or leave the previous one entirely intact.

    A crash mid-write must not produce a half-written record: the reader would then have
    neither the old state nor the new one, and no way to tell which it was owed.
    """
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as output:
        output.write(canonical_json_bytes(dict(value)) + b"\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as error:
        raise OperationsError(f"cannot read {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise OperationsError(f"{path.name} must contain an object")
    return value


def _slug(asset_key: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in asset_key)


def _stamp(moment: datetime) -> str:
    if (
        not isinstance(moment, datetime)
        or moment.tzinfo is None
        or moment.utcoffset() is None
    ):
        raise OperationsError("an operational instant must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
