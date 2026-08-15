"""An incident log is only worth having if it cannot be quietly tidied afterwards."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from touchstone.incidents import (
    EPOCH_FAILED,
    SOURCE_UNAVAILABLE,
    IncidentLog,
    IncidentLogError,
)
from touchstone.signing import canonical_json_bytes, strict_json_loads


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"


def log(tmp_path: Path) -> IncidentLog:
    return IncidentLog(tmp_path / "incidents.jsonl")


def opened(incidents: IncidentLog, **overrides) -> dict[str, object]:
    arguments = {
        "asset_key": ASSET,
        "kind": SOURCE_UNAVAILABLE,
        "detail": "the feed returned 403",
        "occurred_at": AT,
    }
    arguments.update(overrides)
    return incidents.open_incident(**arguments)


def test_an_empty_log_verifies_and_has_nothing_open(tmp_path: Path) -> None:
    incidents = log(tmp_path)

    assert incidents.verify() == []
    assert incidents.open_incidents() == []


def test_opening_and_closing_appends_rather_than_edits(tmp_path: Path) -> None:
    incidents = log(tmp_path)

    entry = opened(incidents)
    assert [i.incident_id for i in incidents.open_incidents()] == [entry["entry_hash"]]

    closure = incidents.close_incident(
        entry["entry_hash"],
        detail="the feed answered",
        occurred_at=AT + timedelta(hours=1),
    )

    assert incidents.open_incidents() == []
    entries = incidents.verify()
    assert len(entries) == 2, "the opening entry is still there, unchanged"
    assert entries[0] == entry
    assert entries[1]["closes"] == entry["entry_hash"]
    assert closure["occurred_at"] == "2026-08-15T10:00:00Z"


def test_incidents_are_separated_by_asset(tmp_path: Path) -> None:
    incidents = log(tmp_path)
    opened(incidents)
    opened(incidents, asset_key="eip155:1:0x" + "cd" * 20, kind=EPOCH_FAILED)

    assert len(incidents.open_incidents()) == 2
    assert len(incidents.open_incidents(ASSET)) == 1


def test_closing_twice_is_refused(tmp_path: Path) -> None:
    incidents = log(tmp_path)
    entry = opened(incidents)
    incidents.close_incident(entry["entry_hash"], detail="recovered", occurred_at=AT)

    with pytest.raises(IncidentLogError, match="already closed"):
        incidents.close_incident(entry["entry_hash"], detail="again", occurred_at=AT)


def test_closing_something_never_opened_is_refused(tmp_path: Path) -> None:
    incidents = log(tmp_path)

    with pytest.raises(IncidentLogError, match="no incident was opened"):
        incidents.close_incident("00" * 32, detail="recovered", occurred_at=AT)


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IncidentLogError, match="unknown incident kind"):
        opened(log(tmp_path), kind="VIBES")


def test_an_incident_must_say_what_happened(tmp_path: Path) -> None:
    for detail in ("", "   "):
        with pytest.raises(IncidentLogError, match="what happened"):
            opened(log(tmp_path), detail=detail)


def test_an_instant_must_be_timezone_aware(tmp_path: Path) -> None:
    with pytest.raises(IncidentLogError, match="timezone-aware"):
        opened(log(tmp_path), occurred_at=datetime(2026, 8, 15, 9, 0))


def test_editing_an_entry_breaks_verification(tmp_path: Path) -> None:
    incidents = log(tmp_path)
    opened(incidents)
    entries = [
        strict_json_loads(line) for line in incidents.path.read_bytes().splitlines()
    ]
    entries[0]["detail"] = "nothing was wrong, actually"
    incidents.path.write_bytes(canonical_json_bytes(entries[0]) + b"\n")

    with pytest.raises(IncidentLogError, match="hash does not match"):
        incidents.verify()


def test_reordering_entries_breaks_the_chain(tmp_path: Path) -> None:
    incidents = log(tmp_path)
    first = opened(incidents)
    incidents.close_incident(first["entry_hash"], detail="recovered", occurred_at=AT)
    lines = incidents.path.read_bytes().splitlines()
    incidents.path.write_bytes(lines[1] + b"\n" + lines[0] + b"\n")

    with pytest.raises(IncidentLogError):
        incidents.verify()


def test_truncating_the_final_entry_is_detected(tmp_path: Path) -> None:
    """The case a hash chain cannot catch on its own.

    Cut entries off the end and what remains is a perfectly valid shorter chain. Only the
    separately written head knows how long the log was supposed to be.
    """
    incidents = log(tmp_path)
    first = opened(incidents)
    incidents.close_incident(first["entry_hash"], detail="recovered", occurred_at=AT)
    lines = incidents.path.read_bytes().splitlines()
    incidents.path.write_bytes(lines[0] + b"\n")

    with pytest.raises(IncidentLogError, match="expects 2 entries"):
        incidents.verify()


def test_a_multi_entry_log_without_its_head_cannot_be_shown_complete(
    tmp_path: Path,
) -> None:
    """With several entries and no head, there is no way to know how many were lost.

    A single entry with no head is different — that is an append interrupted before its
    head was written, and it is repaired. Past one entry the two cases are indistinguishable
    from the log alone, so the safe reading is that completeness is unproven.
    """
    incidents = log(tmp_path)
    opened(incidents)
    opened(incidents, detail="a second failure")
    incidents.head_path.unlink()

    with pytest.raises(IncidentLogError, match="completeness cannot be established"):
        incidents.verify()


def test_a_head_naming_the_wrong_entry_is_refused(tmp_path: Path) -> None:
    incidents = log(tmp_path)
    opened(incidents)
    head = strict_json_loads(incidents.head_path.read_bytes())
    head["head_entry_hash"] = "11" * 32
    incidents.head_path.write_bytes(canonical_json_bytes(head) + b"\n")

    with pytest.raises(IncidentLogError, match="does not name the log's final entry"):
        incidents.verify()


def test_a_forged_closure_of_a_later_entry_is_refused(tmp_path: Path) -> None:
    """A closure may only reference an incident opened before it."""
    incidents = log(tmp_path)
    first = opened(incidents)
    second = opened(incidents, detail="a second failure")
    # Rewrite the first entry so it closes the second, which comes after it.
    lines = incidents.path.read_bytes().splitlines()
    forged = strict_json_loads(lines[0])
    forged["closes"] = second["entry_hash"]
    assert forged["closes"] != first["closes"]

    from touchstone.incidents import _entry_hash

    forged["entry_hash"] = _entry_hash({**forged, "entry_hash": ""})
    incidents.path.write_bytes(canonical_json_bytes(forged) + b"\n")
    head = strict_json_loads(incidents.head_path.read_bytes())
    head["count"] = 1
    head["head_entry_hash"] = forged["entry_hash"]
    incidents.head_path.write_bytes(canonical_json_bytes(head) + b"\n")

    with pytest.raises(IncidentLogError, match="never opened before it"):
        incidents.verify()


def test_a_non_json_line_is_refused(tmp_path: Path) -> None:
    incidents = log(tmp_path)
    opened(incidents)
    with incidents.path.open("ab") as handle:
        handle.write(b"{not json\n")

    with pytest.raises(IncidentLogError, match="not strict JSON"):
        incidents.verify()


def test_an_append_interrupted_before_its_head_is_recovered(tmp_path: Path) -> None:
    """The entry is written first, so a crash between the two loses the head, not the log.

    The earlier version of this test performed an ordinary append and checked the result,
    which proves the happy path and nothing about the crash it was named for. This one
    reproduces the interruption: the line is on disk, the head still describes the state
    before it.
    """
    incidents = log(tmp_path)
    first = opened(incidents)
    second = opened(incidents, detail="a second failure")
    # Roll the head back to where it stood between the two writes.
    incidents.head_path.write_bytes(
        canonical_json_bytes(
            {
                "count": 1,
                "head_entry_hash": first["entry_hash"],
                "version": "touchstone.incident-head.v1",
            }
        )
        + b"\n"
    )

    entries = incidents.verify()

    assert len(entries) == 2, "the entry that was fsynced survives"
    repaired = strict_json_loads(incidents.head_path.read_bytes())
    assert repaired["count"] == 2
    assert repaired["head_entry_hash"] == second["entry_hash"]


def test_a_head_that_ran_ahead_of_the_log_is_a_loss_not_a_repair(
    tmp_path: Path,
) -> None:
    """The opposite direction is never repaired: something is missing."""
    incidents = log(tmp_path)
    opened(incidents)
    head = strict_json_loads(incidents.head_path.read_bytes())
    head["count"] = 5

    incidents.head_path.write_bytes(canonical_json_bytes(head) + b"\n")

    with pytest.raises(IncidentLogError, match="expects 5 entries"):
        incidents.verify()


def test_a_killed_writer_does_not_lock_the_log_out_forever(tmp_path: Path) -> None:
    """The sentinel-file version left a lock nobody could clear.

    It was removed in a ``finally``, which a hard kill skips — so a crash left every later
    append failing until a human deleted the file, at exactly the moment a service was
    trying to record why it crashed.
    """
    import subprocess
    import sys

    incidents = log(tmp_path)
    opened(incidents)
    script = f"""
import os, sys
sys.path.insert(0, r"{Path.cwd()}")
from touchstone.locking import exclusive_lock
with exclusive_lock(r"{incidents.lock_path}"):
    os._exit(9)
"""
    killed = subprocess.run([sys.executable, "-c", script], capture_output=True)
    assert killed.returncode == 9

    # The lock file may well still exist; what matters is that it no longer locks.
    entry = opened(incidents, detail="recorded after the crash")

    assert entry["index"] == 1
    assert len(incidents.verify()) == 2


def test_a_crash_before_the_very_first_head_is_repairable(tmp_path: Path) -> None:
    """The first entry deserves the same recovery as every later one."""
    incidents = log(tmp_path)
    entry = opened(incidents)
    incidents.head_path.unlink()

    assert len(incidents.verify()) == 1, "the entry survives"
    assert incidents.head_path.exists(), "and the head was completed"
    assert strict_json_loads(incidents.head_path.read_bytes())["head_entry_hash"] == (
        entry["entry_hash"]
    )
