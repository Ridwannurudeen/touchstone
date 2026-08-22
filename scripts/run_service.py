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
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402
from touchstone.approval import load_approval_ledger  # noqa: E402
from touchstone.assets import USTB, get_asset  # noqa: E402
from touchstone.incidents import (  # noqa: E402
    EPOCH_FAILED,
    PUBLICATION_UNRESOLVED,
    SCHEDULE_UNUSABLE,
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
    epoch_key_bytes,
)
from touchstone.rpc_quorum import QuorumRPC  # noqa: E402
from touchstone.controls import AssetState  # noqa: E402
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.evaluate import default_controls  # noqa: E402
from touchstone.policy import PolicyError, load as load_policy  # noqa: E402
from touchstone.schedule import ScheduleOutcome, run_schedule  # noqa: E402
from touchstone.ustb_daemon import (  # noqa: E402
    asset_key_bytes,
    epoch_id_for,
    make_producer,
    report_uri,
    require_verifying_bundle,
    write_bundle,
)
from touchstone.sources import LiveTransport, SourceUnavailable  # noqa: E402,F401
from touchstone.signing import Ed25519Signer, frozen_snapshot  # noqa: E402
from touchstone import backup, heartbeat  # noqa: E402
from touchstone.backup import BackupError  # noqa: E402
from touchstone.heartbeat import HeartbeatError  # noqa: E402
from touchstone.quantities import finite_non_negative, utc_instant  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0
# The longest single wait a retry may derive. An hour is already far past the point where
# waiting is the right answer; beyond it the configuration is a mistake, not a policy.
MAX_BACKOFF_SECONDS = 3600.0
# 2 ** 1024 is the first power of two no float can hold. Past it the delay is infinite
# whatever the base, so the count can be refused without the integer ever being built.
_MAX_DOUBLINGS = 1024


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
        heartbeat_path: str | Path | None = None,
        registry_address: str | None = None,
        backup_dir: str | Path | None = None,
        backup_key: bytes | None = None,
        before_publish: Callable[[object], None] | None = None,
    ) -> None:
        self.client = client
        self.operations = operations
        self.incidents = incidents
        self.asset_key = asset_key
        # One daemon per workspace. Checking for an outstanding operation and then acting
        # on it is a read-modify-write across several files, and two services interleaving
        # there can both pass the check and both go on to produce.
        # Anchored like every other durable path. `build_service` already supplies an
        # absolute one, but the constructor is public, and a relative lock silently
        # becomes a *different* lock the moment the process changes directory — which is
        # precisely the second daemon this lock exists to refuse.
        self.lock_path = Path(
            lock_path
            if lock_path is not None
            else Path(operations.directory).parent / "service.lock"
        ).resolve()
        if type(retries) is not int or retries < 0:
            raise ValueError("retries must be a non-negative integer")
        backoff_seconds = finite_non_negative(backoff_seconds, "backoff_seconds")
        # The *derived* delays, over exactly the domain that is accepted. Doubling a
        # finite base can reach infinity, so checking the base alone let a configuration
        # be accepted at startup and then raise on the first failure it was meant to
        # absorb. Three edges matter and each was wrong in turn: no retries derives no
        # delays, so any finite base is fine; a zero base stays zero however many times it
        # doubles; and a huge retry count overflows the exponent itself rather than the
        # product, which surfaced as a raw OverflowError instead of a refusal.
        longest = 0.0
        if retries > 0 and backoff_seconds > 0:
            # A retry count large enough to overflow is refused without being exponentiated.
            # `2 ** (retries - 1)` for retries=10**9 is a multi-hundred-megabyte integer
            # built only to be thrown away, so the check itself was the denial of service.
            longest = (
                math.inf
                if retries - 1 >= _MAX_DOUBLINGS
                else backoff_seconds * float(2 ** (retries - 1))
            )
        if not math.isfinite(longest) or longest > MAX_BACKOFF_SECONDS:
            raise ValueError(
                f"backoff_seconds={backoff_seconds!r} with retries={retries} reaches "
                f"{longest!r} seconds, beyond the {MAX_BACKOFF_SECONDS} second maximum"
            )
        self.retries = retries
        self.backoff_seconds = float(backoff_seconds)
        self.sleep = sleep
        self.now = now
        # Reliability wiring. All optional, because the epoch machinery must remain
        # constructible without it — every test that predates T8 builds a Service with
        # neither a heartbeat nor a backup destination.
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.registry_address = registry_address
        self.backup_dir = Path(backup_dir) if backup_dir else None
        self.backup_key = backup_key
        # Passed to every `resolve()` so a republication cannot outrun its bundle. Optional
        # for the same reason as the rest of this block: the epoch machinery predates it and
        # many tests construct a Service with no workspace paths at all.
        self.before_publish = before_publish
        self._heartbeat_sequence = 0
        self._last_attempted_slot: str | None = None
        self._last_successful_epoch: str | None = None
        self._last_backup_at: str | None = None
        self._last_backup_day: str | None = None
        # The live proof of the workspace lock, set by `serve` for the duration of
        # the serving section. A cooperative backup is only meaningful while it is held.
        self._held = None

    # ---------------------------------------------------------------- reliability
    def beat(self) -> None:
        """Write one heartbeat, from one reading of the clock and the durable state.

        Never fatal. A daemon that stopped serving because it could not write a liveness
        file would have turned a monitoring failure into an outage — and the watchdog
        already treats an absent heartbeat as unhealthy, so the condition is reported by
        exactly the mechanism that exists for it.
        """
        if self.heartbeat_path is None:
            return
        try:
            self._heartbeat_sequence += 1
            heartbeat.write(
                self.heartbeat_path,
                heartbeat.build_record(
                    asset_key=self.asset_key,
                    registry_address=self.registry_address or "",
                    sequence=self._heartbeat_sequence,
                    now=self._moment(),
                    last_attempted_slot=self._last_attempted_slot,
                    last_successful_epoch=self._last_successful_epoch,
                    last_backup_at=self._last_backup_at,
                ),
            )
        except (HeartbeatError, OSError, ValueError):
            return

    def note_attempt(self, scheduled_at: datetime) -> None:
        """Record that this slot was attempted, whatever came of it."""
        stamped = utc_instant(scheduled_at, "scheduled_at")
        self._last_attempted_slot = stamped.isoformat().replace("+00:00", "Z")

    def note_success(self, scheduled_at: datetime) -> None:
        stamped = utc_instant(scheduled_at, "scheduled_at")
        self._last_successful_epoch = stamped.isoformat().replace("+00:00", "Z")

    def backup_if_due(self, scheduled_at: datetime) -> None:
        """Take the daily archive from inside the lock this daemon already holds.

        This is the cooperative path the backup module exists for. A second process
        copying a live workspace would read its files at several different moments; here
        the workspace is between mutations and nothing else can write to it.
        """
        if self.backup_dir is None or self.backup_key is None:
            return
        moment = utc_instant(scheduled_at, "scheduled_at")
        day = moment.date().isoformat()
        if self._last_backup_day == day:
            return
        try:
            if self._held is None:
                raise BackupError(
                    "the service is not holding its workspace lock; a cooperative "
                    "backup is only valid from inside the serving section"
                )
            root = Workspace(Path(self.operations.directory).parent)
            with exclusive_lock(root.evidence_lock) as evidence_held:
                archive = backup.create(
                    self._held,
                    root.root,
                    evidence_held=evidence_held,
                    now=moment,
                    key=self.backup_key,
                    asset_key=self.asset_key,
                    registry_address=self.registry_address or "",
                )
            # The asset key is a CAIP identifier and contains colons, which are not legal
            # in a Windows filename — so naming the archive after it directly produced a
            # backup that silently never appeared. The identity is authenticated inside
            # the archive anyway; the filename only has to be unique and legible.
            safe = self.asset_key.replace(":", "_")
            destination = Path(self.backup_dir) / f"{safe}-{day}.archive"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_bytes(archive)
            temporary.replace(destination)
        except (BackupError, LockUnavailable, OSError, ValueError) as error:
            # An incident, not a crash. A failed backup is a reliability problem to be
            # seen, and the slot that just ran is unaffected by it.
            self._record_backup_failure(str(error))
            return
        self._last_backup_day = day
        self._last_backup_at = moment.isoformat().replace("+00:00", "Z")

    def _record_backup_failure(self, detail: str) -> None:
        try:
            moment = self._moment()
            self.incidents.open_incident(
                asset_key=self.asset_key,
                kind=EPOCH_FAILED,
                detail=f"the daily backup could not be written: {detail}"[:500],
                occurred_at=moment,
                state=self._projected_state(moment),
            )
        except Exception:  # noqa: BLE001 - recording a failure must not raise a new one
            return

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
                self.client,
                expected_asset_key=self.asset_key,
                before_publish=self.before_publish,
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
        epoch_of: Callable[[datetime], str] | None = None,
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

        if epoch_of is not None and correction_of is None:
            settled = self._epoch_already_published(scheduled_at, epoch_of)
            if isinstance(settled, SlotOutcome):
                return settled

        try:
            signed_report = produce(scheduled_at)
        except SourceUnavailable as error:
            return self._record_incident(SOURCE_UNAVAILABLE, str(error), scheduled_at)
        except Exception as error:  # noqa: BLE001 - any epoch failure is an incident
            return self._record_incident(EPOCH_FAILED, str(error), scheduled_at)

        # Frozen before it is inspected, so what is checked is what is published — and
        # inside the boundary, because freezing can fail on a report carrying something
        # that is not JSON, and a direct caller of run_slot deserves the same recorded
        # outcome as everything else here.
        try:
            signed_report = (
                frozen_snapshot(signed_report, "signed_report")
                if signed_report is not None
                else None
            )
        except Exception as error:  # noqa: BLE001 - an unusable report is an epoch failure
            return self._record_incident(
                EPOCH_FAILED,
                f"the produced report cannot be represented: {error}",
                scheduled_at,
            )
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
            uri = report_uri(frozen_snapshot(signed_report, "signed_report"))
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
                    self.client,
                    expected_asset_key=self.asset_key,
                    before_publish=self.before_publish,
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
        self.note_success(scheduled_at)
        return SlotOutcome(
            scheduled_at=scheduled_at,
            published=True,
            incident_id=None,
            detail="published",
        )

    def _epoch_already_published(
        self, scheduled_at: datetime, epoch_of: Callable[[datetime], str]
    ) -> SlotOutcome | None:
        """Ask the chain whether this slot's epoch is already on it.

        A restart is the case this exists for, and the durable state cannot answer it: a
        clean process starts its first slot at `now()`, derives the same epoch, and reads
        the next sequence — which is correct, and would put a second signed report about one
        day on the chain. The registry refuses that outright, but arriving at the refusal
        means having fetched the issuer, evaluated, and signed first, and then recording a
        failure for something that is not one.

        Asked of the registry rather than remembered here, because a restored or wiped
        workspace remembers nothing and the chain is the only party that knows.
        """
        epoch_id = epoch_of(scheduled_at)
        try:
            published = self.client.backend.epoch_sequence(
                asset_key_bytes(self.asset_key), epoch_key_bytes(epoch_id)
            )
        except Exception as error:  # noqa: BLE001 - recorded, and the slot does not run
            # Not knowing whether the epoch is published is not permission to publish it.
            return self._record_incident(
                PUBLICATION_UNRESOLVED,
                f"the registry would not say whether {epoch_id} is already published: "
                f"{error}",
                scheduled_at,
            )
        if not published:
            return None
        # Not an incident. Nothing is wrong: the epoch this slot is about has a report on
        # the chain, which is the outcome the slot exists to produce. Opening EPOCH_FAILED
        # here would alert an operator every time a daemon restarted on a day it had
        # already served.
        #
        # Nothing is *closed* either. This path fetched no evidence and evaluated nothing,
        # so it has observed no recovery: closing without a kind filter retired open source
        # outages and epoch failures on the strength of a publication that happened before
        # they were opened. Recovery is something a successful retrieval establishes.
        self.note_success(scheduled_at)
        return SlotOutcome(
            scheduled_at=scheduled_at,
            published=False,
            incident_id=None,
            detail=f"epoch {epoch_id} was already published at sequence {published}",
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
                self.client,
                expected_asset_key=self.asset_key,
                before_publish=self.before_publish,
            )
        else:
            self._with_retry(
                lambda: self.operations.resolve(
                    self.client,
                    expected_asset_key=self.asset_key,
                    before_publish=self.before_publish,
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
        moment = self._moment()
        self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=EPOCH_FAILED,
            detail=f"the slot at {scheduled_at.isoformat()} failed unexpectedly: {error}",
            occurred_at=moment,
            state=self._projected_state(moment),
        )

    def record_clock_error(self, scheduled_at: datetime, error: BaseException) -> None:
        """Record that the schedule stopped because it could not name its next slot.

        Not an epoch failure. `record_escaped_failure` would write "the slot at X failed
        unexpectedly", and the slot at X had just succeeded — the schedule's *next* step
        is what could not be taken. An operator reading that would look for a broken epoch
        and find a working one.
        """
        moment = self._moment()
        self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=SCHEDULE_UNUSABLE,
            detail=(
                f"the schedule stopped after {scheduled_at.isoformat()}: {error}; "
                "no further slot can be scheduled until the cadence is corrected"
            ),
            occurred_at=moment,
            state=self._projected_state(moment),
        )

    def record_outage(self, first_missed: datetime, count: int) -> None:  # noqa: D401
        """One incident per outage, carrying the exact number of slots it covered.

        Recording each missed slot separately would write thousands of entries for a long
        outage and bury everything else in the log. The count is exact even though only
        one entry is written for it.
        """
        moment = self._moment()
        self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=SLOT_MISSED,
            detail=(
                f"{count} slot(s) did not run, from {first_missed.isoformat()}"
                if count > 1
                else f"the slot scheduled for {first_missed.isoformat()} did not run"
            ),
            occurred_at=moment,
            state=self._projected_state(moment),
        )

    # ----------------------------------------------------------------- internals
    def _with_retry(self, work: Callable[[], object]) -> object:
        """Retry only a transport failure, and only while nothing has been signed.

        Once a transaction exists in the journal, retrying is not this loop's decision to
        make: the publisher reconciles it against the chain, and a retry here would be a
        second opinion about a transaction that may already be on the wire.
        """
        attempt = 0
        delay = self.backoff_seconds
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
                self.sleep(delay)
                # Doubled, not exponentiated. `2 ** attempt` builds an integer that grows
                # without bound, and `0.0 * huge_int` raises OverflowError converting the
                # int — so a zero backoff, which is accepted precisely because it can never
                # grow, crashed the retry loop it was configured to make free. Doubling a
                # float keeps the runtime value inside the domain proved at construction.
                delay *= 2
                attempt += 1

    def _record_incident(
        self, kind: str, detail: str, scheduled_at: datetime
    ) -> SlotOutcome:
        moment = self._moment()
        entry = self.incidents.open_incident(
            asset_key=self.asset_key,
            kind=kind,
            detail=detail,
            occurred_at=moment,
            state=self._projected_state(moment),
        )
        return SlotOutcome(
            scheduled_at=scheduled_at,
            published=False,
            incident_id=entry["entry_hash"],
            detail=detail,
        )

    def _moment(self) -> datetime:
        """The single clock reading an incident is described by.

        `occurred_at` and the projected state are two facts about one event. Reading the
        clock for each of them let an incident be stamped 23:59:59 on one day and carry the
        state projected for the next — a record that is internally inconsistent, and only
        ever at the moment when the date boundary is what matters.
        """
        try:
            return utc_instant(self.now(), "now()")
        except ValueError as error:
            raise ValueError(f"now() must return an instant: {error}") from error

    def _projected_state(self, moment: datetime) -> str | None:
        state = self.operations.load_state(self.asset_key)
        if state is None:
            return None
        return state.projected(moment.astimezone(timezone.utc).date()).value

    def _close_open_incidents(
        self, detail: str, *, kinds: frozenset[str] | set[str] | None = None
    ) -> None:
        moment = self._moment()
        for incident in self.incidents.open_incidents(self.asset_key):
            if kinds is not None and incident.kind not in kinds:
                continue
            self.incidents.close_incident(
                incident.incident_id,
                detail=detail,
                occurred_at=moment,
                state=self._projected_state(moment),
            )


class BatchService:
    """Publish one asset-wide report and its policy reports under one slot lock."""

    def __init__(self, services: tuple[Service, ...]) -> None:
        if not services:
            raise ValueError("a batch service needs at least one service")
        self.services = services
        self.asset_key = services[0].asset_key
        self.lock_path = services[0].lock_path
        self.__held = None

    @property
    def _held(self):
        return self.__held

    @_held.setter
    def _held(self, held) -> None:
        self.__held = held
        for service in self.services:
            service._held = held

    def resolve_startup(self) -> SlotOutcome | None:
        outcomes = [service.resolve_startup() for service in self.services]
        return next((outcome for outcome in outcomes if outcome is not None), None)

    def beat(self) -> None:
        for service in self.services:
            service.beat()

    def note_attempt(self, scheduled_at: datetime) -> None:
        for service in self.services:
            service.note_attempt(scheduled_at)

    def backup_if_due(self, scheduled_at: datetime) -> None:
        for service in self.services:
            service.backup_if_due(scheduled_at)

    def record_escaped_failure(
        self, scheduled_at: datetime, error: BaseException
    ) -> None:
        for service in self.services:
            service.record_escaped_failure(scheduled_at, error)

    def record_clock_error(self, scheduled_at: datetime, error: BaseException) -> None:
        for service in self.services:
            service.record_clock_error(scheduled_at, error)

    def record_outage(self, first_missed: datetime, count: int) -> None:
        for service in self.services:
            service.record_outage(first_missed, count)

    def run_slot(
        self,
        scheduled_at: datetime,
        produce: Callable[[datetime], object | None],
        *,
        report_uri: Callable[[Mapping[str, object]], str],
        epoch_of: Callable[[datetime], str] | None = None,
    ) -> SlotOutcome:
        """Capture once, then let each child service own its durable publication."""
        if epoch_of is not None:
            settled = []
            for service in self.services:
                try:
                    service._resolve_outstanding()
                except Exception as error:  # noqa: BLE001 - the slot cannot proceed
                    return self._record_batch_incident(
                        PUBLICATION_UNRESOLVED,
                        f"an earlier publication is still unresolved: {error}",
                        scheduled_at,
                    )
                outcome = service._epoch_already_published(scheduled_at, epoch_of)
                if outcome is not None:
                    settled.append(outcome)
            if len(settled) == len(self.services):
                return settled[0]

        try:
            produced = produce(scheduled_at)
        except SourceUnavailable as error:
            return self._record_batch_incident(
                SOURCE_UNAVAILABLE, str(error), scheduled_at
            )
        except Exception as error:  # noqa: BLE001 - a capture failure is an incident
            return self._record_batch_incident(EPOCH_FAILED, str(error), scheduled_at)

        from touchstone.ustb_daemon import ProducedReports

        if not isinstance(produced, ProducedReports):
            return self._record_batch_incident(
                EPOCH_FAILED,
                "the policy producer did not return its complete report batch",
                scheduled_at,
            )
        by_asset = {
            report.get("report", {}).get("asset_key"): report
            for report in produced.reports
            if isinstance(report, Mapping) and isinstance(report.get("report"), Mapping)
        }
        expected_assets = {service.asset_key for service in self.services}
        if len(by_asset) != len(produced.reports) or set(by_asset) != expected_assets:
            return self._record_batch_incident(
                EPOCH_FAILED,
                "the policy producer returned an incomplete or mismatched report batch",
                scheduled_at,
            )
        outcomes = []
        for service in self.services:
            signed_report = by_asset.get(service.asset_key)
            outcomes.append(
                service.run_slot(
                    scheduled_at,
                    lambda _at, signed_report=signed_report: signed_report,
                    report_uri=report_uri,
                    epoch_of=epoch_of,
                )
            )
        first_failure = next(
            (outcome for outcome in outcomes if not outcome.published), None
        )
        if first_failure is not None:
            return first_failure
        return SlotOutcome(
            scheduled_at=scheduled_at,
            published=True,
            incident_id=None,
            detail=f"published {len(outcomes)} reports",
        )

    def _record_batch_incident(
        self, kind: str, detail: str, scheduled_at: datetime
    ) -> SlotOutcome:
        outcomes = [
            service._record_incident(kind, detail, scheduled_at)
            for service in self.services
        ]
        return outcomes[0]


def serve(
    service: Service,
    produce: Callable[[datetime], Mapping[str, object] | None],
    *,
    report_uri: Callable[[Mapping[str, object]], str],
    interval_seconds: float,
    max_runs: int | None = None,
    epoch_of: Callable[[datetime], str] | None = None,
    **schedule_arguments: object,
) -> ScheduleOutcome:
    """Resolve what was left in flight, then run slots until asked to stop."""
    with exclusive_lock(service.lock_path) as held:
        # The daemon's own proof of the lock, handed to the cooperative backup. The lock is
        # not reentrant, so this is the only way an in-daemon backup can demonstrate what a
        # standalone one demonstrates by acquiring.
        service._held = held
        return _serve_locked(
            service,
            produce,
            report_uri=report_uri,
            interval_seconds=interval_seconds,
            max_runs=max_runs,
            epoch_of=epoch_of,
            **schedule_arguments,
        )


def _beating_sleep(
    service: Service, sleep: Callable[[float], None]
) -> Callable[[float], None]:
    """Wait in heartbeat-sized steps, beating after each one.

    The scheduler's own sleep is preserved and called repeatedly rather than replaced, so
    an injected clock still controls the passage of time and the tests that drive one keep
    working. A wait shorter than one interval sleeps once and beats once.
    """

    def wait(seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(remaining, heartbeat.DEFAULT_INTERVAL_SECONDS)
            sleep(step)
            remaining -= step
            service.beat()

    return wait


def _serve_locked(
    service: Service,
    produce: Callable[[datetime], Mapping[str, object] | None],
    *,
    report_uri: Callable[[Mapping[str, object]], str],
    interval_seconds: float,
    max_runs: int | None = None,
    epoch_of: Callable[[datetime], str] | None = None,
    **schedule_arguments: object,
) -> ScheduleOutcome:
    service.resolve_startup()
    # Only now. A heartbeat written before reconciliation would report a healthy daemon
    # during the one window where the durable state has not yet been settled — which is
    # precisely when an operator most needs to know the service is not ready.
    service.beat()

    def slot(scheduled_at: datetime) -> None:
        try:
            service.run_slot(
                scheduled_at, produce, report_uri=report_uri, epoch_of=epoch_of
            )
        finally:
            # In the `finally`, because a failed slot is exactly when the heartbeat has to
            # record that an attempt was made. Recording only successes would leave a
            # failing service looking identical to an idle one.
            # Backup before the beat, so the heartbeat reports the archive that was just
            # taken rather than the previous day's. A watchdog alerting on a backup older
            # than 24h would otherwise fire on the strength of stale bookkeeping.
            service.note_attempt(scheduled_at)
            service.backup_if_due(scheduled_at)
            service.beat()

    # The heartbeat cannot ride on the publication cadence. Slots are a day apart and a
    # heartbeat expires in three minutes, so a daemon that beat only around slots reported
    # itself dead for twenty-three of every twenty-four hours — and the serve test used a
    # sixty-second interval and inspected immediately, so it never saw the idle period the
    # service actually spends its life in. Liveness is emitted on its own clock, by
    # subdividing the wait rather than by adding a thread that would beat on beside a
    # crashed scheduler.
    schedule_arguments["sleep"] = _beating_sleep(
        service, schedule_arguments.pop("sleep", time.sleep)
    )
    return run_schedule(
        slot,
        interval_seconds=interval_seconds,
        max_runs=max_runs,
        # Composed, not chosen. Letting a caller's handler *replace* the service's meant
        # supplying one silently switched off the incident recording — an escaped failure
        # would call the caller back and write nothing down.
        on_outage=_also(
            service.record_outage, schedule_arguments.pop("on_outage", None)
        ),
        on_failure=_also(
            service.record_escaped_failure, schedule_arguments.pop("on_failure", None)
        ),
        on_clock_error=_also(
            service.record_clock_error, schedule_arguments.pop("on_clock_error", None)
        ),
        **schedule_arguments,
    )


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
    quorum = QuorumRPC.from_env()
    if quorum is None:
        backend = SignedRegistryBackend(manifest, PublisherKey.from_env(manifest))
    else:
        backend = SignedRegistryBackend(
            manifest,
            PublisherKey.from_env(manifest),
            quorum=quorum,
        )
    root = Workspace(workspace)
    client = PublisherClient(
        backend,
        TransparencyLog(root.transparency_log),
        root.pending_journal,
    )
    # The backup key is optional and read here rather than demanded: a service that
    # refused to start for want of a backup destination would turn a missing archive into
    # an outage. Its absence is visible in the heartbeat, which never records a backup.
    key = None
    if os.environ.get(backup.BACKUP_KEY_ENV):
        key = backup.backup_key()
    return Service(
        client,
        OperationsStore(root.operations),
        IncidentLog(root.incidents),
        asset_key=asset_key,
        lock_path=root.lock,
        heartbeat_path=root.heartbeat,
        registry_address=manifest.registry_address,
        backup_dir=os.environ.get("TOUCHSTONE_BACKUP_DIR") or None,
        backup_key=key,
        # Recovery republishes stored signed bytes without consulting a bundle, so a pending
        # operation whose bundle is missing, truncated or describes another report would go
        # on chain unverifiable. A fresh slot cannot reach that state — it writes and verifies
        # the bundle before the report is returned — but a legacy or externally written
        # operation can.
        before_publish=require_verifying_bundle(root.bundles, manifest.chain_id),
    )


def _serve_ustb(service: Service, arguments) -> int:
    """Run the unattended USTB loop: the mode this service refused to offer until now.

    Everything it composes already existed and was already audited. What did not exist was
    anything calling it on a schedule, so the honest refusal that stood here was accurate
    right up until this function replaced it.
    """
    manifest = DeploymentManifest.load(arguments.manifest)
    descriptor = get_asset(arguments.asset_key)
    # The public-network refusal is not here. It lives in `main`, ahead of `build_service`,
    # because construction reads the EVM publisher key and this check must hold on a host
    # that has none. Keeping a copy here as well left two guards for one rule, and a mutant
    # that disabled this one survived because the other still caught it — which is exactly
    # how a duplicated rule stops being tested.
    transport = LiveTransport(user_agent=arguments.source_user_agent)
    if arguments.fixtures:
        if descriptor is not USTB:
            print(
                "SERVICE FAIL: committed fixture mode currently supports USTB only",
                file=sys.stderr,
            )
            return 1
        from touchstone.epoch import FixtureTransport

        try:
            transport = FixtureTransport(
                arguments.fixtures, date.fromisoformat(arguments.fixture_capture)
            )
        except (TypeError, ValueError) as error:
            print(
                f"SERVICE FAIL: --fixture-capture must name a committed capture: {error}",
                file=sys.stderr,
            )
            return 1

    signer = Ed25519Signer.from_env()
    if signer.kid != manifest.active_key.kid:
        # The manifest names the key the registry's consumers will verify against. Signing
        # with anything else produces reports that verify for nobody.
        print(
            f"SERVICE FAIL: the signing seed derives {signer.kid}, but the manifest's "
            f"active reporting key is {manifest.active_key.kid}",
            file=sys.stderr,
        )
        return 1

    policy_paths = tuple(arguments.policy_manifest or ())
    policy_workspace_paths = tuple(arguments.policy_workspace or ())
    try:
        ledger = load_approval_ledger()
        policies = tuple(
            load_policy(path, approved=default_controls(descriptor, ledger))
            for path in policy_paths
        )
        for policy in policies:
            if policy.asset_key != arguments.asset_key:
                raise PolicyError(
                    f"policy {policy.policy_id} is bound to {policy.asset_key}, not "
                    f"{arguments.asset_key}"
                )
    except (PolicyError, OSError, ValueError) as error:
        print(f"SERVICE FAIL: {error}", file=sys.stderr)
        return 1

    workspace = Workspace(arguments.workspace)
    store = EvidenceStore(workspace.evidence)
    key_bytes = asset_key_bytes(arguments.asset_key)

    def next_sequence() -> int:
        # Asked of the chain, never counted locally. A local counter drifts from the
        # registry the first time a publication fails, and the registry refuses an
        # out-of-order sequence — so the drift would surface as a permanent outage.
        return service.client.backend.latest_sequence(key_bytes) + 1

    def previous_state(on: date) -> AssetState:
        state = service.operations.load_state(arguments.asset_key)
        return AssetState.UNVERIFIABLE if state is None else state.projected(on)

    if policies:
        policy_services = tuple(
            build_service(arguments.manifest, workspace_path, asset_key=policy.key)
            for policy, workspace_path in zip(policies, policy_workspace_paths)
        )
        for policy_service in policy_services:
            policy_service.before_publish = require_verifying_bundle(
                workspace.bundles, manifest.chain_id
            )
        services = (service, *policy_services)
        service_by_key = {item.asset_key: item for item in services}
        manifests = {
            (policy.policy_id, policy.version): Path(path).read_bytes()
            for policy, path in zip(policies, policy_paths)
        }

        def next_sequence_for(key: str) -> int:
            return service.client.backend.latest_sequence(asset_key_bytes(key)) + 1

        def previous_state_for(key: str, on: date) -> AssetState:
            state = service_by_key[key].operations.load_state(key)
            return AssetState.UNVERIFIABLE if state is None else state.projected(on)

        served_service: Service | BatchService = BatchService(services)
        producer = make_producer(
            store=store,
            signer=signer,
            next_sequence=next_sequence,
            previous_state=previous_state,
            next_sequence_for=next_sequence_for,
            previous_state_for=previous_state_for,
            policies=policies,
            policy_manifests=manifests,
            transport=transport,
            bundle_sink=write_bundle(workspace.bundles, manifest.chain_id),
            asset=descriptor,
        )
    else:
        served_service = service
        producer = make_producer(
            store=store,
            signer=signer,
            next_sequence=next_sequence,
            previous_state=previous_state,
            transport=transport,
            # Every published report gets an offline verification bundle written beside the
            # workspace's other durable state. Without this the service published reports a
            # reader had no way to check, which is the one claim the project rests on.
            bundle_sink=write_bundle(workspace.bundles, manifest.chain_id),
            asset=descriptor,
        )

    outcome = serve(
        served_service,
        producer,
        report_uri=report_uri,
        interval_seconds=arguments.interval_seconds,
        max_runs=arguments.max_runs,
        # The same derivation the producer names its report with, so the question asked of
        # the registry is about the epoch the slot would actually publish.
        epoch_of=lambda scheduled_at: epoch_id_for(scheduled_at, descriptor),
    )
    print(
        json.dumps(
            {
                "completed": outcome.completed,
                "failed": len(outcome.failed),
                "missed": outcome.missed_count,
                "clock_error": outcome.clock_error,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if outcome.completed and not outcome.failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--asset-key", required=True)
    parser.add_argument(
        "--source-user-agent",
        default=None,
        help="identifying HTTP User-Agent; required for SEC-backed assets",
    )
    parser.add_argument(
        "--policy-manifest",
        action="append",
        default=[],
        help="policy manifest; repeat with --policy-workspace for policy publication",
    )
    parser.add_argument(
        "--policy-workspace",
        action="append",
        default=[],
        help="durable workspace for the matching policy manifest",
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="settle any publication left in flight and stop, signing nothing new",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=86_400.0,
        help="seconds between slots; one day by default, because an epoch is a statement "
        "about a particular day's evidence",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="stop after this many slots. Omit to serve until stopped, which is the "
        "unattended mode; 1 is the canary.",
    )
    parser.add_argument(
        "--fixtures",
        default=None,
        help="serve from committed fixtures instead of the live sources. For rehearsing "
        "the USTB unattended path without touching an issuer endpoint. Local manifests "
        "only; FOBXX fixture service mode is not implemented.",
    )
    parser.add_argument(
        "--fixture-capture",
        default=None,
        help="which committed capture to serve, as an ISO date. Required with --fixtures "
        "and deliberately without a default: the wrong capture rehearses a path that "
        "cannot publish, and silently.",
    )
    arguments = parser.parse_args(argv)
    if arguments.fixture_capture and not arguments.fixtures:
        parser.error("--fixture-capture is meaningless without --fixtures")
    if arguments.fixtures and not arguments.fixture_capture:
        parser.error("--fixtures requires --fixture-capture")
    if arguments.policy_workspace and not arguments.policy_manifest:
        parser.error("--policy-workspace requires --policy-manifest")
    if len(arguments.policy_manifest) != len(arguments.policy_workspace):
        parser.error("each --policy-manifest requires one --policy-workspace")
    # Before anything reads a key or touches the network. `build_service` constructs the
    # publisher, which reads TOUCHSTONE_PUBLISHER_PRIVATE_KEY — so the fixture-mode refusal
    # that lived inside `_serve_ustb` was never reached on a host without that key, and the
    # operator got "TOUCHSTONE_PUBLISHER_PRIVATE_KEY is not set" instead of the thing that
    # actually mattered. Its test called `_serve_ustb` directly and so never exercised the
    # ordering the CLI really has.
    try:
        manifest = DeploymentManifest.load(arguments.manifest)
    except (DeploymentError, OSError, ValueError) as error:
        print(f"SERVICE FAIL: {error}", file=sys.stderr)
        return 1
    if not manifest.is_active:
        # A superseded deployment cannot be caught by preflight: it compares deployed code
        # against the digest this manifest records, and this manifest records that very
        # deployment's digest, so they agree. Only the declared state refuses it.
        print(
            f"SERVICE FAIL: {arguments.manifest} is marked "
            f"{manifest.deployment_state!r}; nothing may be published to it",
            file=sys.stderr,
        )
        return 1
    if arguments.fixtures and not manifest.is_local:
        print(
            f"SERVICE FAIL: fixture mode is a local rehearsal; {manifest.network} is a "
            "public network and must be served from live sources",
            file=sys.stderr,
        )
        return 1
    try:
        # Construction is where the workspace is judged usable at all: a log that is not a
        # file, a directory that is not a directory, an incident log reachable by two
        # names. Those are deliberate refusals, and they were reaching the operator as an
        # uncaught traceback rather than as this service's own failure line — a new way to
        # fail that the startup contract did not cover.
        service = build_service(
            arguments.manifest, arguments.workspace, asset_key=arguments.asset_key
        )
    except (DeploymentError, IdentityError, OSError, ValueError) as error:
        print(f"SERVICE FAIL: {error}", file=sys.stderr)
        return 1
    try:
        if not arguments.resolve_only:
            return _serve_ustb(service, arguments)
        with exclusive_lock(service.lock_path):
            outcome = service.resolve_startup()
    except (
        DeploymentError,
        IdentityError,
        LockUnavailable,
        OperationsError,
        PublicationError,
        ValueError,
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
