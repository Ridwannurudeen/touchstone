"""The service's ordering rules, which are the only thing standing between a fault and a
double publication."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from touchstone.incidents import (
    EPOCH_FAILED,
    SCHEDULE_UNUSABLE,
    PUBLICATION_UNRESOLVED,
    SOURCE_UNAVAILABLE,
    IncidentLog,
)
from touchstone.operations import OperationsStore, UnresolvedPublication
from touchstone.publish import PublisherClient, TransportUnavailable
from touchstone.signing import Ed25519Signer
from touchstone.translog import TransparencyLog

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from run_service import Service, SourceUnavailable, serve  # noqa: E402

from test_publish import ASSET_KEY_OF, FakeBackend, _signed_report  # noqa: E402


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)


def build(tmp_path: Path, backend: FakeBackend, **overrides) -> Service:
    client = PublisherClient(
        backend,
        TransparencyLog(tmp_path / "transparency.jsonl"),
        tmp_path / "pending.json",
    )
    arguments = {
        "asset_key": ASSET_KEY_OF,
        "sleep": lambda seconds: None,
        "now": lambda: AT,
    }
    arguments.update(overrides)
    return Service(
        client,
        OperationsStore(tmp_path / "operations", now=lambda: AT),
        IncidentLog(tmp_path / "incidents.jsonl"),
        **arguments,
    )


def uri(signed_report) -> str:
    return f"urn:touchstone:report:{signed_report['report']['sequence']}"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_a_slot_resolves_an_earlier_publication_before_it_fetches_anything(
    tmp_path: Path,
) -> None:
    """The invariant cannot live only in startup.

    The scheduler deliberately keeps going after a failed slot, so a publication left
    unresolved by slot one would otherwise be discovered by slot two only *after* it had
    already fetched evidence and signed a report.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    produced: list[datetime] = []

    def produce(scheduled_at: datetime):
        produced.append(scheduled_at)
        return _signed_report(len(produced))

    # Make the first publication fail after the operation is durably recorded.
    def refuse(prepared):
        raise TransportUnavailable("rpc down during publication")

    backend.broadcast = refuse
    clock = Clock()

    outcome = serve(
        service,
        produce,
        report_uri=uri,
        interval_seconds=60,
        max_runs=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: AT,
    )

    assert len(produced) == 1, (
        "the second slot must not fetch or sign while the first is unresolved"
    )
    assert outcome.completed == 2, "the schedule itself keeps running"
    incidents = IncidentLog(tmp_path / "incidents.jsonl").open_incidents()
    assert [i.kind for i in incidents] == [
        PUBLICATION_UNRESOLVED,
        PUBLICATION_UNRESOLVED,
    ]


def test_a_transport_failure_during_publication_is_retried(tmp_path: Path) -> None:
    """The retry has to sit where the failure arises.

    It wrapped the producer, and every transport failure this service can actually suffer
    arises later, during preflight and submission — so submission was never retried once.
    """
    backend = FakeBackend()
    slept: list[float] = []
    service = build(tmp_path, backend, sleep=slept.append, retries=3)
    attempts = {"count": 0}
    real_prepare = backend.prepare

    def flaky(asset_key, report, report_uri, correction_of):
        # Injected at preparation, which is where preflight runs and where a transport
        # failure can still be retried: nothing has been signed or journalled yet. A
        # failure at broadcast is deliberately *not* retryable, and the test below covers
        # that.
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise TransportUnavailable("rpc briefly unreachable")
        return real_prepare(asset_key, report, report_uri, correction_of)

    backend.prepare = flaky

    outcome = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert outcome.published is True
    assert attempts["count"] == 3, "it kept trying"
    assert slept == [2.0, 4.0], "and backed off between attempts"
    assert len(backend.submissions) == 1, "landing exactly one publication"


def test_retrying_stops_once_a_transaction_has_been_journalled(
    tmp_path: Path,
) -> None:
    """Past the journal, resending is the publisher's decision and not a loop's."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.client._write_pending(
        {
            "asset_key": "eip155:1:0x" + "11" * 20,
            "chain_id": 31337,
            "correction_of": None,
            "nonce": 0,
            "publisher_address": backend.manifest.publisher_address,
            "raw_transaction": "aa",
            "registry_address": backend.manifest.registry_address,
            "report_sha256": "cd" * 32,
            "report_uri": "urn:touchstone:report:1",
            "sequence": 1,
            "transaction_hash": "0x" + "ab" * 32,
        }
    )

    with pytest.raises(UnresolvedPublication, match="reconciled, not retried"):
        service._with_retry(lambda: None)


def test_a_permanent_refusal_is_not_retried(tmp_path: Path) -> None:
    backend = FakeBackend()
    slept: list[float] = []
    service = build(tmp_path, backend, sleep=slept.append)
    attempts = {"count": 0}

    def refuse():
        attempts["count"] += 1
        raise ValueError("the registry would reject this publication")

    with pytest.raises(ValueError):
        service._with_retry(refuse)
    assert attempts["count"] == 1
    assert slept == []


def test_state_is_written_before_the_operation_is_forgotten(tmp_path: Path) -> None:
    """The crash window between a settled publication and its projection.

    Clearing first left the chain and the transparency log final, the operation gone, the
    projection showing the previous epoch, and nothing left to reconcile from.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    operations = service.operations
    cleared = {"called": False}

    def clear_and_die() -> None:
        cleared["called"] = True
        raise RuntimeError("crash after the publication settled")

    operations.clear_operation = clear_and_die

    outcome = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert cleared["called"], "the crash happened at the clear, not before it"
    assert outcome.published is False, "and it was recorded rather than propagated"
    state = operations.load_state(ASSET_KEY_OF)
    assert state is not None, "the projection was already durable when the crash hit"
    assert state.sequence == 1


def test_a_source_failure_publishes_nothing_and_opens_an_incident(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    service = build(tmp_path, backend)

    def unavailable(scheduled_at):
        raise SourceUnavailable("the feed returned 403")

    outcome = service.run_slot(AT, unavailable, report_uri=uri)

    assert outcome.published is False
    assert backend.submissions == [], "nothing reached the chain"
    assert service.operations.load_operation() is None
    incidents = service.incidents.open_incidents()
    assert [i.kind for i in incidents] == [SOURCE_UNAVAILABLE]
    assert "403" in incidents[0].detail


def test_incomplete_evidence_produces_no_report_at_all(tmp_path: Path) -> None:
    """Absence of evidence must never be rendered as an observation."""
    backend = FakeBackend()
    service = build(tmp_path, backend)

    outcome = service.run_slot(AT, lambda at: None, report_uri=uri)

    assert outcome.published is False
    assert backend.prepared == 0, "nothing was signed"
    assert backend.submissions == []
    assert len(service.client.transparency_log.verify()) == 0


def test_recovery_closes_the_incident_that_recorded_the_failure(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    service = build(tmp_path, backend)

    def unavailable(scheduled_at):
        raise SourceUnavailable("the feed returned 403")

    service.run_slot(AT, unavailable, report_uri=uri)
    assert len(service.incidents.open_incidents()) == 1

    service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert service.incidents.open_incidents() == []
    entries = service.incidents.verify()
    assert entries[0]["closes"] is None
    assert entries[1]["closes"] == entries[0]["entry_hash"], (
        "the closure references the incident rather than editing it"
    )


def test_a_journalled_publication_can_still_reconcile_on_a_later_slot(
    tmp_path: Path,
) -> None:
    """Refusing to *retry* must not mean refusing to *reconcile*.

    Routing reconciliation through the retry loop meant the loop's own guard fired before
    the work ran, so a publication that timed out waiting for a receipt could never settle
    while the daemon stayed alive — every later slot recorded another incident and never
    once asked the publisher to look at the chain.
    """
    backend = FakeBackend()
    backend.time_out_once = True
    service = build(tmp_path, backend)

    first = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)
    assert first.published is False, "the receipt wait timed out"
    assert service.client.pending_transaction() is not None
    assert service.operations.load_operation() is not None

    # The transaction did confirm; the next slot must notice.
    second = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert service.operations.load_operation() is None, "it reconciled"
    assert service.client.pending_transaction() is None
    assert len(backend.submissions) == 1, "and published nothing extra"
    assert second.published is True


def test_a_settled_sequence_under_another_uri_is_not_accepted_as_ours(
    tmp_path: Path,
) -> None:
    """A duplicate sequence proves the slot is taken, not that this operation filled it.

    The report URI is not inside the signed report, so comparing the signed report alone
    let an operation under a different URI be cleared as settled while the chain held the
    first one.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)
    assert len(backend.submissions) == 1

    # The same report, recorded for publication under a different URI.
    service.operations.begin_operation(
        _signed_report(1),
        report_uri="urn:touchstone:report:elsewhere",
        correction_of=None,
        scheduled_for=AT,
    )

    with pytest.raises(UnresolvedPublication, match="does not match this operation"):
        service.operations.resolve(service.client)
    assert service.operations.load_operation() is not None, "kept for review"


def test_a_chain_report_that_differs_in_its_roots_is_not_accepted_as_ours(
    tmp_path: Path,
) -> None:
    """The URI is not the only field that can differ, and the log alone cannot tell.

    Here the transparency log *does* hold this exact report, so the log check passes; the
    chain is what disagrees. Comparing only the URI and the lineage would have cleared the
    operation and saved local state for roots the registry does not contain.
    """
    import dataclasses

    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)
    asset_key = next(iter(backend.reports))

    # The chain now says something different from what we published and logged.
    backend.reports[asset_key][0] = dataclasses.replace(
        backend.reports[asset_key][0],
        control_set_root="ee" * 32,
        evidence_root="ff" * 32,
    )
    service.operations.begin_operation(
        _signed_report(1),
        report_uri=uri(_signed_report(1)),
        correction_of=None,
        scheduled_for=AT,
    )

    with pytest.raises(UnresolvedPublication, match="does not match this operation"):
        service.operations.resolve(service.client)
    assert service.operations.load_operation() is not None, "kept for review"


def test_a_report_absent_from_our_log_is_not_accepted_as_ours(tmp_path: Path) -> None:
    """A different report at the same sequence is not this operation settling."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    other = _signed_report(1, control_set_root="ee" * 32, evidence_root="ff" * 32)
    service.operations.begin_operation(
        other, report_uri=uri(other), correction_of=None, scheduled_for=AT
    )

    with pytest.raises(UnresolvedPublication, match="no record of publishing it"):
        service.operations.resolve(service.client)
    assert service.operations.load_operation() is not None


def test_settling_a_publication_does_not_claim_the_source_recovered(
    tmp_path: Path,
) -> None:
    """Closing every incident kind asserted a recovery nobody observed."""
    backend = FakeBackend()
    service = build(tmp_path, backend)

    def unavailable(scheduled_at):
        raise SourceUnavailable("the feed returned 403")

    service.run_slot(AT, unavailable, report_uri=uri)
    service.operations.begin_operation(
        _signed_report(1),
        report_uri=uri(_signed_report(1)),
        correction_of=None,
        scheduled_for=AT,
    )

    service.resolve_startup()

    open_kinds = [i.kind for i in service.incidents.open_incidents()]
    assert SOURCE_UNAVAILABLE in open_kinds, (
        "the source incident stays open: settling a publication says nothing about it"
    )


def test_a_stale_publication_incident_is_closed_when_nothing_is_outstanding(
    tmp_path: Path,
) -> None:
    """A run that died between clearing the operation and closing its incident."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.incidents.open_incident(
        asset_key=ASSET_KEY_OF,
        kind=PUBLICATION_UNRESOLVED,
        detail="left over from a run that died",
        occurred_at=AT,
    )

    assert service.resolve_startup() is None
    assert service.incidents.open_incidents() == [], "the stale incident was closed"


def test_a_failure_recording_the_operation_is_still_recorded(tmp_path: Path) -> None:
    """Naming the report and writing the operation are inside the boundary now."""
    backend = FakeBackend()
    service = build(tmp_path, backend)

    def bad_uri(signed_report):
        raise RuntimeError("disk full")

    outcome = service.run_slot(AT, lambda at: _signed_report(1), report_uri=bad_uri)

    assert outcome.published is False
    # EPOCH_FAILED, not PUBLICATION_UNRESOLVED. Nothing was handed to the publisher, so
    # filing it as a publication incident made startup close it the moment it saw no
    # operation and no journal — declaring recovered a failure nobody had touched.
    assert [i.kind for i in service.incidents.open_incidents()] == [EPOCH_FAILED]
    assert backend.submissions == []


def test_two_services_cannot_share_one_workspace(tmp_path: Path) -> None:
    """The check-to-produce window is only closed by a lock spanning both."""
    from touchstone.locking import LockUnavailable, exclusive_lock

    backend = FakeBackend()
    service = build(tmp_path, backend)
    clock = Clock()

    with exclusive_lock(service.lock_path):
        with pytest.raises(LockUnavailable):
            serve(
                service,
                lambda at: _signed_report(1),
                report_uri=uri,
                interval_seconds=60,
                max_runs=1,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
                now=lambda: AT,
            )
    assert backend.submissions == [], "the second service did no work at all"


def test_startup_does_not_close_a_failure_that_never_reached_the_publisher(
    tmp_path: Path,
) -> None:
    """The false-closure this arrangement is built to avoid.

    A URI failure leaves no operation and no journal. If it were filed as a publication
    incident, startup would see both absent and close it — reporting a recovery for a
    cause nobody had touched.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)

    def bad_uri(signed_report):
        raise RuntimeError("disk full")

    service.run_slot(AT, lambda at: _signed_report(1), report_uri=bad_uri)
    assert len(service.incidents.open_incidents()) == 1

    service.resolve_startup()

    assert [i.kind for i in service.incidents.open_incidents()] == [EPOCH_FAILED], (
        "the failure stays open until something actually addresses it"
    )


def test_a_journal_without_an_operation_stops_the_slot_before_it_produces(
    tmp_path: Path,
) -> None:
    """Either durable layer saying 'unresolved' must stop production, not just one.

    A journal with no operation was ignored by the resolution gate, so the slot fetched
    and signed and only then met the refusal — after exactly the work that must not
    happen first.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.client._write_pending(
        {
            "asset_key": ASSET_KEY_OF,
            "chain_id": 31337,
            "correction_of": None,
            "nonce": 0,
            "publisher_address": backend.manifest.publisher_address,
            "raw_transaction": "aa",
            "registry_address": backend.manifest.registry_address,
            "report_sha256": "cd" * 32,
            "report_uri": "urn:touchstone:report:1",
            "sequence": 1,
            "transaction_hash": "0x" + "ab" * 32,
        }
    )
    produced = []

    outcome = service.run_slot(
        AT,
        lambda at: produced.append(at) or _signed_report(1),
        report_uri=uri,
    )

    assert produced == [], "nothing was fetched or signed"
    assert outcome.published is False
    assert [i.kind for i in service.incidents.open_incidents()] == [
        PUBLICATION_UNRESOLVED
    ]


def test_startup_refuses_a_journal_with_no_operation(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.client._write_pending(
        {
            "asset_key": ASSET_KEY_OF,
            "chain_id": 31337,
            "correction_of": None,
            "nonce": 0,
            "publisher_address": backend.manifest.publisher_address,
            "raw_transaction": "aa",
            "registry_address": backend.manifest.registry_address,
            "report_sha256": "cd" * 32,
            "report_uri": "urn:touchstone:report:1",
            "sequence": 1,
            "transaction_hash": "0x" + "ab" * 32,
        }
    )

    with pytest.raises(UnresolvedPublication, match="journalled with no operation"):
        service.resolve_startup()

    # And it is written down. An aborted startup that leaves no trace is exactly what the
    # incident log exists to prevent; asserting only the exception proved nothing about it.
    assert [i.kind for i in service.incidents.open_incidents()] == [
        PUBLICATION_UNRESOLVED
    ]


def test_a_caller_handler_runs_as_well_as_the_incident_record_not_instead(
    tmp_path: Path,
) -> None:
    """Supplying a handler must not switch off the recording.

    Choosing between the caller's handler and the service's meant that passing one
    silently disabled the incident write — an escaped failure called the caller back and
    left nothing in the log. The earlier version of this test ran a *successful* slot and
    asserted an empty list, which proved only that the keyword no longer collided.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    clock = Clock()
    seen = []

    def explode(
        scheduled_at, produce, *, report_uri, correction_of=None, epoch_of=None
    ):
        raise RuntimeError("the whole slot fell over")

    # Make run_slot itself fail, so the failure escapes to the scheduler.
    service.run_slot = explode

    outcome = serve(
        service,
        lambda at: _signed_report(1),
        report_uri=uri,
        interval_seconds=60,
        max_runs=1,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: AT,
        on_failure=lambda at, error: seen.append(error),
    )

    assert outcome.failed, "the slot did fail"
    assert [str(error) for error in seen] == ["the whole slot fell over"], (
        "the caller's handler ran"
    )
    assert [i.kind for i in service.incidents.open_incidents()] == [EPOCH_FAILED], (
        "and the incident was still recorded"
    )


def test_a_durable_operation_for_another_asset_is_refused(tmp_path: Path) -> None:
    """The upgrade path the produced-report check alone leaves open.

    An operation persisted for another asset before a crash is still on disk afterwards,
    and whichever service starts next would resolve and publish it. The record on disk
    needs the same check as the record in memory.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    # A service for a different asset wrote this operation before dying.
    OperationsStore(tmp_path / "operations", now=lambda: AT).begin_operation(
        _signed_report(1),
        report_uri="urn:touchstone:report:1",
        correction_of=None,
        scheduled_for=AT,
    )
    service.asset_key = "eip155:1:0x" + "99" * 20

    with pytest.raises(UnresolvedPublication, match="configured for"):
        service.resolve_startup()

    assert backend.submissions == [], "nothing was published for the other asset"
    assert [i.kind for i in service.incidents.open_incidents()] == [
        PUBLICATION_UNRESOLVED
    ]


def test_a_slot_refuses_a_durable_operation_for_another_asset(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = build(tmp_path, backend)
    OperationsStore(tmp_path / "operations", now=lambda: AT).begin_operation(
        _signed_report(1),
        report_uri="urn:touchstone:report:1",
        correction_of=None,
        scheduled_for=AT,
    )
    service.asset_key = "eip155:1:0x" + "99" * 20
    produced = []

    outcome = service.run_slot(
        AT, lambda at: produced.append(at) or _signed_report(1), report_uri=uri
    )

    assert produced == [], "nothing was fetched"
    assert outcome.published is False
    assert backend.submissions == []


def test_a_produced_report_for_another_asset_is_refused(tmp_path: Path) -> None:
    """A producer returning the wrong asset was published under it, and then this
    asset's incidents were closed — a recovery recorded for something unobserved."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.asset_key = "eip155:1:0x" + "99" * 20

    outcome = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert outcome.published is False
    assert backend.submissions == []
    assert [i.kind for i in service.incidents.open_incidents()] == [EPOCH_FAILED]


def test_an_unreadable_operation_at_startup_is_recorded(tmp_path: Path) -> None:
    """The failure most worth seeing: "I cannot tell what was in flight"."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.operations.operation_path.write_bytes(b"{not json\n")

    with pytest.raises(UnresolvedPublication, match="cannot be read"):
        service.resolve_startup()

    assert [i.kind for i in service.incidents.open_incidents()] == [
        PUBLICATION_UNRESOLVED
    ]


def test_a_failing_journal_read_at_startup_is_recorded(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = build(tmp_path, backend)

    def unreadable():
        raise RuntimeError("the journal cannot be read")

    service.client.pending_transaction = unreadable

    with pytest.raises(UnresolvedPublication, match="cannot be read"):
        service.resolve_startup()

    incidents = service.incidents.open_incidents()
    assert [i.kind for i in incidents] == [PUBLICATION_UNRESOLVED]
    assert incidents[0].asset_key == ASSET_KEY_OF
    assert "cannot be read" in incidents[0].detail, (
        "counting incidents says nothing about whether the right one was written"
    )


def test_a_failing_recorder_does_not_replace_the_failure_it_describes(
    tmp_path: Path,
) -> None:
    """Whatever goes wrong writing the incident, the original error is the one to report."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.client._write_pending(
        {
            "asset_key": ASSET_KEY_OF,
            "chain_id": 31337,
            "correction_of": None,
            "nonce": 0,
            "publisher_address": backend.manifest.publisher_address,
            "raw_transaction": "aa",
            "registry_address": backend.manifest.registry_address,
            "report_sha256": "cd" * 32,
            "report_uri": "urn:touchstone:report:1",
            "sequence": 1,
            "transaction_hash": "0x" + "ab" * 32,
        }
    )

    def unavailable(**arguments):
        raise RuntimeError("incident log unavailable")

    service.incidents.open_incident = unavailable

    with pytest.raises(UnresolvedPublication, match="journalled with no operation"):
        service.resolve_startup()


def test_reconciliation_closes_its_incident_even_if_the_next_producer_fails(
    tmp_path: Path,
) -> None:
    """Closing at the end of a successful slot left it open whenever the slot failed.

    The publication was settled; reporting the service as still stuck on it because the
    *next* thing went wrong describes a state that does not exist.
    """
    backend = FakeBackend()
    backend.drop_first_broadcast = True
    service = build(tmp_path, backend)

    first = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)
    assert first.published is False, "the broadcast was dropped"
    assert [i.kind for i in service.incidents.open_incidents()] == [
        PUBLICATION_UNRESOLVED
    ]
    assert service.operations.load_operation() is not None

    def unavailable(scheduled_at):
        raise SourceUnavailable("the feed returned 403")

    # This slot resolves the outstanding publication first, then its producer fails.
    service.run_slot(AT, unavailable, report_uri=uri)

    assert service.operations.load_operation() is None, "the publication settled"
    kinds = [i.kind for i in service.incidents.open_incidents()]
    assert kinds == [SOURCE_UNAVAILABLE], (
        f"only the new failure stays open; the settled publication's incident was "
        f"closed by the resolution that earned it, got {kinds}"
    )


@pytest.mark.parametrize("backoff", [float("nan"), float("inf"), -1, True])
def test_a_non_finite_backoff_is_refused(tmp_path: Path, backoff: object) -> None:
    backend = FakeBackend()

    with pytest.raises(ValueError, match="backoff_seconds"):
        build(tmp_path, backend, backoff_seconds=backoff)


def test_an_operation_swapped_between_the_check_and_the_publish_is_refused(
    tmp_path: Path,
) -> None:
    """Check and use must be the same object, or the check is decoration.

    Validating a loaded operation and then calling a resolve that re-reads the file means
    two different objects: whatever replaced it in between was published unchecked. A
    static file cannot show this, so the file changes between the two loads.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    store = service.operations
    ours = _signed_report(1)
    store.begin_operation(
        ours, report_uri=uri(ours), correction_of=None, scheduled_for=AT
    )

    theirs = dict(_signed_report(1))
    theirs["report"] = {**theirs["report"], "asset_key": "eip155:1:0x" + "99" * 20}
    real_load = store.load_operation
    loads = {"n": 0}

    def load_ours_then_theirs():
        loads["n"] += 1
        operation = real_load()
        if loads["n"] == 1 or operation is None:
            return operation
        # The second read — the one resolve() performs — returns a foreign operation.
        return dataclasses.replace(
            operation,
            asset_key="eip155:1:0x" + "99" * 20,
            signed_report=theirs,
        )

    store.load_operation = load_ours_then_theirs

    with pytest.raises(UnresolvedPublication, match="configured for"):
        service.resolve_startup()
    assert backend.submissions == [], "the substituted operation was never published"


def test_a_uri_callback_cannot_rewrite_the_report_it_was_given(
    tmp_path: Path,
) -> None:
    """The report is checked, then named. Naming must not be a chance to replace it.

    Handing the callback the very object that was just checked let it clear and refill the
    dict in place, so the check passed on one report and the publication carried another.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    foreign = dict(_signed_report(1))
    foreign["report"] = {**foreign["report"], "asset_key": "eip155:1:0x" + "99" * 20}

    def rewrite_while_naming(value):
        value.clear()
        value.update(foreign)
        return "urn:touchstone:report:1"

    outcome = service.run_slot(
        AT, lambda at: _signed_report(1), report_uri=rewrite_while_naming
    )

    assert outcome.published is True, "our own report published normally"
    operation_assets = [
        entry["signed_report"]["report"]["asset_key"]
        for entry in service.client.transparency_log.verify()
    ]
    assert operation_assets == [ASSET_KEY_OF], (
        f"the published report must be the one that was checked, got {operation_assets}"
    )


@pytest.mark.parametrize(
    ("backoff", "retries"),
    [
        (1e308, 2),
        (1e300, 40),
        (1e10, 10),
        (1.0, 1025),  # the exponent itself overflows, not the product
    ],
)
def test_a_finite_backoff_that_derives_an_impossible_delay_is_refused(
    tmp_path: Path, backoff: float, retries: int
) -> None:
    """Checking the base said nothing about the delays doubled out of it.

    A finite base reaches infinity in a few doublings, so a configuration accepted at
    startup raised on the first failure it existed to absorb — the recovery becoming the
    outage.
    """
    backend = FakeBackend()

    with pytest.raises(ValueError, match="beyond the"):
        build(tmp_path, backend, backoff_seconds=backoff, retries=retries)


def test_an_ordinary_backoff_is_still_accepted(tmp_path: Path) -> None:
    backend = FakeBackend()

    service = build(tmp_path, backend, backoff_seconds=2.0, retries=3)

    assert service.backoff_seconds == 2.0


@pytest.mark.parametrize(
    ("backoff", "retries"),
    [
        (0.0, 2000),  # every wait is zero however many times it doubles
        (3601.0, 0),  # no retries derive no delays at all
        (2.0, 3),
    ],
)
def test_a_backoff_that_derives_no_impossible_delay_is_accepted(
    tmp_path: Path, backoff: float, retries: int
) -> None:
    """The check must cover the domain it accepts and no more.

    Refusing a large base with zero retries, or a zero base with many, rejected
    configurations that derive no delay worth objecting to — and a huge retry count
    overflowed the exponent and surfaced as a raw OverflowError rather than a refusal.
    """
    service = build(tmp_path, FakeBackend(), backoff_seconds=backoff, retries=retries)

    assert service.backoff_seconds == backoff
    assert service.retries == retries


def test_a_report_that_cannot_be_represented_is_recorded_not_raised(
    tmp_path: Path,
) -> None:
    """Freezing is part of the slot, so its failure is a slot outcome.

    A producer returning something unserialisable raised straight out of run_slot, past
    the boundary that is supposed to turn every failure into a recorded incident.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)

    outcome = service.run_slot(
        AT,
        lambda at: {"report": {"asset_key": ASSET_KEY_OF, "x": object()}},
        report_uri=uri,
    )

    assert outcome.published is False
    assert [i.kind for i in service.incidents.open_incidents()] == [EPOCH_FAILED]
    assert backend.submissions == []


def test_a_zero_backoff_survives_the_retry_count_it_was_accepted_with(
    tmp_path: Path,
) -> None:
    """Construction accepts (0.0, 2000); the retry loop has to survive it too.

    The delay was computed as `backoff * 2 ** attempt`. That builds an integer of `attempt`
    bits, and multiplying a float by it converts the integer first — so on attempt 1024 a
    zero backoff, accepted precisely because it can never grow, raised OverflowError from
    inside the recovery it was configured to make free.
    """
    backend = FakeBackend()
    slept: list[float] = []
    service = build(
        tmp_path, backend, sleep=slept.append, backoff_seconds=0.0, retries=2000
    )
    attempts = {"count": 0}
    real_prepare = backend.prepare

    def flaky(asset_key, report, report_uri, correction_of):
        attempts["count"] += 1
        if attempts["count"] <= 1500:
            raise TransportUnavailable("rpc down for a long time")
        return real_prepare(asset_key, report, report_uri, correction_of)

    backend.prepare = flaky

    outcome = service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert outcome.published is True
    assert attempts["count"] == 1501
    assert slept == [0.0] * 1500, "every wait was the zero it was configured to be"


def test_an_incident_is_stamped_with_one_reading_of_the_clock(tmp_path: Path) -> None:
    """`occurred_at` and the projected state are two facts about one event.

    Reading the clock separately for each let an incident be stamped one second before
    midnight and carry the state projected for the *following* day. It shows up only at a
    date boundary — which is precisely when the projection is the thing an operator is
    reading the incident for. The state below is CONFIRMED through the 15th and STALE from
    the 16th, so the two readings disagree by exactly one word.
    """
    backend = FakeBackend()
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = signer.sign_report(
        {
            "asset_key": ASSET_KEY_OF,
            "control_set_root": "22" * 32,
            "correction_of": None,
            "evidence_root": "33" * 32,
            "observed_at": "2026-08-15T09:00:00Z",
            "publisher_kid": signer.kid,
            "sequence": 1,
            "state": "CONFIRMED",
            "state_transition": {
                "as_of": "2026-08-15",
                "evidence_deadline": "2026-08-15",
            },
            "valid_until": "2026-08-15T23:59:59Z",
        }
    )
    before_midnight = datetime(2026, 8, 15, 23, 59, 59, tzinfo=timezone.utc)
    readings = iter(
        [before_midnight]
        + [
            datetime(2026, 8, 16, 0, 0, second, tzinfo=timezone.utc)
            for second in range(1, 9)
        ]
    )
    service = build(tmp_path, backend, now=lambda: next(readings))
    service.operations.save_state(report, updated_at=before_midnight)

    def unavailable(at):
        raise SourceUnavailable("the issuer endpoint did not answer")

    outcome = service.run_slot(AT, unavailable, report_uri=uri)

    assert outcome.published is False
    entry = next(
        e for e in service.incidents.verify() if e["entry_hash"] == outcome.incident_id
    )
    assert entry["occurred_at"].startswith("2026-08-15T23:59:59")
    assert entry["state"] == "CONFIRMED", (
        "the state was projected for the same moment the incident was stamped with"
    )


@pytest.mark.parametrize(
    "path",
    ["record_escaped_failure", "record_outage", "close", "record_clock_error"],
)
def test_every_incident_path_reads_the_clock_once(tmp_path: Path, path: str) -> None:
    """All four recorders, not only the one the first regression happened to exercise.

    Each of them stamps an incident and projects a state, and each could regress
    independently. The state below is CONFIRMED through the 15th and STALE from the 16th,
    so a second reading of the clock across midnight changes the recorded word.
    """
    backend = FakeBackend()
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = signer.sign_report(
        {
            "asset_key": ASSET_KEY_OF,
            "control_set_root": "22" * 32,
            "correction_of": None,
            "evidence_root": "33" * 32,
            "observed_at": "2026-08-15T09:00:00Z",
            "publisher_kid": signer.kid,
            "sequence": 1,
            "state": "CONFIRMED",
            "state_transition": {
                "as_of": "2026-08-15",
                "evidence_deadline": "2026-08-15",
            },
            "valid_until": "2026-08-15T23:59:59Z",
        }
    )
    before_midnight = datetime(2026, 8, 15, 23, 59, 59, tzinfo=timezone.utc)
    readings = iter(
        [before_midnight]
        + [
            datetime(2026, 8, 16, 0, 0, second, tzinfo=timezone.utc)
            for second in range(1, 12)
        ]
    )
    service = build(tmp_path, backend, now=lambda: next(readings))
    service.operations.save_state(report, updated_at=before_midnight)

    if path == "close":
        # Closing reads the clock too, so it gets the same single-reading treatment. It
        # needs something open to close, opened before the clock is under test.
        opener = build(tmp_path, backend, now=lambda: before_midnight)
        opener.record_outage(AT, 3)
        service._close_open_incidents("recovered")
        entries = [e for e in service.incidents.verify() if e["closes"] is not None]
    else:
        getattr(service, path)(
            AT, RuntimeError("boom") if path != "record_outage" else 3
        )
        entries = [e for e in service.incidents.verify() if e["closes"] is None]

    assert entries, f"{path} wrote an incident"
    entry = entries[-1]
    assert entry["occurred_at"].startswith("2026-08-15T23:59:59")
    assert entry["state"] == "CONFIRMED", (
        "the state was projected for the same moment the entry was stamped with"
    )


def test_a_schedule_that_cannot_continue_is_not_recorded_as_a_failed_epoch(
    tmp_path: Path,
) -> None:
    """The slot succeeded; naming the *next* one is what failed.

    Routing this through `record_escaped_failure` wrote "the slot at X failed
    unexpectedly" about a slot that had just published, sending an operator to look for a
    broken epoch that does not exist.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)

    service.record_clock_error(AT, OverflowError("a slot 1e20 seconds after X"))

    open_incidents = service.incidents.open_incidents()
    assert [i.kind for i in open_incidents] == [SCHEDULE_UNUSABLE]
    assert "the schedule stopped after" in open_incidents[0].detail
    assert "failed unexpectedly" not in open_incidents[0].detail


def test_serve_routes_a_clock_error_to_the_schedule_incident(tmp_path: Path) -> None:
    """The adapter, not only the recorder: `serve` has to hand the callback across."""
    backend = FakeBackend()
    service = build(tmp_path, backend)
    clock = Clock()

    def die_on_the_second_slot(at):
        clock.t += 1e12  # an outage no clock can name the far side of
        return _signed_report(1)

    outcome = serve(
        service,
        die_on_the_second_slot,
        report_uri=uri,
        interval_seconds=2e10,
        max_runs=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: AT,
    )

    assert outcome.clock_error is not None
    assert [i.kind for i in service.incidents.open_incidents()] == [SCHEDULE_UNUSABLE]


def test_recovery_will_not_republish_a_report_whose_bundle_is_missing(
    tmp_path: Path,
) -> None:
    """`resolve()` republishes stored signed bytes, so the check must live inside it.

    A fresh slot builds and verifies a bundle before its report is ever returned, so
    publication and verifiability go together there. Recovery had no such step: a pending
    operation written before bundles existed, or one whose file was deleted, would go back on
    chain with nothing a reader could check it against — permanently, correctable only by
    issuing another report.

    The hook is checked here rather than at the call sites because `resolve()` re-reads the
    operation file. A guard applied to a previously loaded operation guards a different
    object, which is the same reason the asset key is checked inside this method.
    """
    backend = FakeBackend()
    service = build(tmp_path, backend)
    service.operations.begin_operation(
        _signed_report(1),
        report_uri="urn:touchstone:report:1",
        correction_of=None,
        scheduled_for=AT,
    )
    seen: list[object] = []

    def refuse(operation):
        seen.append(operation)
        raise RuntimeError("no verifying bundle for this report")

    with pytest.raises(RuntimeError, match="no verifying bundle"):
        service.operations.resolve(service.client, before_publish=refuse)

    assert len(seen) == 1, "the guard was not consulted"
    assert seen[0].signed_report == _signed_report(1), (
        "the guard must see the operation this call loaded, not a stale one"
    )
    assert not backend.submissions, "an unverifiable report reached the chain"
    assert service.operations.load_operation() is not None, "kept for review"
