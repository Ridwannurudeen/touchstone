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
from touchstone.translog import TransparencyLog  # noqa: E402


DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0


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
        if not isinstance(backoff_seconds, (int, float)) or backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
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
        except OperationsError as error:
            raise UnresolvedPublication(
                f"the recorded operation cannot be read: {error}"
            ) from error
        if operation is None:
            # The operation may have been cleared by a run that died before it could
            # close the incident it had opened. Nothing is outstanding, so the incident
            # is stale, and leaving it open would report a service as stuck for ever.
            if self.client.pending_transaction() is None:
                self._close_open_incidents(
                    "no publication is outstanding",
                    kinds={PUBLICATION_UNRESOLVED},
                )
            return None
        moment = self.now()
        try:
            self.operations.resolve(self.client)
        except (PublicationError, OperationsError) as error:
            self.incidents.open_incident(
                asset_key=operation.asset_key,
                kind=PUBLICATION_UNRESOLVED,
                detail=(
                    f"sequence {operation.sequence} was in flight at startup and could "
                    f"not be settled: {error}"
                ),
                occurred_at=moment,
            )
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
            self.operations.begin_operation(
                signed_report,
                report_uri=report_uri(signed_report),
                correction_of=correction_of,
                scheduled_for=scheduled_at,
            )
        except Exception as error:  # noqa: BLE001 - recorded, like every other failure
            return self._record_incident(
                PUBLICATION_UNRESOLVED,
                f"the report could not be recorded for publication: {error}",
                scheduled_at,
            )
        try:
            # The retry belongs here, around the publication, because this is where a
            # transport failure actually arises — preflight and submission. Wrapping the
            # producer instead meant submission was never retried at all.
            self._with_retry(lambda: self.operations.resolve(self.client))
        except (PublicationError, OperationsError) as error:
            # OperationsError as well as PublicationError: refusing to retry a journalled
            # transaction raises the former, and letting it escape would end the slot
            # rather than record it — the schedule would survive, but the reason would be
            # lost.
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
        if self.operations.load_operation() is None:
            return
        if self.client.pending_transaction() is not None:
            self.operations.resolve(self.client)
            return
        self._with_retry(lambda: self.operations.resolve(self.client))

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

    def record_outage(self, first_missed: datetime, count: int) -> None:
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
        on_outage=service.record_outage,
        # A last resort. run_slot records everything it can, but anything that escapes it
        # would otherwise leave the schedule running and the log silent — the worst
        # combination, because it looks exactly like working.
        on_failure=service.record_escaped_failure,
        **schedule_arguments,
    )


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
