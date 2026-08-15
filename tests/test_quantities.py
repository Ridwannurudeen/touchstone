"""Every boundary that takes a duration refuses the same set of non-durations.

Each of these had its own check and each was different: some accepted NaN, some accepted
infinity, and all of them raised a bare ``OverflowError`` on an integer too large to be a
float. A timeout that never elapses is not a long timeout — it is the absence of one — and
finding that out at the socket rather than at the configuration is finding out too late.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from touchstone.compiler import HTTPProvider
from touchstone.oracles import HTTPRPC
from touchstone.quantities import (
    finite_non_negative,
    finite_number,
    finite_positive,
)
from touchstone.schedule import run_schedule

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from probe_sources import probe  # noqa: E402


NOT_A_DURATION = [
    float("nan"),
    float("inf"),
    float("-inf"),
    10**1000,  # a perfectly ordinary int, and larger than any float
    True,  # an int in Python, and never something anyone configured
    "30",
    None,
]


@pytest.mark.parametrize("value", NOT_A_DURATION)
def test_finite_number_refuses_everything_that_is_not_one(value: object) -> None:
    with pytest.raises(ValueError):
        finite_number(value, "timeout")


@pytest.mark.parametrize("value", [*NOT_A_DURATION, 0, -1.0])
def test_finite_positive_refuses_a_duration_that_never_elapses(value: object) -> None:
    with pytest.raises(ValueError):
        finite_positive(value, "timeout")


@pytest.mark.parametrize("value", [*NOT_A_DURATION, -1.0])
def test_finite_non_negative_refuses_a_negative_duration(value: object) -> None:
    with pytest.raises(ValueError):
        finite_non_negative(value, "backoff")


def test_a_finite_duration_is_returned_as_a_float() -> None:
    assert finite_positive(30, "timeout") == 30.0
    assert finite_non_negative(0, "backoff") == 0.0
    assert isinstance(finite_positive(30, "timeout"), float)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 10**1000, 0, -1])
def test_the_model_provider_refuses_a_timeout_that_never_elapses(
    monkeypatch: pytest.MonkeyPatch, timeout: object
) -> None:
    monkeypatch.setenv("TOUCHSTONE_MODEL_ENDPOINT", "https://example.invalid/v1")
    monkeypatch.setenv("TOUCHSTONE_MODEL_KEY", "k")
    monkeypatch.setenv("TOUCHSTONE_MODEL_NAME", "m")

    with pytest.raises(ValueError, match="timeout"):
        HTTPProvider(timeout=timeout)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 10**1000, 0, -1])
def test_the_rpc_client_refuses_a_timeout_that_never_elapses(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        HTTPRPC("https://example.invalid/rpc", timeout=timeout)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 10**1000, 0, -1])
def test_the_source_prober_refuses_a_timeout_that_never_elapses(
    timeout: object,
) -> None:
    """Refused before the socket: urlopen turns these into a read that never returns."""
    with pytest.raises(ValueError, match="timeout"):
        probe(_target(), timeout=timeout)


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), 10**1000, 0, -1])
def test_the_scheduler_refuses_an_interval_that_is_not_a_duration(
    interval: object,
) -> None:
    ran = []

    with pytest.raises(ValueError, match="interval_seconds"):
        run_schedule(ran.append, interval_seconds=interval, max_runs=1)

    assert ran == [], "and it refused before the first job, not after it"


def _target():
    from probe_sources import ProbeTarget

    return ProbeTarget(
        manifest="superstate-ustb.json",
        source_id="superstate-ustb-nav",
        url="https://api.superstate.com/v1/funds/1/nav-daily",
        max_bytes=1024,
        expected_mime="application/json",
    )
