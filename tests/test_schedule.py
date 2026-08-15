"""The scheduler's job is to keep running and to be honest about what it skipped."""

from datetime import datetime, timedelta, timezone
import subprocess

import pytest

from touchstone.schedule import MAX_NAMED_MISSES, main, run_schedule


START = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)


class FakeTime:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def schedule(job, clock: FakeTime, **overrides):
    arguments = {
        "interval_seconds": 60,
        "monotonic": clock.monotonic,
        "sleep": clock.sleep,
        "now": lambda: START,
    }
    arguments.update(overrides)
    return run_schedule(job, **arguments)


def test_schedule_runs_immediately_then_at_fixed_cadence() -> None:
    clock = FakeTime()
    starts = []

    outcome = schedule(lambda at: starts.append(clock.current), clock, max_runs=3)

    assert outcome.completed == 3
    assert outcome.failed == ()
    assert starts == [0.0, 60.0, 120.0]
    assert clock.sleeps == [60.0, 60.0]


def test_each_slot_is_told_the_moment_it_was_scheduled_for() -> None:
    """The slot's own time, not the time the work happened to start."""
    clock = FakeTime()
    scheduled = []

    schedule(lambda at: scheduled.append(at), clock, max_runs=3)

    assert scheduled == [
        START,
        START + timedelta(seconds=60),
        START + timedelta(seconds=120),
    ]


def test_a_failed_slot_does_not_end_the_schedule() -> None:
    """Criterion 1. A transient fault must not become an outage lasting until someone looks."""
    clock = FakeTime()
    attempts = []
    failures = []

    def job(scheduled_at: datetime) -> None:
        attempts.append(scheduled_at)
        if len(attempts) == 1:
            raise RuntimeError("epoch failed")

    outcome = schedule(
        job,
        clock,
        max_runs=3,
        on_failure=lambda at, error: failures.append((at, error)),
    )

    assert len(attempts) == 3, "the schedule kept going after the failure"
    assert outcome.completed == 2
    assert outcome.failed == (START,)
    assert [at for at, _ in failures] == [START]
    assert str(failures[0][1]) == "epoch failed"


def test_missed_slots_are_recorded_by_their_own_timestamps_and_never_run_late() -> None:
    """Criterion 2. Running yesterday's slot today would file today's evidence as yesterday's."""
    clock = FakeTime()
    starts = []
    missed = []

    def job(scheduled_at: datetime) -> None:
        starts.append(clock.current)
        if len(starts) == 1:
            clock.current += 125  # two slots pass while this one runs

    outcome = schedule(job, clock, max_runs=2, on_missed=missed.append)

    assert starts == [0.0, 180.0], "no catch-up run happened"
    assert outcome.missed == (
        START + timedelta(seconds=60),
        START + timedelta(seconds=120),
    )
    assert missed == list(outcome.missed)
    assert outcome.missed_count == 2
    assert not outcome.misses_were_truncated


def test_a_long_outage_is_counted_exactly_and_named_in_part() -> None:
    """Recording downtime must not itself hang.

    Naming every slot meant one iteration per missed slot, so a clock jump or a month of
    downtime at a short cadence would loop for as long as it took to describe.
    """
    clock = FakeTime()
    runs = []

    def job(scheduled_at: datetime) -> None:
        runs.append(scheduled_at)
        if len(runs) == 1:
            clock.current += 86_400_000  # a thousand days

    outcome = schedule(job, clock, max_runs=2)

    assert outcome.completed == 2
    assert outcome.missed_count == 86_400_000 // 60
    assert len(outcome.missed) == MAX_NAMED_MISSES
    assert outcome.misses_were_truncated
    assert outcome.missed[0] == START + timedelta(seconds=60)


@pytest.mark.parametrize(
    ("interval", "runs"),
    [(0, 1), (-1, 1), (True, 1), (1, 0), (1, True)],
)
def test_schedule_rejects_invalid_configuration(interval, runs) -> None:
    with pytest.raises(ValueError):
        run_schedule(lambda at: None, interval_seconds=interval, max_runs=runs)


def test_schedule_rejects_a_job_that_is_not_callable() -> None:
    with pytest.raises(TypeError):
        run_schedule("not a job", max_runs=1)


def test_cli_executes_argument_vector_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(command, *, check):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert (
        main(["--runs", "1", "--", "python", "-m", "touchstone.epoch", "--fixtures"])
        == 0
    )
    assert calls == [(["python", "-m", "touchstone.epoch", "--fixtures"], True)]


def test_cli_reports_a_failed_slot_in_its_exit_status(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The schedule continues, and the status still says the work did not all happen."""

    def run(command, *, check):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", run)

    assert main(["--runs", "1", "--", "false"]) == 1
    assert "failed" in capsys.readouterr().err
