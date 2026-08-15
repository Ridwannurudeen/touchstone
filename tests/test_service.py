"""The service's ordering rules, which are the only thing standing between a fault and a
double publication."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from touchstone.incidents import (
    PUBLICATION_UNRESOLVED,
    SOURCE_UNAVAILABLE,
    IncidentLog,
)
from touchstone.operations import OperationsStore
from touchstone.publish import PublisherClient, TransportUnavailable
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

    with pytest.raises(Exception, match="reconciled, not retried"):
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

    with pytest.raises(RuntimeError, match="crash after"):
        service.run_slot(AT, lambda at: _signed_report(1), report_uri=uri)

    assert cleared["called"], "the crash happened at the clear, not before it"
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
