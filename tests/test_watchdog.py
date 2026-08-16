"""The watchdog's value is entirely in what it refuses to call healthy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

from touchstone.heartbeat import build_record, write
from touchstone.incidents import SOURCE_UNAVAILABLE, IncidentLog
from touchstone.signing import canonical_json_bytes
from touchstone.translog import TransparencyLog
from touchstone.watchdog import inspect, render, restart_command
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
from test_publish import _signed_report  # noqa: E402


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
REGISTRY = "0x" + "ab" * 20


def alive(workspace: Workspace, **changes: object) -> None:
    record = build_record(
        asset_key=ASSET, registry_address=REGISTRY, sequence=1, now=AT
    )
    record.update(changes)
    write(workspace.heartbeat, record)


def looked_at(root: Path, *, now: datetime = AT + timedelta(seconds=30), **changes):
    arguments = {"asset_key": ASSET, "registry_address": REGISTRY}
    arguments.update(changes)
    return inspect(root, now=now, **arguments)


def named(report, name: str):
    return next(check for check in report.checks if check.name == name)


def test_a_running_service_is_healthy(tmp_path: Path) -> None:
    alive(Workspace(tmp_path))

    report = looked_at(tmp_path)

    assert report.healthy
    assert report.exit_code == 0
    assert report.failures == ()


def test_a_dead_daemon_is_detected_by_its_expired_heartbeat(tmp_path: Path) -> None:
    """Nothing rewrote the file, so the only thing that changed is the clock."""
    alive(Workspace(tmp_path))

    report = looked_at(tmp_path, now=AT + timedelta(minutes=4))

    assert not report.healthy
    assert report.exit_code == 1
    assert not named(report, "heartbeat").healthy
    assert "expired" in named(report, "heartbeat").detail


def test_a_workspace_that_was_never_started_is_not_healthy(tmp_path: Path) -> None:
    report = looked_at(tmp_path)

    assert not report.healthy
    assert "no heartbeat" in named(report, "heartbeat").detail


def test_a_damaged_transparency_log_is_caught(tmp_path: Path) -> None:
    """The durable record is checked, not just the process writing it."""
    workspace = Workspace(tmp_path)
    alive(workspace)
    log = TransparencyLog(workspace.transparency_log)
    log.append(
        _signed_report(1),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    raw = workspace.transparency_log.read_bytes()
    workspace.transparency_log.write_bytes(
        raw.replace(b'"sequence":1', b'"sequence":9')
    )

    report = looked_at(tmp_path)

    assert not report.healthy
    assert not named(report, "transparency-log").healthy


def test_a_damaged_incident_log_is_caught(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    alive(workspace)
    incidents = IncidentLog(workspace.incidents)
    incidents.open_incident(
        asset_key=ASSET,
        kind=SOURCE_UNAVAILABLE,
        detail="the feed returned 403",
        occurred_at=AT,
    )
    workspace.incidents.write_bytes(b"{not json\n")

    report = looked_at(tmp_path)

    assert not report.healthy
    assert not named(report, "incident-log").healthy


def test_a_journalled_transaction_with_no_operation_is_the_dangerous_state(
    tmp_path: Path,
) -> None:
    """One without the other means a restart cannot tell what the chain already holds.

    That is the position from which a service publishes the same day twice, so it is
    reported as unhealthy rather than left for the next startup to discover.
    """
    workspace = Workspace(tmp_path)
    alive(workspace)
    workspace.pending_journal.parent.mkdir(parents=True, exist_ok=True)
    workspace.pending_journal.write_bytes(
        canonical_json_bytes({"transaction_hash": "0x" + "11" * 32}) + b"\n"
    )

    report = looked_at(tmp_path)

    assert not report.healthy
    assert "no operation" in named(report, "publication").detail


def test_a_healthy_daemon_with_no_epoch_is_alive_and_unhealthy(tmp_path: Path) -> None:
    """The two questions stay separate all the way out to the exit code."""
    alive(Workspace(tmp_path), last_successful_epoch=None, last_attempted_slot=None)

    report = looked_at(tmp_path, due_slot=AT)

    assert named(report, "heartbeat").healthy, "the process is alive"
    assert not named(report, "epoch").healthy, "and has produced nothing"
    assert report.exit_code == 1


def test_a_stalled_sequence_is_refused(tmp_path: Path) -> None:
    alive(Workspace(tmp_path), sequence=7)

    assert looked_at(tmp_path, previous_sequence=6).healthy
    assert not looked_at(tmp_path, previous_sequence=7).healthy


def test_the_watchdog_writes_nothing_into_the_workspace(tmp_path: Path) -> None:
    """It observes a workspace another process owns; a second writer is what it prevents.

    The daemon holds the workspace lock for its whole serving lifetime, so anything this
    wrote would either block or race. The assertion is on the directory itself.
    """
    workspace = Workspace(tmp_path)
    alive(workspace)
    def snapshot():
        # Directories and file *bytes*, not just names and sizes. The first version of
        # this test compared paths, sizes and mtimes of files only — and passed while the
        # watchdog was creating an `operations/` directory on every inspection.
        return sorted(
            (
                path.relative_to(tmp_path).as_posix(),
                path.is_dir(),
                None if path.is_dir() else path.read_bytes(),
            )
            for path in tmp_path.rglob("*")
        )

    before = snapshot()

    looked_at(tmp_path)

    assert snapshot() == before, "the watchdog modified the workspace it was watching"


def test_inspecting_a_workspace_that_was_never_served_creates_nothing(
    tmp_path: Path,
) -> None:
    """The case the byte-comparison above would still have missed.

    An empty directory has nothing to compare, so the assertion has to be that it is
    still empty afterwards.
    """
    workspace = tmp_path / "untouched"
    workspace.mkdir()

    looked_at(workspace)

    assert list(workspace.iterdir()) == [], "inspection created part of the workspace"


def test_the_report_names_every_question_it_asked(tmp_path: Path) -> None:
    alive(Workspace(tmp_path))

    rendered = render(looked_at(tmp_path))

    for name in (
        "heartbeat",
        "epoch",
        "transparency-log",
        "incident-log",
        "publication",
    ):
        assert name in rendered
    assert rendered.endswith("HEALTHY")


@pytest.mark.parametrize(
    "argv", [(), ("",), ("systemctl", ""), ("systemctl", None), "systemctl restart"]
)
def test_a_restart_command_must_be_an_argument_vector(argv: object) -> None:
    """Never a shell string.

    This runs unattended with the ability to start processes, and a shell string is an
    injection surface a fixed argv does not have — there is nothing for a crafted
    workspace path or asset key to escape into.
    """
    with pytest.raises(ValueError, match="vector of strings"):
        restart_command(argv)


def test_a_valid_restart_command_is_returned_unchanged() -> None:
    assert restart_command(["systemctl", "--user", "restart", "touchstone"]) == (
        "systemctl",
        "--user",
        "restart",
        "touchstone",
    )


def test_an_alert_fires_on_the_edge_not_on_every_check(tmp_path: Path) -> None:
    """A watchdog that alerts every 60 seconds is a watchdog whose alerts get muted."""
    from touchstone.watchdog import transition

    alive(Workspace(tmp_path))
    healthy = looked_at(tmp_path)
    sick = looked_at(tmp_path, now=AT + timedelta(minutes=4))

    first_bad = transition(sick, None)
    assert first_bad.changed
    assert first_bad.event == "HEARTBEAT_STALE"
    assert first_bad.severity == "CRITICAL"

    again = transition(sick, first_bad.fingerprint)
    assert not again.changed, "the same condition alerted twice"

    recovered = transition(healthy, first_bad.fingerprint)
    assert recovered.changed
    assert recovered.event == "RECOVERED"
    assert recovered.severity == "INFO"

    assert not transition(healthy, recovered.fingerprint).changed


def test_a_first_healthy_observation_announces_nothing(tmp_path: Path) -> None:
    """There is nothing to recover from, and nobody was told of a condition."""
    from touchstone.watchdog import transition

    alive(Workspace(tmp_path))

    assert not transition(looked_at(tmp_path), None).changed


def test_different_failures_are_different_conditions(tmp_path: Path) -> None:
    """A stale heartbeat and a broken log must not collapse into one 'unhealthy'."""
    from touchstone.watchdog import transition

    workspace = Workspace(tmp_path)
    alive(workspace)
    stale = transition(looked_at(tmp_path, now=AT + timedelta(minutes=4)), None)

    workspace.pending_journal.parent.mkdir(parents=True, exist_ok=True)
    workspace.pending_journal.write_bytes(
        canonical_json_bytes({"transaction_hash": "0x" + "11" * 32}) + b"\n"
    )
    unresolved = transition(looked_at(tmp_path), None)

    assert stale.event == "HEARTBEAT_STALE"
    assert unresolved.event == "PUBLICATION_UNRESOLVED"
    assert stale.fingerprint != unresolved.fingerprint
