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
import subprocess
import sys
import time


DEFAULT_INTERVAL_SECONDS = 86_400.0


# How many individual missed slots are named. A service down for a month at a one-minute
# cadence missed forty-three thousand of them; listing each would turn a record into a
# denial of service against whoever reads it. The count is always exact.
MAX_NAMED_MISSES = 64


@dataclass(frozen=True, slots=True)
class ScheduleOutcome:
    """What a run of the schedule actually did, including what it could not do."""

    completed: int = 0
    failed: tuple[datetime, ...] = field(default_factory=tuple)
    missed: tuple[datetime, ...] = field(default_factory=tuple)
    missed_count: int = 0

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
    on_missed: Callable[[datetime], None] | None = None,
) -> ScheduleOutcome:
    """Run immediately, then at fixed cadence, without overlapping or catching up.

    Timing uses a monotonic clock, because a wall clock can step backwards and a schedule
    that follows it would run a slot twice. The *record* of each slot uses the wall clock,
    because "the 09:00 slot was missed" is the only form of that statement anyone can act
    on.
    """
    if not callable(job):
        raise TypeError("job must be callable")
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, (int, float))
        or interval_seconds <= 0
    ):
        raise ValueError("interval_seconds must be positive")
    if max_runs is not None and (type(max_runs) is not int or max_runs < 1):
        raise ValueError("max_runs must be a positive integer or None")

    interval = float(interval_seconds)
    next_run = monotonic()
    scheduled_at = now()
    completed = 0
    failed: list[datetime] = []
    missed: list[datetime] = []
    missed_count = 0

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

        next_run += interval
        scheduled_at = _advanced(scheduled_at, interval)
        current = monotonic()
        if next_run < current:
            # Slots whose moment has already passed. How many is arithmetic, not a loop:
            # a long outage or a clock that jumped would otherwise iterate once per missed
            # slot, and a scheduler that hangs while recording downtime is worse than one
            # that records it roughly.
            skipped = int((current - next_run) // interval) + 1
            for index in range(skipped):
                slot = _advanced(scheduled_at, interval * index)
                missed_count += 1
                if len(missed) < MAX_NAMED_MISSES:
                    missed.append(slot)
                    if on_missed is not None:
                        on_missed(slot)
            next_run += interval * skipped
            scheduled_at = _advanced(scheduled_at, interval * skipped)

    return ScheduleOutcome(
        completed=completed,
        failed=tuple(failed),
        missed=tuple(missed),
        missed_count=missed_count,
    )


def _advanced(moment: datetime, interval: float) -> datetime:
    return datetime.fromtimestamp(moment.timestamp() + interval, timezone.utc)


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

    outcome = run_schedule(
        execute,
        interval_seconds=args.interval_seconds,
        max_runs=args.runs,
        on_failure=report_failure,
        on_missed=report_missed,
    )
    # A failed slot is reported rather than swallowed: the schedule continues, but the
    # exit status still says the work did not all happen.
    return 0 if outcome.completed and not outcome.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
