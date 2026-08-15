"""Durable operational state: what survives a crash, and what it means afterwards."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from touchstone.controls import AssetState
from touchstone.operations import (
    OperationsError,
    OperationsStore,
    UnresolvedPublication,
)
from touchstone.publish import PublisherClient
from touchstone.signing import canonical_json_bytes
from touchstone.translog import TransparencyLog

from test_publish import FakeBackend, _manifest, _signed_report  # noqa: F401


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)


def store(tmp_path: Path) -> OperationsStore:
    return OperationsStore(tmp_path / "operations")


def client_for(tmp_path: Path, backend: FakeBackend) -> PublisherClient:
    return PublisherClient(
        backend,
        TransparencyLog(tmp_path / "transparency.jsonl"),
        tmp_path / "pending.json",
    )


def begin(operations: OperationsStore, sequence: int = 1):
    return operations.begin_operation(
        _signed_report(sequence),
        report_uri=f"urn:touchstone:report:{sequence}",
        correction_of=None,
        scheduled_for=AT,
    )


def test_an_operation_records_the_whole_publication(tmp_path: Path) -> None:
    """Everything needed to finish it, so a restart never has to reconstruct anything."""
    operations = store(tmp_path)

    operation = begin(operations)

    assert operation.sequence == 1
    assert operation.report_uri == "urn:touchstone:report:1"
    assert operation.correction_of is None
    assert operation.scheduled_for == "2026-08-15T09:00:00Z"

    reloaded = operations.load_operation()
    assert reloaded == operation
    assert reloaded.signed_report == _signed_report(1)


def test_a_second_operation_cannot_begin_while_one_is_unresolved(
    tmp_path: Path,
) -> None:
    """The invariant the whole service rests on: one publication in flight at a time."""
    operations = store(tmp_path)
    begin(operations)

    with pytest.raises(UnresolvedPublication, match="still unresolved"):
        begin(operations, sequence=2)


def test_resolving_publishes_and_clears(tmp_path: Path) -> None:
    operations = store(tmp_path)
    backend = FakeBackend()
    begin(operations)

    result = operations.resolve(client_for(tmp_path, backend))

    assert result is not None
    assert len(backend.submissions) == 1
    assert operations.load_operation() is None


def test_a_crash_after_the_publisher_finished_still_clears(tmp_path: Path) -> None:
    """The exact gap between the publisher's journal and this store.

    The publication settled and the publisher cleared its own journal, then the process
    died before this operation was removed. On restart the registry reports the sequence
    as already published — which is only *ours* because the transparency log holds this
    exact report under it.
    """
    operations = store(tmp_path)
    backend = FakeBackend()
    client = client_for(tmp_path, backend)
    begin(operations)
    operations.resolve(client)
    assert operations.load_operation() is None

    # Recreate the operation, simulating a crash before it was cleared.
    begin(operations)

    result = operations.resolve(client)

    assert result is None, "nothing new was published"
    assert len(backend.submissions) == 1, "and nothing was published twice"
    assert operations.load_operation() is None, "the stale operation was cleared"


def test_a_sequence_published_by_someone_else_is_not_treated_as_ours(
    tmp_path: Path,
) -> None:
    """A duplicate sequence only means we finished if our own log says so."""
    operations = store(tmp_path)
    backend = FakeBackend()
    client = client_for(tmp_path, backend)
    # The chain already holds sequence 1, but this service never recorded publishing it.
    client.publish(_signed_report(1), report_uri="urn:touchstone:report:1")
    client.transparency_log.path.unlink()
    begin(operations)

    with pytest.raises(UnresolvedPublication, match="no record of publishing it"):
        operations.resolve(client)
    assert operations.load_operation() is not None, "the operation is kept for review"


def test_state_is_projected_stale_after_its_deadline_without_new_evidence(
    tmp_path: Path,
) -> None:
    """Criterion 3. Absence of evidence ages the projection; it never invents one."""
    operations = store(tmp_path)

    state = operations.save_state(_signed_report(1), updated_at=AT)

    assert state.observed_state == AssetState.CONFIRMED.value
    assert state.evidence_deadline == date(2026, 8, 13)
    assert state.projected(date(2026, 8, 13)) is AssetState.CONFIRMED
    assert state.projected(date(2026, 8, 14)) is AssetState.STALE, (
        "past its deadline it goes stale on its own, with no new report"
    )


def test_state_survives_a_reload(tmp_path: Path) -> None:
    operations = store(tmp_path)
    saved = operations.save_state(_signed_report(1), updated_at=AT)

    assert operations.load_state(saved.asset_key) == saved
    assert operations.load_state("eip155:1:0x" + "99" * 20) is None


def test_a_projection_needs_a_plain_date(tmp_path: Path) -> None:
    state = store(tmp_path).save_state(_signed_report(1), updated_at=AT)

    with pytest.raises(OperationsError, match="plain date"):
        state.projected(datetime(2026, 8, 14, tzinfo=timezone.utc))


def test_a_half_written_record_never_replaces_a_whole_one(tmp_path: Path) -> None:
    """Criterion 6. A crash mid-write leaves the old record, never a fragment.

    The write goes to a temporary file and is moved into place in one step, so a reader
    sees either the previous record or the new one — never half of either.
    """
    operations = store(tmp_path)
    first = operations.save_state(_signed_report(1), updated_at=AT)
    path = operations.state_path(first.asset_key)
    original = path.read_bytes()

    # A temporary file left behind by an interrupted write must not be read as state.
    path.with_name(path.name + ".tmp").write_bytes(b'{"asset_key": "half')

    assert path.read_bytes() == original
    assert operations.load_state(first.asset_key) == first


def test_a_corrupt_operation_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    operations = store(tmp_path)
    begin(operations)
    operations.operation_path.write_bytes(b"{not json\n")

    with pytest.raises(OperationsError, match="cannot read"):
        operations.load_operation()


def test_an_operation_that_contradicts_its_report_is_refused(tmp_path: Path) -> None:
    operations = store(tmp_path)
    begin(operations)
    from touchstone.signing import strict_json_loads

    record = strict_json_loads(operations.operation_path.read_bytes())
    record["sequence"] = 99
    operations.operation_path.write_bytes(canonical_json_bytes(record) + b"\n")

    with pytest.raises(OperationsError, match="does not describe the report"):
        operations.load_operation()


def test_an_instant_must_be_timezone_aware(tmp_path: Path) -> None:
    operations = store(tmp_path)

    with pytest.raises(OperationsError, match="timezone-aware"):
        operations.begin_operation(
            _signed_report(1),
            report_uri="urn:touchstone:report:1",
            correction_of=None,
            scheduled_for=datetime(2026, 8, 15, 9, 0),
        )
