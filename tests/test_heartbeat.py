"""A heartbeat's only job is to stop being true when the daemon stops running."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from touchstone.heartbeat import (
    DEFAULT_EXPIRY_SECONDS,
    HEARTBEAT_VERSION,
    HeartbeatError,
    build_record,
    process_identity,
    read,
    verify,
    write,
)
from touchstone.signing import canonical_json_bytes
from touchstone.workspace import Workspace


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
REGISTRY = "0x" + "ab" * 20


def record(**changes: object) -> dict[str, object]:
    value = build_record(
        asset_key=ASSET,
        registry_address=REGISTRY,
        sequence=1,
        now=AT,
    )
    value.update(changes)
    return value


def beat(tmp_path: Path, **changes: object) -> Path:
    path = Workspace(tmp_path).heartbeat
    write(path, record(**changes))
    return path


def checked(path: Path, *, now: datetime, **changes: object):
    arguments = {"asset_key": ASSET, "registry_address": REGISTRY}
    arguments.update(changes)
    return verify(path, now=now, **arguments)


def test_a_fresh_heartbeat_is_alive_and_healthy(tmp_path: Path) -> None:
    health = checked(beat(tmp_path), now=AT + timedelta(seconds=30))

    assert health.ok
    assert health.daemon_alive
    assert health.epoch_healthy
    assert health.reasons == ()
    assert health.record is not None
    assert health.record["version"] == HEARTBEAT_VERSION


def test_a_heartbeat_expires_rather_than_staying_green(tmp_path: Path) -> None:
    """The property the whole module exists for.

    Nothing rewrites this file when the daemon dies — there is nothing left to rewrite it —
    so the record has to become false on its own, by the reader's clock rather than by any
    stored flag.
    """
    path = beat(tmp_path)

    assert checked(path, now=AT + timedelta(seconds=DEFAULT_EXPIRY_SECONDS - 1)).ok
    expired = checked(path, now=AT + timedelta(seconds=DEFAULT_EXPIRY_SECONDS + 1))

    assert not expired.daemon_alive
    assert "expired" in " ".join(expired.reasons)


def test_no_heartbeat_at_all_is_not_alive(tmp_path: Path) -> None:
    health = checked(Workspace(tmp_path).heartbeat, now=AT)

    assert not health.ok
    assert health.record is None
    assert "no heartbeat" in " ".join(health.reasons)


def test_a_heartbeat_written_in_the_future_is_refused(tmp_path: Path) -> None:
    """A clock that jumped forward once would otherwise keep this green indefinitely."""
    path = beat(tmp_path)

    health = checked(path, now=AT - timedelta(minutes=5))

    assert not health.daemon_alive
    assert "future" in " ".join(health.reasons)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("asset_key", "eip155:1:0x" + "99" * 20, "different asset"),
        ("registry_address", "0x" + "cd" * 20, "different deployment"),
    ],
)
def test_a_heartbeat_for_another_identity_is_not_this_ones(
    tmp_path: Path, field: str, value: str, expected: str
) -> None:
    """Two services sharing a directory is a misconfiguration, not a liveness signal."""
    path = beat(tmp_path, **{field: value})

    health = checked(path, now=AT + timedelta(seconds=30))

    assert not health.daemon_alive
    assert expected in " ".join(health.reasons)


def test_a_sequence_that_does_not_advance_is_refused(tmp_path: Path) -> None:
    """A reader that has already seen sequence 5 must not accept 5 again.

    A restored backup and a second daemon writing to one workspace both look exactly like
    this, and both are states where the file says more than it knows.
    """
    path = beat(tmp_path, sequence=5)

    assert checked(path, now=AT + timedelta(seconds=30), previous_sequence=4).ok
    stalled = checked(path, now=AT + timedelta(seconds=30), previous_sequence=5)

    assert not stalled.daemon_alive
    assert "did not advance" in " ".join(stalled.reasons)


def test_liveness_and_epoch_health_are_answered_separately(tmp_path: Path) -> None:
    """A daemon running perfectly with a dead source is alive and unhealthy.

    Collapsing the two hides exactly the failure that matters: the process everyone can see
    is fine, and the thing it exists to do has not happened for two days.
    """
    path = beat(tmp_path, last_successful_epoch=None, last_attempted_slot=None)

    health = checked(path, now=AT + timedelta(seconds=30), slot_overdue=True)

    assert health.daemon_alive, "the process is writing heartbeats"
    assert not health.epoch_healthy, "and has produced no epoch for an overdue slot"
    assert not health.ok
    assert "overdue" in " ".join(health.reasons)


def test_an_overdue_slot_with_a_recorded_attempt_is_still_healthy(
    tmp_path: Path,
) -> None:
    """An attempt that opened an incident is the system working, not failing silently."""
    path = beat(tmp_path, last_attempted_slot="2026-08-15T09:00:00Z")

    assert checked(path, now=AT + timedelta(seconds=30), slot_overdue=True).ok


def test_a_truncated_heartbeat_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    path = beat(tmp_path)
    path.write_bytes(b'{"version": "touchstone.hea')

    health = checked(path, now=AT)

    assert not health.daemon_alive
    assert "strict JSON" in " ".join(health.reasons)


@pytest.mark.parametrize("field", sorted({"version", "written_at", "sequence"}))
def test_a_heartbeat_missing_a_field_is_not_a_heartbeat(
    tmp_path: Path, field: str
) -> None:
    path = Workspace(tmp_path).heartbeat
    incomplete = record()
    del incomplete[field]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(incomplete) + b"\n")

    with pytest.raises(HeartbeatError, match="exactly the documented set"):
        read(path)


def test_an_unsupported_version_is_refused(tmp_path: Path) -> None:
    path = Workspace(tmp_path).heartbeat
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        canonical_json_bytes(record(version="touchstone.heartbeat.v9")) + b"\n"
    )

    with pytest.raises(HeartbeatError, match="version is not supported"):
        read(path)


def test_a_record_is_built_from_one_reading_of_the_clock() -> None:
    """The expiry belongs to the write it describes, not to a second look at the clock."""
    built = build_record(
        asset_key=ASSET,
        registry_address=REGISTRY,
        sequence=1,
        now=AT,
        expiry_seconds=180.0,
    )

    assert built["written_at"] == "2026-08-15T09:00:00Z"
    assert built["expires_at"] == "2026-08-15T09:03:00Z"


@pytest.mark.parametrize("expiry", [0, -1, float("nan"), float("inf"), True, "180"])
def test_an_expiry_that_is_not_a_duration_is_refused(expiry: object) -> None:
    """A window that never closes is not a long window; it is the absence of one."""
    with pytest.raises(ValueError, match="expiry_seconds"):
        build_record(
            asset_key=ASSET,
            registry_address=REGISTRY,
            sequence=1,
            now=AT,
            expiry_seconds=expiry,
        )


@pytest.mark.parametrize("sequence", [0, -1, True, "1", None])
def test_a_sequence_that_is_not_a_count_is_refused(sequence: object) -> None:
    with pytest.raises(HeartbeatError, match="sequence"):
        build_record(
            asset_key=ASSET,
            registry_address=REGISTRY,
            sequence=sequence,
            now=AT,
        )


def test_an_instant_that_is_not_aware_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_record(
            asset_key=ASSET,
            registry_address=REGISTRY,
            sequence=1,
            now=datetime(2026, 8, 15, 9, 0),
        )


def test_a_process_identity_distinguishes_a_recycled_pid() -> None:
    """PIDs are recycled, and a heartbeat naming only one can be confirmed by a stranger."""
    mine = process_identity()

    assert mine.startswith(f"{__import__('os').getpid()}:")
    assert process_identity() == mine, "the same process answers the same way"


def test_a_half_written_heartbeat_never_replaces_a_whole_one(tmp_path: Path) -> None:
    """A stale heartbeat expires and is correctly called dead; a truncated one is a guess."""
    path = beat(tmp_path)
    original = path.read_bytes()
    path.with_name(path.name + ".tmp").write_bytes(b'{"version": "half')

    assert path.read_bytes() == original
    assert checked(path, now=AT + timedelta(seconds=30)).ok


def test_a_heartbeat_that_cannot_be_written_is_this_modules_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "open", refuse)

    with pytest.raises(HeartbeatError, match="cannot be written"):
        write(Workspace(tmp_path).heartbeat, record())


def test_the_workspace_derives_the_heartbeat_and_evidence_paths(tmp_path: Path) -> None:
    """Evidence is the one thing here that cannot be recreated, so it is inside the root."""
    workspace = Workspace(tmp_path / "asset")

    assert workspace.heartbeat == tmp_path / "asset" / "heartbeat.json"
    assert workspace.evidence == tmp_path / "asset" / "evidence"
