import subprocess

import pytest

from touchstone.schedule import main, run_schedule


class FakeTime:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def test_schedule_runs_immediately_then_at_fixed_cadence() -> None:
    clock = FakeTime()
    starts = []

    def job() -> None:
        starts.append(clock.current)

    assert (
        run_schedule(
            job,
            interval_seconds=60,
            max_runs=3,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        == 3
    )
    assert starts == [0.0, 60.0, 120.0]
    assert clock.sleeps == [60.0, 60.0]


def test_schedule_skips_missed_slots_without_overlap() -> None:
    clock = FakeTime()
    starts = []

    def job() -> None:
        starts.append(clock.current)
        if len(starts) == 1:
            clock.current += 125

    run_schedule(
        job,
        interval_seconds=60,
        max_runs=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert starts == [0.0, 180.0]
    assert clock.sleeps == [55.0]


def test_schedule_propagates_job_failure() -> None:
    def fail() -> None:
        raise RuntimeError("epoch failed")

    with pytest.raises(RuntimeError, match="epoch failed"):
        run_schedule(fail, max_runs=2)


@pytest.mark.parametrize(
    ("interval", "runs"),
    [(0, 1), (-1, 1), (True, 1), (1, 0), (1, True)],
)
def test_schedule_rejects_invalid_configuration(interval, runs) -> None:
    with pytest.raises(ValueError):
        run_schedule(lambda: None, interval_seconds=interval, max_runs=runs)


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
