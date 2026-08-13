"""Fixed-cadence scheduler for Touchstone epoch commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import subprocess
import time


DEFAULT_INTERVAL_SECONDS = 86_400.0


def run_schedule(
    job: Callable[[], None],
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_runs: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run immediately, then at fixed cadence without overlapping invocations."""
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
    completed = 0
    while max_runs is None or completed < max_runs:
        delay = next_run - monotonic()
        if delay > 0:
            sleep(delay)
        job()
        completed += 1
        next_run += interval
        current = monotonic()
        while next_run < current:
            next_run += interval
    return completed


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

    def execute() -> None:
        subprocess.run(command, check=True)

    return (
        0
        if run_schedule(
            execute,
            interval_seconds=args.interval_seconds,
            max_runs=args.runs,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
