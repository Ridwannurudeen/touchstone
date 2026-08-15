"""Fixed-cadence scheduler for Touchstone epoch commands.

Two properties matter more than cadence accuracy.

A slot that fails must not end the schedule. An epoch can fail for reasons that have
nothing to do with the next one — a source that was briefly unreachable, a node that
restarted — and a scheduler that stops on the first of them turns a transient fault into an
outage that lasts until somebody notices.

A slot that was missed must be recorded and never run late. Catching up looks helpful and
is not: an epoch is a statement about a particular day's evidence, and running yesterday's
slot today would retrieve today's evidence and file it under yesterday. The slot is
reported as missed, with the wall-clock time it should have run, and skipped.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import subprocess
import sys
import time

from touchstone.quantities import finite_positive


DEFAULT_INTERVAL_SECONDS = 86_400.0


# How many individual missed slots are named. A service down for a month at a one-minute
# cadence missed forty-three thousand of them; listing each would turn a record into a
# denial of service against whoever reads it. The count is always exact.
MAX_NAMED_MISSES = 64

# Tolerance for deciding whether a slot is due now rather than missed. The floor keeps a
# tiny interval from producing a uselessly small one; the ulp term is what actually matters
# once the clock reports real uptime rather than a test's zero.
_SLOT_EPSILON = 1e-12
_SLOT_ULPS = 8
# The most rounding noise that may be treated as "the same instant". Beyond this the
# tolerance would be a grace period rather than a correction, so the configuration is
# refused instead.
_MAX_SLACK_SLOTS = 1 / 1024


@dataclass(frozen=True, slots=True)
class ScheduleOutcome:
    """What a run of the schedule actually did, including what it could not do."""

    completed: int = 0
    failed: tuple[datetime, ...] = field(default_factory=tuple)
    missed: tuple[datetime, ...] = field(default_factory=tuple)
    missed_count: int = 0
    # Set when the schedule stopped because it could not name the next slot. That is not a
    # failed slot — the slot that ran succeeded — so it is reported separately rather than
    # counted among the failures.
    clock_error: str | None = None

    @property
    def attempted(self) -> int:
        return self.completed + len(self.failed)

    @property
    def misses_were_truncated(self) -> bool:
        """Whether `missed` names every slot it counts."""
        return self.missed_count > len(self.missed)


def run_schedule(
    job: Callable[[datetime], None],
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_runs: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_failure: Callable[[datetime, BaseException], None] | None = None,
    on_clock_error: Callable[[datetime, BaseException], None] | None = None,
    on_missed: Callable[[datetime], None] | None = None,
    on_outage: Callable[[datetime, int], None] | None = None,
) -> ScheduleOutcome:
    """Run immediately, then at fixed cadence, without overlapping or catching up.

    Timing uses a monotonic clock, because a wall clock can step backwards and a schedule
    that follows it would run a slot twice. The *record* of each slot uses the wall clock,
    because "the 09:00 slot was missed" is the only form of that statement anyone can act
    on.
    """
    if not callable(job):
        raise TypeError("job must be callable")
    # Finiteness before anything runs. NaN compares false against every bound, infinity
    # passes them all, and an integer too large to be a float raised from inside the check
    # itself — so all three were discovered only after the first slot had executed.
    finite_positive(interval_seconds, "interval_seconds")
    if max_runs is not None and (type(max_runs) is not int or max_runs < 1):
        raise ValueError("max_runs must be a positive integer or None")

    interval = float(interval_seconds)
    next_run = monotonic()
    # Checked once here so an unusable cadence is refused before any work happens, rather
    # than part-way through the first catch-up.
    _slot_slack(max(abs(next_run), interval), interval)
    scheduled_at = now()
    # The clock's *result*, not merely its interval. A naive datetime has no offset, so
    # every later `_advanced` call reinterprets it through whatever the host timezone
    # happens to be — and returns an aware value, so one schedule ends up mixing naive and
    # aware slot identities that no longer compare or serialise consistently.
    if not isinstance(scheduled_at, datetime):
        raise TypeError("now() must return a datetime")
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ValueError("now() must return a timezone-aware datetime")
    scheduled_at = scheduled_at.astimezone(timezone.utc)
    try:
        # Every timestamp the run will need, proved before the first slot runs. A finite
        # but enormous interval passes every numeric bound and then overflows the wall
        # clock *after* a job has executed — a configuration error reported as a mid-flight
        # crash with side effects already taken.
        #
        # The span is exactly what the schedule reaches and no more. A finite run ends at
        # start + (max_runs - 1) * interval; proving start + max_runs * interval rejected
        # schedules whose every used timestamp was representable. An unbounded run has no
        # last slot, but it always needs its next one, so it is proved for one interval —
        # collapsing None to a zero span proved nothing and let an impossible cadence run a
        # side-effecting job before failing.
        _advanced(
            scheduled_at, interval * (1 if max_runs is None else max(0, max_runs - 1))
        )
    except ScheduleClockError as error:
        raise ValueError(
            f"interval_seconds={interval_seconds!r} cannot be added to the clock: {error}"
        ) from error
    completed = 0
    failed: list[datetime] = []
    missed: list[datetime] = []
    missed_count = 0
    clock_error: str | None = None

    while max_runs is None or completed + len(failed) < max_runs:
        delay = next_run - monotonic()
        if delay > 0:
            sleep(delay)
        try:
            job(scheduled_at)
        except Exception as error:  # noqa: BLE001 - a failed slot must not end the schedule
            failed.append(scheduled_at)
            if on_failure is not None:
                on_failure(scheduled_at, error)
        else:
            completed += 1

        if max_runs is not None and completed + len(failed) >= max_runs:
            # Nothing more will run, so nothing more needs a timestamp. Advancing anyway
            # computed a slot no one would ever use — and for a long interval that
            # arithmetic overflowed *after* every requested job had already succeeded.
            break

        next_run += interval
        try:
            scheduled_at = _advanced(scheduled_at, interval)
        except ScheduleClockError as error:
            # The slot that just ran succeeded; what failed is naming the next one. Adding
            # it to `failed` counted one slot as both completed and failed, so the outcome
            # said two things had happened when one had.
            clock_error = str(error)
            if on_clock_error is not None:
                on_clock_error(scheduled_at, error)
            break
        current = monotonic()
        if _slots_elapsed(current, next_run, interval) > 0:
            # Slots whose moment has already passed. How many is arithmetic, not a loop:
            # a long outage or a clock that jumped would otherwise iterate once per missed
            # slot, and a scheduler that hangs while recording downtime is worse than one
            # that records it roughly.
            # Ceiling, not floor-plus-one. A slot due at exactly this moment is due, not
            # missed: the old form counted it as skipped and then jumped past it, so an
            # outage of an exact multiple of the interval silently dropped a live slot.
            #
            # The tolerance matters as much as the ceiling. With a fractional interval the
            # division lands a few ulps above a whole number — 2.0000000000000004 for a
            # gap that is exactly two intervals — and a bare ceiling reads that as three,
            # dropping the live slot all over again. Anything within a hair of a whole
            # number is that whole number; a slot a nanosecond late is due, not missed.
            skipped = _slots_elapsed(current, next_run, interval)
            assert skipped > 0
            # Only the slots actually named cost anything. Iterating over every skipped
            # slot to discard most of them made the work proportional to the outage, which
            # is the opposite of what a recovering service needs.
            names = min(skipped, MAX_NAMED_MISSES - len(missed))
            try:
                named_slots = [
                    _advanced(scheduled_at, interval * index)
                    for index in range(max(names, 0))
                ]
            except ScheduleClockError as error:
                # Naming an individual missed slot can overflow just as the aggregate
                # advancement can, and it happens first. Guarding only the aggregate left
                # the earlier call to escape unreported.
                clock_error = str(error)
                if on_clock_error is not None:
                    on_clock_error(scheduled_at, error)
                break
            for slot in named_slots:
                missed.append(slot)
                if on_missed is not None:
                    on_missed(slot)
            if on_outage is not None and skipped > 0:
                # Only when something was actually missed. Lateness inside the tolerance
                # produces a skip count of zero, and reporting that as an outage told the
                # operator a slot had not run at the very moment it was running.
                on_outage(scheduled_at, skipped)
            missed_count += skipped
            next_run += interval * skipped
            try:
                scheduled_at = _advanced(scheduled_at, interval * skipped)
            except ScheduleClockError as error:
                clock_error = str(error)
                if on_clock_error is not None:
                    on_clock_error(scheduled_at, error)
                break

    return ScheduleOutcome(
        completed=completed,
        failed=tuple(failed),
        missed=tuple(missed),
        missed_count=missed_count,
        clock_error=clock_error,
    )


def _slots_elapsed(current: float, next_run: float, interval: float) -> int:
    """How many slots are strictly in the past. A slot due now is due, not missed.

    The tolerance has to be derived from the magnitudes involved, not fixed. A monotonic
    clock reports uptime, so the operands are large and their difference loses precision
    in proportion: at ten million seconds one ulp is about 1.9ns, which divided by a
    fifth-of-a-second interval is 9.3e-9 of a slot — larger than a fixed 1e-9 quotient
    tolerance could absorb, so a gap of exactly N intervals read as N+1 and the slot due
    at that instant was skipped. Tests that start their clock at zero never see it.
    """
    ratio = (current - next_run) / interval
    slack = _slot_slack(max(abs(current), abs(next_run), interval), interval)
    return math.ceil(ratio - slack)


def _slot_slack(scale: float, interval: float) -> float:
    """Float noise expressed as a fraction of one slot, and refused if it is not small.

    The tolerance exists to stop an exact multiple being miscounted, and nothing else. Left
    unbounded it grows with the clock and shrinks with the interval, so a microsecond
    cadence at a billion seconds of uptime produced a slack of 0.93 *slots* — silently a
    grace period of almost a whole interval, which is precisely what this is not.

    Rather than quietly tolerate that, the configuration is refused: below roughly
    ``8192 * ulp(clock)`` the clock cannot resolve the interval, and a scheduler that
    cannot tell one slot from the next should say so instead of guessing.
    """
    slack = max(_SLOT_ULPS * math.ulp(scale) / interval, _SLOT_EPSILON)
    if slack > _MAX_SLACK_SLOTS:
        raise ValueError(
            f"interval_seconds={interval!r} is too fine for a clock reading {scale!r}: "
            f"one slot is worth {slack:.3f} slots of rounding noise"
        )
    return slack


class ScheduleClockError(ValueError):
    """A slot time cannot be represented on this clock."""


def _advanced(moment: datetime, interval: float) -> datetime:
    try:
        return datetime.fromtimestamp(moment.timestamp() + interval, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ScheduleClockError(
            f"a slot {interval} seconds after {moment.isoformat()} cannot be represented"
        ) from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a Touchstone command at a fixed, non-overlapping cadence"
    )
    parser.add_argument(
        "--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS
    )
    parser.add_argument("--runs", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    def execute(scheduled_at: datetime) -> None:
        del scheduled_at
        subprocess.run(command, check=True)

    def report_failure(scheduled_at: datetime, error: BaseException) -> None:
        print(f"slot {scheduled_at.isoformat()} failed: {error}", file=sys.stderr)

    def report_missed(scheduled_at: datetime) -> None:
        print(f"slot {scheduled_at.isoformat()} was missed", file=sys.stderr)

    def report_clock_error(scheduled_at: datetime, error: BaseException) -> None:
        print(f"the schedule stopped after {scheduled_at.isoformat()}: {error}", file=sys.stderr)

    outcome = run_schedule(
        execute,
        interval_seconds=args.interval_seconds,
        max_runs=args.runs,
        on_failure=report_failure,
        on_missed=report_missed,
        on_clock_error=report_clock_error,
    )
    # A failed slot is reported rather than swallowed: the schedule continues, but the
    # exit status still says the work did not all happen. A clock error is not a failed
    # slot and is not counted as one — but it did end the schedule early, so reporting
    # success because no *slot* failed said the work had all happened when it had not.
    if outcome.clock_error is not None:
        return 1
    return 0 if outcome.completed and not outcome.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
