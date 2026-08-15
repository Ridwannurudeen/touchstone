"""The unattended service: one slot at a time, resolving the last one first.

The order is the whole design.

On startup, and before any evidence is fetched or anything is signed, an unresolved
publication is settled. A service that begins a new epoch while an old publication is in
flight is a service that can put two reports on the chain for one day, and no amount of
care later undoes that.

Within a slot, evidence either supports a report or it does not. If it does not — the
source would not answer, the epoch failed, the captures did not confirm — an incident is
opened and **nothing is signed and nothing is published**. The projection ages into
``STALE`` on its own once the last report's deadline passes. Silence is recorded as
silence; it is never rendered as an observation.

Retrying is deliberately narrow. Only a transport failure before signing is retried, only
while no transaction has been journalled, and only a bounded number of times. Once signed
bytes exist, the publisher's own reconciliation owns them and no loop here may resend them.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402
from touchstone.incidents import (  # noqa: E402
    EPOCH_FAILED,
    PUBLICATION_UNRESOLVED,
    SLOT_MISSED,
    SOURCE_UNAVAILABLE,
    IncidentLog,
)
from touchstone.locking import LockUnavailable, exclusive_lock  # noqa: E402
from touchstone.keyring import (  # noqa: E402
    IdentityError,
    PublisherKey,
    assert_role_separation,
)
from touchstone.operations import (  # noqa: E402
    OperationsError,
    OperationsStore,
    UnresolvedPublication,
)
from touchstone.publish import (  # noqa: E402
    PublicationError,
    PublisherClient,
    SignedRegistryBackend,
    TransportUnavailable,
)
from touchstone.schedule import ScheduleOutcome, run_schedule  # noqa: E402
from touchstone.signing import (  # noqa: E402
    canonical_json_bytes,
    strict_json_loads,
)
from touchstone.translog import TransparencyLog  # noqa: E402


DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0
# The longest single wait a retry may derive. An hour is already far past the point where
# waiting is the right answer; beyond it the configuration is a mistake, not a policy.
MAX_BACKOFF_SECONDS = 3600.0


class SourceUnavailable(RuntimeError):
    """Evidence could not be retrieved. Says nothing about the asset."""


@dataclass(frozen=True, slots=True)
class SlotOutcome:
    """What one slot did, in terms an operator can act on."""

    scheduled_at: datetime
    published: bool
    incident_id: str | None
    detail: str


class Service:
    """Holds the durable pieces and performs one slot at a time."""

    def __init__(
        self,
        client: PublisherClient,
        operations: OperationsStore,
        incidents: IncidentLog,
        *,
        asset_key: str,
        lock_path: str | Path | None = None,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.client = client
        self.operations = operations
        self.incidents = incidents
        self.asset_key = asset_key
        # One daemon per workspace. Checking for an outstanding operation and then acting
        # on it is a read-modify-write across several files, and two services interleaving
        # there can both pass the check and both go on to produce.
        self.lock_path = Path(
            lock_path
            if lock_path is not None
            else Path(operations.directory).parent / "service.lock"
        )
        if type(retries) is not int or retries < 0:
            raise ValueError("retries must be a non-negative integer")
        if (
            isinstance(backoff_seconds, bool)
            or not isinstance(backoff_seconds, (int, float))
            or not math.isfinite(backoff_seconds)
            or backoff_seconds < 0
        ):
            raise ValueError("backoff_seconds must be a non-negative, finite number")
        # The *derived* delays, not only the base. Doubling a finite base can reach
        # infinity, and a configuration accepted at startup would then raise on the first
        # failure it was supposed to absorb — turning the recovery into the outage.
        longest = backoff_seconds * (2 ** max(retries - 1, 0))
        if not math.isfinite(longest) or longest > MAX_BACKOFF_SECONDS:
            raise ValueError(
                f"backoff_seconds={backoff_seconds!r} with retries={retries} reaches "
                f"{longest!r} seconds, beyond the {MAX_BACKOFF_SECONDS} second maximum"
            )
        self.retries = retries
        self.backoff_seconds = float(backoff_seconds)
        self.sleep = sleep
        self.now = now

    # ------------------------------------------------------------------- startup
    def resolve_startup(self) -> SlotOutcome | None:
        """Settle any publication left in flight. Nothing else may run before this.

        A failure here opens an incident and is re-raised: continuing into a new epoch
        with an unresolved publication is the one thing this service must never do.
        """
        try:
            operation = self.operations.load_operation()
            journalled_before = self.client.pending_transaction()
        except Exception as error:  # noqa: BLE001 - reading is a failure like any other
            # These reads used to abort before anything was written down, so the one
            # startup failure an operator most needs to see — "I cannot even tell what was
            # in flight" — was the one that left no trace.
            self._record_startup_failure(
                f"the durable publication state cannot be read: {error}"
            )
            raise UnresolvedPublication(
                f"the durable publication state cannot be read: {error}"
            ) from error
        if operation is None:
            journalled = journalled_before
            if journalled is not None:
                # Recorded before it is raised. An aborted startup that leaves no trace is
                # exactly the kind of thing the incident log exists to make impossible.
                detail = (
                    f"transaction {journalled} is journalled with no operation to settle "
                    "it; this workspace must be reconciled before the service runs"
                )
                # Through the recorder, so a failure writing the incident cannot
                # replace the unresolved-publication error it was describing.
                self._record_startup_failure(detail)
                raise UnresolvedPublication(detail)
            # The operation may have been cleared by a run that died before it could close
            # the incident it had opened. With no operation and no journal there is
            # genuinely nothing outstanding, and leaving it open would report a service as
            # stuck for ever. This only ever closes *publication* incidents, which is why
            # a failure before an operation exists is not filed as one.
            self._close_open_incidents(
                "no publication is outstanding",
                kinds={PUBLICATION_UNRESOLVED},
            )
            return None
        moment = self.now()
        self._require_our_asset(operation, moment=moment)
        try:
            self.operations.resolve(
                self.client, expected_asset_key=self.asset_key
            )
        except Exception as error:  # noqa: BLE001 - the contract is that *any* failure is recorded
            self._record_startup_failure(
                f"sequence {operation.sequence} was in flight at startup and could not "
                f"be settled: {error}",
                asset_key=operation.asset_key,
                occurred_at=moment,
            )
            # Deliberately broad. The transparency log raises its own error type, and
            # catching only the publication and operations types let that escape with no
            # incident at all — leaving the docstring's promise false for exactly the
            # failure an operator would most want recorded.
            raise UnresolvedPublication(
                f"startup could not settle sequence {operation.sequence}: {error}"
            ) from error
        # Only the publication incidents. Settling a publication says nothing about
        # whether the source came back, and closing a SOURCE_UNAVAILABLE here would have
        # claimed a recovery nobody observed.
        self._close_open_incidents(
            f"sequence {operation.sequence} was settled on a later start",
            kinds={PUBLICATION_UNRESOLVED},
        )
        return SlotOutcome(
            scheduled_at=moment,
            published=True,
            incident_id=None,
            detail=f"settled sequence {operation.sequence} left in flight",
        )

    def _require_our_asset(self, operation, *, moment: datetime | None = None) -> None:
        """A durable operation must be for the asset this service is configured for.

        Checking only what ``produce()`` returns leaves the upgrade path open: an
        operation persisted for another asset before a crash is still resolved — and
        published — by whichever service starts next. The record on disk needs the same
        check as the record in memory.
        """
        if operation.asset_key == self.asset_key:
            return
        detail = (
            f"the recorded operation is for {operation.asset_key!r}, but this service is "
            f"configured for {self.asset_key!r}"
        )
        self._record_startup_failure(
            detail, asset_key=operation.asset_key, occurred_at=moment
        )
        raise UnresolvedPublication(detail)

    def _record_startup_failure(
        self,
        detail: str,
        *,
        asset_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Write the incident, and never let writing it hide the failure it describes."""
        try:
            self.incidents.open_incident(
                asset_key=asset_key or self.asset_key,
                kind=PUBLICATION_UNRESOLVED,
                detail=detail,
                occurred_at=occurred_at or self.now(),
            )
        except Exception:  # noqa: BLE001 - the original failure is the one to report
            pass

    # ---------------------------------------------------------------------- slot
    def run_slot(
        self,
        scheduled_at: datetime,
        produce: Callable[[datetime], Mapping[str, object] | None],
        *,
        report_uri: Callable[[Mapping[str, object]], str],
        correction_of: int | None = None,
    ) -> SlotOutcome:
        """Produce a report for this slot and publish it, or record why neither happened."""
        # Every slot, not only the first. A publication that failed leaves an operation
        # behind, and the scheduler deliberately keeps going — so without this the next
        # slot would fetch and sign before anything noticed the earlier one was still in
        # flight. Resolving first is the whole invariant; it cannot live only in startup.
        try:
            self._resolve_outstanding()
        except Exception as error:  # noqa: BLE001 - recorded, and the slot does not run
            return self._record_incident(
                PUBLICATION_UNRESOLVED,
                f"an earlier publication is still unresolved: {error}",
                scheduled_at,
            )

        try:
            signed_report = produce(scheduled_at)
        except SourceUnavailable as error:
            return self._record_incident(SOURCE_UNAVAILABLE, str(error), scheduled_at)
        except Exception as error:  # noqa: BLE001 - any epoch failure is an incident
            return self._record_incident(EPOCH_FAILED, str(error), scheduled_at)

        # Frozen before it is inspected, so what is checked is what is published.
        signed_report = _frozen(signed_report) if signed_report is not None else None
        produced_asset = None
        if isinstance(signed_report, Mapping):
            report = signed_report.get("report")
            if isinstance(report, Mapping):
                produced_asset = report.get("asset_key")
        if signed_report is not None and produced_asset != self.asset_key:
            # Nothing else checks this. The operation takes its asset from the report, so
            # a producer returning some other asset's report was published under it and
            # then *this* asset's incidents were closed — recording a recovery for an
            # asset nobody had observed.
            return self._record_incident(
                EPOCH_FAILED,
                f"the producer returned a report for {produced_asset!r}, but this "
                f"service is configured for {self.asset_key!r}",
                scheduled_at,
            )

        if signed_report is None:
            # Incomplete evidence is not a finding. No report is signed, nothing reaches
            # the chain, and the projection ages out on its own.
            return self._record_incident(
                SOURCE_UNAVAILABLE,
                "evidence was incomplete, so no report was produced",
                scheduled_at,
            )

        try:
            # Inside the boundary: naming the report and recording the operation can both
            # fail — a bad URI, a full disk — and outside it those failures ended the slot
            # with nothing written down at all.
            # The caller names the report from its own copy. Handing over the object
            # that was just checked let a URI callback rewrite it in place — the check
            # passed on one report and the publication carried another.
            uri = report_uri(_frozen(signed_report))
            self.operations.begin_operation(
                signed_report,
                report_uri=uri,
                correction_of=correction_of,
                scheduled_for=scheduled_at,
                expected_asset_key=self.asset_key,
            )
        except Exception as error:  # noqa: BLE001 - recorded, like every other failure
            # EPOCH_FAILED, not PUBLICATION_UNRESOLVED: nothing was ever handed to the
            # publisher, so there is no operation and no journal. Filing it as a
            # publication incident made it look resolved the moment startup noticed those
            # were absent — closing an incident whose cause nobody had touched.
            return self._record_incident(
                EPOCH_FAILED,
                f"the report could not be recorded for publication: {error}",
                scheduled_at,
            )
        try:
            # The retry belongs here, around the publication, because this is where a
            # transport failure actually arises — preflight and submission. Wrapping the
            # producer instead meant submission was never retried at all.
            self._with_retry(
                lambda: self.operations.resolve(
                    self.client, expected_asset_key=self.asset_key
                )
            )
        except Exception as error:  # noqa: BLE001 - anything here is an incident
            # Deliberately broad. Two chosen types left the transparency log's own error
            # escaping unrecorded, and naming types one at a time is how that keeps
            # happening: the schedule survives either way, but the reason is lost.
            return self._record_incident(
                PUBLICATION_UNRESOLVED,
                f"the report was signed but not published: {error}",
                scheduled_at,
            )
        self._close_open_incidents("evidence was retrieved and published again")
        return SlotOutcome(
            scheduled_at=scheduled_at,
            published=True,
            incident_id=None,
            detail="published",
        )

    def _resolve_outstanding(self) -> None:
        """Settle a publication left over from an earlier slot, if there is one.

        When a transaction has already been journalled this calls the publisher **once**,
        without the retry loop. Routing it through the loop meant the loop's own guard —
        refuse while a journal exists — fired before the work ran, so a publication that
        timed out waiting for a receipt could never reconcile while the daemon stayed
        alive, even after the transaction confirmed. Disabling the retry is right;
        disabling reconciliation is not.
        """
        journalled = self.client.pending_transaction()
        outstanding = self.operations.load_operation()
        if outstanding is not None:
            self._require_our_asset(outstanding)
        if outstanding is None:
            if journalled is not None:
                # A journal with no operation is still something unresolved on the chain.
                # Returning here let the slot go on to fetch and sign, and only then run
                # into the refusal — after the work that must not happen first.
                raise UnresolvedPublication(
                    f"transaction {journalled} is journalled with no operation to settle "
                    "it; nothing new may be produced until it is reconciled"
                )
            return
        if journalled is not None:
            self.operations.resolve(
                self.client, expected_asset_key=self.asset_key
            )
        else:
            self._with_retry(
                lambda: self.operations.resolve(
                    self.client, expected_asset_key=self.asset_key
                )
            )
        # Closed here, with the resolution that earned it, rather than at the end of a
        # successful slot. Waiting meant a producer failing afterwards left the settled
        # publication's incident open, reporting a service as stuck on something it had
        # just finished.
        self._close_open_incidents(
            "the publication left over from an earlier slot was settled",
            kinds={PUBLICATION_UNRESOLVED},
        )

    def record_escaped_failure(
        self, scheduled_at: datetime, error: BaseException
    ) -> None:
        """Record a slot failure that got past run_slot's own handling."""
        self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=EPOCH_FAILED,
            detail=f"the slot at {scheduled_at.isoformat()} failed unexpectedly: {error}",
            occurred_at=self.now(),
            state=self._projected_state(),
        )

    def record_outage(self, first_missed: datetime, count: int) -> None:  # noqa: D401
        """One incident per outage, carrying the exact number of slots it covered.

        Recording each missed slot separately would write thousands of entries for a long
        outage and bury everything else in the log. The count is exact even though only
        one entry is written for it.
        """
        self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=SLOT_MISSED,
            detail=(
                f"{count} slot(s) did not run, from {first_missed.isoformat()}"
                if count > 1
                else f"the slot scheduled for {first_missed.isoformat()} did not run"
            ),
            occurred_at=self.now(),
            state=self._projected_state(),
        )

    # ----------------------------------------------------------------- internals
    def _with_retry(self, work: Callable[[], object]) -> object:
        """Retry only a transport failure, and only while nothing has been signed.

        Once a transaction exists in the journal, retrying is not this loop's decision to
        make: the publisher reconciles it against the chain, and a retry here would be a
        second opinion about a transaction that may already be on the wire.
        """
        attempt = 0
        while True:
            if self.client.pending_transaction() is not None:
                raise UnresolvedPublication(
                    "a journalled transaction is unresolved; it is reconciled, not retried"
                )
            try:
                return work()
            except TransportUnavailable:
                if attempt >= self.retries:
                    raise
                self.sleep(self.backoff_seconds * (2**attempt))
                attempt += 1

    def _record_incident(
        self, kind: str, detail: str, scheduled_at: datetime
    ) -> SlotOutcome:
        entry = self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=kind,
            detail=detail,
            occurred_at=self.now(),
            state=self._projected_state(),
        )
        return SlotOutcome(
            scheduled_at=scheduled_at,
            published=False,
            incident_id=entry["entry_hash"],
            detail=detail,
        )

    def _projected_state(self) -> str | None:
        state = self.operations.load_state(self.asset_key)
        if state is None:
            return None
        return state.projected(self.now().date()).value

    def _close_open_incidents(
        self, detail: str, *, kinds: frozenset[str] | set[str] | None = None
    ) -> None:
        for incident in self.incidents.open_incidents(self.asset_key):
            if kinds is not None and incident.kind not in kinds:
                continue
            self.incidents.close_incident(
                incident.incident_id,
                detail=detail,
                occurred_at=self.now(),
                state=self._projected_state(),
            )


def serve(
    service: Service,
    produce: Callable[[datetime], Mapping[str, object] | None],
    *,
    report_uri: Callable[[Mapping[str, object]], str],
    interval_seconds: float,
    max_runs: int | None = None,
    **schedule_arguments: object,
) -> ScheduleOutcome:
    """Resolve what was left in flight, then run slots until asked to stop."""
    with exclusive_lock(service.lock_path):
        return _serve_locked(
            service,
            produce,
            report_uri=report_uri,
            interval_seconds=interval_seconds,
            max_runs=max_runs,
            **schedule_arguments,
        )


def _serve_locked(
    service: Service,
    produce: Callable[[datetime], Mapping[str, object] | None],
    *,
    report_uri: Callable[[Mapping[str, object]], str],
    interval_seconds: float,
    max_runs: int | None = None,
    **schedule_arguments: object,
) -> ScheduleOutcome:
    service.resolve_startup()
    return run_schedule(
        lambda scheduled_at: service.run_slot(
            scheduled_at, produce, report_uri=report_uri
        ),
        interval_seconds=interval_seconds,
        max_runs=max_runs,
        # Composed, not chosen. Letting a caller's handler *replace* the service's meant
        # supplying one silently switched off the incident recording — an escaped failure
        # would call the caller back and write nothing down.
        on_outage=_also(service.record_outage, schedule_arguments.pop("on_outage", None)),
        on_failure=_also(
            service.record_escaped_failure, schedule_arguments.pop("on_failure", None)
        ),
        **schedule_arguments,
    )


def _frozen(signed_report):
    """An independent copy, so nobody can rewrite a report after it has been checked."""
    return strict_json_loads(canonical_json_bytes(dict(signed_report)))


def _also(mandatory, extra):
    """Run the service's own recorder first, then anything the caller asked for."""
    if extra is None:
        return mandatory

    def both(*arguments):
        mandatory(*arguments)
        extra(*arguments)

    return both


def build_service(manifest_path: str, workspace: str, *, asset_key: str) -> Service:
    """Wire the durable pieces from a committed deployment manifest."""
    manifest = DeploymentManifest.load(manifest_path)
    assert_role_separation()
    backend = SignedRegistryBackend(manifest, PublisherKey.from_env(manifest))
    root = Path(workspace)
    client = PublisherClient(
        backend,
        TransparencyLog(root / "transparency.jsonl"),
        root / "pending.json",
    )
    return Service(
        client,
        OperationsStore(root / "operations"),
        IncidentLog(root / "incidents.jsonl"),
        asset_key=asset_key,
    )



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--asset-key", required=True)
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="settle any publication left in flight and stop, signing nothing new",
    )
    arguments = parser.parse_args(argv)
    try:
        service = build_service(
            arguments.manifest, arguments.workspace, asset_key=arguments.asset_key
        )
        if not arguments.resolve_only:  # noqa: SIM102
            # A slot needs an epoch runner, and this project has no live-source runner
            # yet: PLAN-T10 and PLAN-T11 own the adapters. Refusing is honest; a service
            # that started and silently published nothing would look like it was working.
            parser.error(
                "no live epoch adapter is wired yet; --resolve-only is the supported mode"
            )
        with exclusive_lock(service.lock_path):
            outcome = service.resolve_startup()
    except (
        DeploymentError,
        IdentityError,
        LockUnavailable,
        OperationsError,
        PublicationError,
    ) as error:
        print(f"SERVICE FAIL: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "resolved": outcome is not None,
                "detail": outcome.detail if outcome else "nothing was in flight",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
