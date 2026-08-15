"""The scheduler's job is to keep running and to be honest about what it skipped."""

from datetime import datetime, timedelta, timezone
import math
import subprocess

import pytest

from touchstone.schedule import MAX_NAMED_MISSES, main, run_schedule


START = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)


class FakeTime:
    def __init__(self, origin: float = 0.0) -> None:
        # A real monotonic clock reports uptime, not zero. Every test here started at
        # zero, which is precisely where floating-point cancellation does not bite — so a
        # tolerance too small for real magnitudes looked correct.
        self.current = origin
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
    # Ceiling, not floor-plus-one: the slot due at the moment the clock lands is due,
    # not missed, so the count is one lower than a naive division suggests.
    assert outcome.missed_count == 86_400_000 // 60 - 1
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


def test_recording_an_outage_costs_no_more_than_naming_its_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded work, not merely a bounded list.

    Computing the count arithmetically and then looping over every skipped slot to throw
    most of them away left the work proportional to the outage — the thing the bound was
    added to prevent.
    """
    import touchstone.schedule as schedule_module

    advanced = schedule_module._advanced
    calls = {"n": 0}

    def counted(moment, interval):
        calls["n"] += 1
        return advanced(moment, interval)

    monkeypatch.setattr(schedule_module, "_advanced", counted)

    clock = FakeTime()
    runs = []

    def job(scheduled_at: datetime) -> None:
        runs.append(scheduled_at)
        if len(runs) == 1:
            clock.current += 60_000  # a thousand slots pass

    outcome = schedule(job, clock, max_runs=2)

    assert outcome.missed_count == 999, "the count is exact"
    assert len(outcome.missed) == MAX_NAMED_MISSES
    assert calls["n"] <= MAX_NAMED_MISSES + 8, (
        f"work must stay near the bound, not the outage; took {calls['n']} steps"
    )


def test_an_outage_is_reported_once_with_its_exact_size() -> None:
    """A service that never stops never sees the returned outcome."""
    clock = FakeTime()
    outages = []
    runs = []

    def job(scheduled_at: datetime) -> None:
        runs.append(scheduled_at)
        if len(runs) == 1:
            clock.current += 300  # five slots

    schedule(job, clock, max_runs=2, on_outage=lambda first, count: outages.append((first, count)))

    # Four slots passed (60, 120, 180, 240); the 300 slot is due now and is run.
    assert outages == [(START + timedelta(seconds=60), 4)]


def test_a_slot_due_at_this_exact_moment_is_run_not_skipped() -> None:
    """The boundary the catch-up arithmetic got wrong.

    Counting the skipped slots as floor-plus-one treated a slot due at precisely the
    current moment as already missed, then jumped past it — so an outage lasting an exact
    multiple of the interval silently dropped a live slot.
    """
    clock = FakeTime()
    starts = []
    missed = []

    def job(scheduled_at: datetime) -> None:
        starts.append(clock.current)
        if len(starts) == 1:
            clock.current += 240  # exactly four intervals

    outcome = schedule(job, clock, max_runs=2, on_missed=missed.append)

    assert starts == [0.0, 240.0], "the slot due at 240 ran"
    assert outcome.missed_count == 3
    assert missed == [
        START + timedelta(seconds=60),
        START + timedelta(seconds=120),
        START + timedelta(seconds=180),
    ]


@pytest.mark.parametrize("origin", [0.0, 1e5, 1e7, 8.64e7])
@pytest.mark.parametrize("interval", [0.05, 0.1, 0.2, 0.3, 1 / 3, 2.5])
@pytest.mark.parametrize("elapsed", [1, 2, 3, 4, 5, 6, 7])
def test_a_fractional_interval_still_runs_the_slot_that_is_due(
    origin: float, interval: float, elapsed: int
) -> None:
    """The same boundary, over the input domain where floats do not divide cleanly.

    A gap of exactly N intervals divides to a hair over N — 2.0000000000000004 — and a
    bare ceiling reads that as N+1, dropping the live slot again. Integer intervals hid
    this entirely, which is why the first fix looked complete.
    """
    clock = FakeTime(origin)
    starts = []

    def job(scheduled_at: datetime) -> None:
        starts.append(scheduled_at)
        if len(starts) == 1:
            clock.current += interval * elapsed

    outcome = schedule(job, clock, interval_seconds=interval, max_runs=2)

    assert outcome.missed_count == elapsed - 1, (
        f"{elapsed} slots elapsed, so {elapsed - 1} were missed and one was due"
    )
    # Within a microsecond: datetime has microsecond resolution, and the scheduler
    # accumulates its slot times by repeated addition rather than one multiplication.
    expected = START + timedelta(seconds=interval * elapsed)
    assert abs((starts[1] - expected).total_seconds()) < 1e-5


def test_a_partial_gap_still_counts_the_slot_it_passed() -> None:
    """The tolerance must not swallow a slot that genuinely went by."""
    clock = FakeTime()
    starts = []

    def job(scheduled_at: datetime) -> None:
        starts.append(scheduled_at)
        if len(starts) == 1:
            clock.current += 0.25  # two and a half intervals of 0.1

    outcome = schedule(job, clock, interval_seconds=0.1, max_runs=2)

    assert outcome.missed_count == 2


def test_an_outage_is_never_reported_with_a_count_of_zero() -> None:
    """The catch-up branch must not announce an outage of no slots.

    Lateness that the float tolerance absorbs produced a skip count of zero, and the
    service dutifully recorded that a slot "did not run" at the very moment it ran. The
    requirement is about the report, not about how much lateness is acceptable — that is a
    separate policy question, and this asserts only the invariant.
    """
    for origin in (0.0, 1e5, 1e7):
        for interval in (0.1, 0.2, 60, 86_400):
            # The last two are the ones that matter: an overrun of exactly one interval,
            # and one representable step past it. Exact and half-interval gaps never land
            # inside the float tolerance, so a sweep of only those passes against the
            # broken code and proves nothing.
            for overrun in (
                0.0,
                interval,
                interval * 2.5,
                math.nextafter(interval, math.inf),
                math.nextafter(interval * 3, math.inf),
            ):
                clock = FakeTime(origin)
                outages = []
                runs = []

                def job(scheduled_at: datetime, clock=clock, runs=runs, overrun=overrun):
                    runs.append(scheduled_at)
                    if len(runs) == 1:
                        clock.current += overrun

                outcome = schedule(
                    job,
                    clock,
                    interval_seconds=interval,
                    max_runs=2,
                    on_outage=lambda first, count: outages.append(count),
                )

                assert all(count > 0 for count in outages), (
                    f"origin={origin} interval={interval} overrun={overrun} reported "
                    f"{outages}"
                )
                assert sum(outages) == outcome.missed_count


def test_lateness_of_a_single_ulp_reports_no_outage() -> None:
    """The case the sweep above cannot reach, and the one the defect actually needed.

    Exact and half-interval overruns never land inside the float tolerance, so a sweep of
    them passes against the broken code too. Being one ulp past the due instant is what
    produces a skip count of zero — and what used to be announced as an outage of no
    slots, telling an operator a slot had not run at the moment it ran.
    """
    origin = 100_000.0
    clock = FakeTime(origin)
    outages = []
    runs = []

    def job(scheduled_at: datetime) -> None:
        runs.append(scheduled_at)
        if len(runs) == 1:
            # One representable step past exactly one interval.
            clock.current = math.nextafter(origin + 60.0, math.inf)

    outcome = schedule(
        job,
        clock,
        interval_seconds=60,
        max_runs=2,
        on_outage=lambda first, count: outages.append(count),
    )

    assert outcome.missed_count == 0
    assert outages == [], "an outage of zero slots must never be reported"


def test_a_cadence_the_clock_cannot_resolve_is_refused() -> None:
    """An unbounded tolerance is a grace period wearing a correction's clothes.

    At a billion seconds of uptime a microsecond interval leaves rounding noise worth most
    of a slot, so a slot genuinely in the past would be treated as due now. Refusing the
    configuration is the honest answer; silently tolerating almost a whole interval is not.
    """
    clock = FakeTime(1_000_000_000.0)

    with pytest.raises(ValueError, match="too fine for a clock"):
        schedule(lambda at: None, clock, interval_seconds=1.02e-6, max_runs=1)

    # A realistic cadence at the same uptime is unaffected.
    assert schedule(lambda at: None, FakeTime(1e9), interval_seconds=60, max_runs=1).completed == 1


@pytest.mark.parametrize(
    "interval", [float("nan"), float("inf"), float("-inf"), 1e20, 1e300]
)
def test_an_unusable_cadence_is_refused_before_any_slot_runs(interval: float) -> None:
    """A configuration error must not arrive as a mid-flight crash.

    NaN compares false against every bound and infinity passes them all, so both reached
    the loop and failed only after a slot had already executed — with whatever side
    effects that slot had. A finite but enormous interval did the same, overflowing the
    wall clock on the first advancement.
    """
    ran = []

    with pytest.raises(ValueError):
        run_schedule(
            lambda at: ran.append(at),
            interval_seconds=interval,
            max_runs=1,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: None,
            now=lambda: START,
        )

    assert ran == [], "nothing ran before the configuration was rejected"
