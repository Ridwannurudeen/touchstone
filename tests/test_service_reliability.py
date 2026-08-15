"""The reliability layer is only worth anything if the daemon actually runs it.

Every piece of PLAN-T8 was built and tested in isolation first, and every one of those
tests passed while nothing in the serving path called any of it. A module that is correct
and unreachable is indistinguishable, from an operator's seat, from a module that does not
exist — so these tests exercise `serve()` and assert the artifacts appear on disk.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import secrets
import sys

import pytest

from touchstone.backup import open_archive
from touchstone.heartbeat import read as read_heartbeat, verify as verify_heartbeat
from touchstone.incidents import IncidentLog
from touchstone.operations import OperationsStore
from touchstone.publish import PublisherClient
from touchstone.translog import TransparencyLog
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from run_service import Service, serve  # noqa: E402
from test_publish import FakeBackend, _signed_report  # noqa: E402
from test_service import AT, ASSET_KEY_OF, Clock, uri  # noqa: E402


REGISTRY = "0x" + "ab" * 20
KEY = secrets.token_bytes(32)


def served(tmp_path: Path, **overrides) -> tuple[Service, Workspace, FakeBackend]:
    workspace = Workspace(tmp_path / "asset")
    workspace.root.mkdir(parents=True, exist_ok=True)
    backend = FakeBackend()
    client = PublisherClient(
        backend,
        TransparencyLog(workspace.transparency_log),
        workspace.pending_journal,
    )
    arguments: dict[str, object] = {
        "asset_key": ASSET_KEY_OF,
        "sleep": lambda seconds: None,
        "now": lambda: AT,
        "lock_path": workspace.lock,
        "heartbeat_path": workspace.heartbeat,
        "registry_address": REGISTRY,
        "backup_dir": tmp_path / "archives",
        "backup_key": KEY,
    }
    arguments.update(overrides)
    service = Service(
        client,
        OperationsStore(workspace.operations, now=lambda: AT),
        IncidentLog(workspace.incidents),
        **arguments,
    )
    return service, workspace, backend


def run(service: Service, workspace: Workspace, *, runs: int = 1, produce=None):
    clock = Clock()
    published: list[datetime] = []

    def default_produce(scheduled_at: datetime):
        published.append(scheduled_at)
        return _signed_report(len(published))

    return serve(
        service,
        produce or default_produce,
        report_uri=uri,
        interval_seconds=60,
        max_runs=runs,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: AT,
    )


def test_serving_writes_a_heartbeat_a_watchdog_can_read(tmp_path: Path) -> None:
    """The wiring finding in one assertion: does the file exist after serving."""
    service, workspace, _ = served(tmp_path)

    run(service, workspace)

    assert workspace.heartbeat.exists(), (
        "the daemon served a slot and wrote no heartbeat"
    )
    record = read_heartbeat(workspace.heartbeat)
    assert record["asset_key"] == ASSET_KEY_OF
    assert record["registry_address"] == REGISTRY
    health = verify_heartbeat(
        workspace.heartbeat,
        now=AT,
        asset_key=ASSET_KEY_OF,
        registry_address=REGISTRY,
    )
    assert health.daemon_alive


def test_the_first_heartbeat_comes_after_reconciliation(tmp_path: Path) -> None:
    """A heartbeat during startup would report health before the state was settled."""
    service, workspace, _ = served(tmp_path)
    order: list[str] = []
    original = service.resolve_startup

    def watched():
        order.append("reconciled")
        return original()

    service.resolve_startup = watched
    original_beat = service.beat

    def beat():
        order.append("beat")
        return original_beat()

    service.beat = beat

    run(service, workspace)

    assert order[0] == "reconciled", "the heartbeat preceded reconciliation"
    assert "beat" in order


def test_the_heartbeat_records_the_slot_that_was_attempted(tmp_path: Path) -> None:
    service, workspace, _ = served(tmp_path)

    run(service, workspace)

    record = read_heartbeat(workspace.heartbeat)
    assert record["last_attempted_slot"] is not None
    assert record["last_successful_epoch"] is not None


def test_a_failed_slot_still_records_that_it_was_attempted(tmp_path: Path) -> None:
    """Recording only successes leaves a failing service looking identical to an idle one.

    That is the exact silence the epoch-health check exists to break, so the attempt is
    noted in a `finally` rather than on the success path.
    """
    service, workspace, _ = served(tmp_path)

    def refuse(scheduled_at: datetime):
        raise RuntimeError("the source would not answer")

    run(service, workspace, produce=refuse)

    record = read_heartbeat(workspace.heartbeat)
    assert record["last_attempted_slot"] is not None, "a failed slot recorded nothing"
    assert record["last_successful_epoch"] is None


def test_serving_takes_the_daily_backup_from_inside_its_own_lock(
    tmp_path: Path,
) -> None:
    """The cooperative path. A second process could not do this while the daemon runs."""
    service, workspace, _ = served(tmp_path)

    run(service, workspace)

    archives = sorted((tmp_path / "archives").glob("*.archive"))
    assert archives, "the daemon served a slot and took no backup"
    value = open_archive(
        archives[0].read_bytes(),
        key=KEY,
        asset_key=ASSET_KEY_OF,
        registry_address=REGISTRY,
    )
    paths = {item["path"] for item in value["files"]}
    assert "transparency.jsonl" in paths, "the archive is missing the durable record"


def test_the_backup_is_taken_once_a_day_not_once_a_slot(tmp_path: Path) -> None:
    service, workspace, _ = served(tmp_path)

    run(service, workspace, runs=3)

    assert len(sorted((tmp_path / "archives").glob("*.archive"))) == 1


def test_the_heartbeat_reports_when_the_last_backup_happened(tmp_path: Path) -> None:
    service, workspace, _ = served(tmp_path)

    run(service, workspace)

    assert read_heartbeat(workspace.heartbeat)["last_backup_at"] is not None


def test_a_backup_that_cannot_be_written_opens_an_incident_and_does_not_stop_the_slot(
    tmp_path: Path,
) -> None:
    """A failed backup is a reliability problem to be seen, not a reason to stop serving."""
    service, workspace, _ = served(tmp_path)

    def refuse(*args, **kwargs):
        raise PermissionError(13, "denied")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("run_service.backup.create", refuse)
        outcome = run(service, workspace)

    assert outcome.completed == 1, "the slot succeeded despite the backup failing"
    entries = IncidentLog(workspace.incidents).verify()
    assert any("backup" in entry["detail"] for entry in entries), (
        "a failed backup left no trace"
    )


def test_serving_without_a_backup_destination_still_serves(tmp_path: Path) -> None:
    """Absence of a backup destination must not be an outage; it is visible in the beat."""
    service, workspace, _ = served(tmp_path, backup_dir=None, backup_key=None)

    outcome = run(service, workspace)

    assert outcome.completed == 1
    assert read_heartbeat(workspace.heartbeat)["last_backup_at"] is None


def test_a_heartbeat_that_cannot_be_written_does_not_stop_the_service(
    tmp_path: Path,
) -> None:
    """A monitoring failure must not become an outage.

    The watchdog already treats an absent heartbeat as unhealthy, so the condition is
    reported by exactly the mechanism that exists for it.
    """
    service, workspace, _ = served(tmp_path, backup_dir=None, backup_key=None)

    def refuse(*args, **kwargs):
        raise PermissionError(13, "denied")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("run_service.heartbeat.write", refuse)
        outcome = run(service, workspace)

    assert outcome.completed == 1, "a heartbeat failure stopped the service"
    assert not workspace.heartbeat.exists(), "and the heartbeat is simply absent"


def test_the_heartbeat_sequence_advances_every_slot(tmp_path: Path) -> None:
    """A watchdog refuses a sequence that has not advanced, so it has to."""
    service, workspace, _ = served(tmp_path)

    run(service, workspace)
    first = read_heartbeat(workspace.heartbeat)["sequence"]
    run(service, workspace)
    second = read_heartbeat(workspace.heartbeat)["sequence"]

    assert second > first
