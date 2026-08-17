"""The scheduler's job is to keep running and to be honest about what it skipped."""

from datetime import datetime, timedelta, timezone
import math
import subprocess

import pytest

from touchstone.schedule import (
    MAX_NAMED_MISSES,
    ScheduleOutcome,
    main,
    run_schedule,
)


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

    schedule(
        job,
        clock,
        max_runs=2,
        on_outage=lambda first, count: outages.append((first, count)),
    )

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

                def job(
                    scheduled_at: datetime, clock=clock, runs=runs, overrun=overrun
                ):
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
    assert (
        schedule(
            lambda at: None, FakeTime(1e9), interval_seconds=60, max_runs=1
        ).completed
        == 1
    )


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), float("-inf")])
def test_a_cadence_that_is_not_a_number_is_refused_before_any_slot_runs(
    interval: float,
) -> None:
    """A configuration error must not arrive as a mid-flight crash.

    NaN compares false against every bound and infinity passes them all, so both reached
    the loop and failed only after a slot had already executed — with whatever side
    effects that slot had.
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


def test_a_span_that_the_clock_cannot_reach_is_refused_up_front() -> None:
    """Every timestamp the run will need, proved before the first job.

    An interval that survives the first step and overflows a later one ran every requested
    slot and *then* failed, with all the work already done.

    The interval has to be unrepresentable on *every* platform, which is not the same as
    unrepresentable here. This asserted with 1e11 and passed on Windows, where
    `datetime.fromtimestamp` raises `OSError: Invalid argument` well before `datetime`'s own
    range runs out — while Linux reaches the year 5197 without complaint and the test failed
    the moment it ran anywhere else. `datetime.max` is 253402300800 seconds after the epoch,
    so a span past that overflows `datetime` itself rather than some libc's opinion of it.
    """
    clock = FakeTime()
    ran = []

    with pytest.raises(ValueError, match="cannot be added to the clock"):
        schedule(lambda at: ran.append(at), clock, interval_seconds=1e12, max_runs=2)

    assert ran == [], "no slot ran"


def test_a_span_the_clock_can_reach_is_not_refused() -> None:
    """Validation must not be stricter than the thing it validates.

    A finite run reaches start + (max_runs - 1) * interval. Proving one interval further
    rejected schedules whose every used timestamp was perfectly representable — including
    a single run, which needs no advancement at all.
    """
    clock = FakeTime()

    assert (
        schedule(lambda at: None, clock, interval_seconds=1e20, max_runs=1).completed
        == 1
    )
    # Two runs a long way apart: the second slot is in the year 2660, which is fine.
    assert (
        schedule(
            lambda at: None, FakeTime(), interval_seconds=2e10, max_runs=2
        ).completed
        == 2
    )


@pytest.mark.parametrize(
    ("interval_seconds", "overflows_while"),
    [
        # 1e7 leaves every individually named miss representable, so only the aggregate
        # jump past all of them overflows.
        (1e7, "advancing past the whole outage"),
        # 2e10 overflows on the very first named miss — 49 slots of 634 years each reaches
        # the year 33000 — which happens *before* the aggregate advancement. Guarding only
        # the aggregate left this call to escape as a bare OSError.
        (2e10, "naming one missed slot"),
    ],
)
def test_an_outage_beyond_the_clocks_reach_ends_the_schedule_in_the_open(
    interval_seconds: float, overflows_while: str
) -> None:
    """The one span that cannot be proved in advance, because the jump is unbounded.

    A long enough outage pushes the next slot past any representable date. That has to end
    the schedule through the same reported path as any other failure rather than escaping
    as a bare OSError once the work is done — and it has to do so at *whichever* of the two
    advancements overflows first.
    """
    clock = FakeTime()
    failures = []
    clock_errors = []
    ran = []

    def job(scheduled_at: datetime) -> None:
        ran.append(scheduled_at)
        if len(ran) == 1:
            clock.current += 1e12  # an outage no clock can name the far side of

    outcome = run_schedule(
        job,
        interval_seconds=interval_seconds,
        max_runs=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: START,
        on_failure=lambda at, error: failures.append(error),
        on_clock_error=lambda at, error: clock_errors.append(error),
    )

    assert len(ran) == 1, "the schedule stopped rather than crashing"
    assert clock_errors and "cannot be represented" in str(clock_errors[0]), (
        f"it reported the overflow while {overflows_while}"
    )
    assert outcome.clock_error == str(clock_errors[0])
    # The slot that ran *succeeded*. Recording it as failed too said two things had
    # happened when one had, and made completed + failed exceed the jobs attempted. It
    # must not reach `on_failure` either: a consumer that only knows about failed slots
    # would open an incident saying a slot it had just completed had failed.
    assert outcome.completed == 1
    assert outcome.failed == ()
    assert failures == []


def test_an_unbounded_schedule_proves_the_slot_it_will_actually_need() -> None:
    """`max_runs=None` has no last slot, but it always needs its next one.

    Collapsing an unbounded run to a zero-span check proved nothing at all, so an
    impossible cadence was accepted, ran one side-effecting job, and only then discovered
    it could not name slot two.
    """
    ran = []
    with pytest.raises(ValueError, match="cannot be added to the clock"):
        run_schedule(
            ran.append,
            interval_seconds=1e20,
            max_runs=None,
            monotonic=FakeTime().monotonic,
            sleep=lambda _: None,
            now=lambda: START,
        )
    assert ran == [], "and it refused before the first job, not after it"


def test_a_finished_schedule_does_not_compute_a_slot_nobody_will_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the last requested run there is no next slot to name.

    Counting jobs cannot show this — the count is the same either way. What distinguishes
    the two is whether a timestamp was computed for a slot that will never run, so the
    advancements themselves are counted.
    """
    import touchstone.schedule as schedule_module

    advanced = schedule_module._advanced
    calls = []

    def counted(moment, interval):
        calls.append(interval)
        return advanced(moment, interval)

    monkeypatch.setattr(schedule_module, "_advanced", counted)
    clock = FakeTime()

    outcome = schedule(lambda at: None, clock, interval_seconds=60, max_runs=2)

    assert outcome.completed == 2
    # One advancement between the two slots, plus the single up-front span check. A third
    # would be the slot after the last run.
    assert len(calls) == 2, f"advancements: {calls}"


def test_the_cli_reports_a_schedule_that_could_not_continue(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A clock error is not a failed slot — but it did end the schedule early.

    Reporting success because no *slot* failed said the work had all happened when the
    schedule had in fact stopped and would never run again. Only an outage can produce
    this at runtime, and its size is unbounded by definition, so the schedule itself is
    stubbed: what is under test here is the CLI's two obligations — pass the callback
    through, and let the outcome reach the exit status.
    """
    import touchstone.schedule as schedule_module

    def stopped_by_the_clock(job, *, on_clock_error=None, **arguments):
        job(START)
        on_clock_error(START, OverflowError("a slot 1e20 seconds after X"))
        return ScheduleOutcome(
            completed=1, failed=(), clock_error="a slot 1e20 seconds after X"
        )

    monkeypatch.setattr(schedule_module, "run_schedule", stopped_by_the_clock)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, *, check: subprocess.CompletedProcess(command, 0),
    )

    assert main(["--runs", "2", "--", "true"]) == 1, (
        "a schedule that stopped is not a schedule that finished"
    )
    assert "the schedule stopped after" in capsys.readouterr().err


@pytest.mark.parametrize(
    "reading",
    [datetime(2026, 8, 15, 9, 0), "2026-08-15T09:00:00Z", 1786_000_000, None],
)
def test_the_scheduler_refuses_a_clock_with_no_timezone(reading: object) -> None:
    """A naive datetime has no offset, so it is not a moment — it is a moment shape.

    Every later `_advanced` call reinterprets it through whatever the host timezone
    happens to be and returns an aware value, so one schedule ends up mixing naive and
    aware slot identities that no longer compare or serialise consistently. It has to be
    caught before the first job, because after that the identities are already written.
    """
    ran = []

    with pytest.raises((TypeError, ValueError), match="now()"):
        run_schedule(
            ran.append,
            interval_seconds=60,
            max_runs=1,
            monotonic=FakeTime().monotonic,
            sleep=lambda _: None,
            now=lambda: reading,
        )

    assert ran == [], "and it refused before the first job, not after it"


def test_a_clock_reading_that_is_not_a_number_ends_the_schedule() -> None:
    """The clock's readings, not only its interval.

    NaN compares false against every bound, so it passed the slack check untouched and
    first became an error inside `math.ceil` — after a slot had already run. A reading
    that is not a number is a clock failure, and this schedule already ends on one.
    """
    ran: list[datetime] = []
    readings = iter([0.0, float("nan")])
    clock_errors: list[BaseException] = []

    outcome = run_schedule(
        ran.append,
        interval_seconds=1.0,
        max_runs=3,
        monotonic=lambda: next(readings, 99.0),
        sleep=lambda seconds: None,
        on_clock_error=lambda moment, error: clock_errors.append(error),
    )

    assert outcome.clock_error is not None, "it ended through the declared clock path"
    assert "monotonic()" in outcome.clock_error
    assert clock_errors, "and the caller was told"
    assert len(ran) <= 1, "a bad reading did not run further slots"
