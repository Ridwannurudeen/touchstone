"""Every boundary that takes a duration refuses the same set of non-durations.

Each of these had its own check and each was different: some accepted NaN, some accepted
infinity, and all of them raised a bare ``OverflowError`` on an integer too large to be a
float. A timeout that never elapses is not a long timeout — it is the absence of one — and
finding that out at the socket rather than at the configuration is finding out too late.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
import sys

import pytest

from touchstone.compiler import HTTPProvider
from touchstone.oracles import HTTPRPC
from touchstone.quantities import (
    finite_non_negative,
    finite_number,
    finite_positive,
    utc_instant,
)
from touchstone.schedule import run_schedule

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from probe_sources import probe  # noqa: E402


# The exact domain every boundary must refuse. Listing a subset per boundary is how the
# sweep went incomplete in the first place: a boundary that accepted `True` or `"30"` still
# passed its own narrower list.
REFUSED_DURATIONS = [
    float("nan"),
    float("inf"),
    float("-inf"),
    10**1000,
    True,
    "30",
    None,
    0,
    -1,
]


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


@pytest.mark.parametrize("timeout", REFUSED_DURATIONS)
def test_the_model_provider_refuses_a_timeout_that_never_elapses(
    monkeypatch: pytest.MonkeyPatch, timeout: object
) -> None:
    monkeypatch.setenv("TOUCHSTONE_MODEL_ENDPOINT", "https://example.invalid/v1")
    monkeypatch.setenv("TOUCHSTONE_MODEL_KEY", "k")
    monkeypatch.setenv("TOUCHSTONE_MODEL_NAME", "m")

    with pytest.raises(ValueError, match="timeout"):
        HTTPProvider(timeout=timeout)


@pytest.mark.parametrize("timeout", REFUSED_DURATIONS)
def test_the_rpc_client_refuses_a_timeout_that_never_elapses(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        HTTPRPC("https://example.invalid/rpc", timeout=timeout)


@pytest.mark.parametrize("timeout", REFUSED_DURATIONS)
def test_the_source_prober_refuses_a_timeout_that_never_elapses(
    timeout: object,
) -> None:
    """Refused before the socket: urlopen turns these into a read that never returns."""
    with pytest.raises(ValueError, match="timeout"):
        probe(_target(), timeout=timeout)


@pytest.mark.parametrize("interval", REFUSED_DURATIONS)
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


@pytest.mark.parametrize("receipt_timeout", REFUSED_DURATIONS)
def test_the_publisher_refuses_a_receipt_timeout_that_never_elapses(
    tmp_path: Path, receipt_timeout: object
) -> None:
    """web3 waits while `time.time() > begun_at + timeout` is false, which NaN and
    infinity never make true — so the wait that bounds a broadcast would not bound it."""
    from touchstone.publish import PublisherClient
    from touchstone.translog import TransparencyLog

    with pytest.raises(ValueError, match="receipt_timeout"):
        PublisherClient(
            backend=None,
            transparency_log=TransparencyLog(tmp_path / "transparency.jsonl"),
            pending_path=tmp_path / "pending.json",
            receipt_timeout=receipt_timeout,
        )


@pytest.mark.parametrize(
    "backoff", [value for value in REFUSED_DURATIONS if value != 0 or value is True]
)
def test_the_service_refuses_a_backoff_that_is_not_a_duration(
    tmp_path: Path, backoff: object
) -> None:
    """Zero is a legitimate backoff here, so it is excluded; everything else is not."""
    from run_service import Service

    with pytest.raises(ValueError, match="backoff_seconds"):
        Service(
            client=None,
            operations=_operations_at(tmp_path),
            incidents=None,
            asset_key="eip155:1:0x" + "11" * 20,
            backoff_seconds=backoff,
        )


@pytest.mark.parametrize("timeout", REFUSED_DURATIONS)
def test_the_source_fetcher_refuses_a_timeout_that_never_elapses(
    timeout: object,
) -> None:
    from touchstone.sources import fetch_source

    with pytest.raises(ValueError, match="timeout"):
        fetch_source(
            "superstate-ustb-nav-daily",
            store=None,
            transport=None,
            timeout=timeout,
        )


@pytest.mark.parametrize("timeout", REFUSED_DURATIONS)
def test_the_isolated_normalizer_refuses_a_timeout_that_never_elapses(
    timeout: object,
) -> None:
    """A worker with no wall-clock bound is a worker that is never reaped."""
    from touchstone.normalize.ustb import normalize_ustb_payload_isolated

    with pytest.raises(ValueError, match="timeout"):
        normalize_ustb_payload_isolated(
            "superstate-ustb-nav-daily", b"{}", timeout=timeout
        )


@pytest.mark.parametrize(
    "confidence", [float("nan"), float("inf"), 10**1000, True, "1"]
)
def test_a_control_record_refuses_a_confidence_that_is_not_a_number(
    confidence: object,
) -> None:
    """`float(10**1000)` raised OverflowError before `isfinite` was ever reached."""
    from test_controls import make_control

    with pytest.raises((TypeError, ValueError), match="compiler_confidence"):
        make_control(compiler_confidence=confidence)


def _operations_at(tmp_path):
    class _At:
        directory = tmp_path / "operations"

    return _At()


class _StatefulZone(tzinfo):
    """Answers the first offset request and declines every one after it."""

    def __init__(self) -> None:
        self.reads = 0

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        self.reads += 1
        return timedelta(0) if self.reads == 1 else None

    def dst(self, dt: datetime | None) -> None:
        return None


class _HostileZone(tzinfo):
    """A tzinfo is caller code, and caller code can raise anything."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise RuntimeError("this zone refuses to say")

    def dst(self, dt: datetime | None) -> None:
        return None


def test_an_instant_is_resolved_from_the_offset_that_was_validated() -> None:
    """Validating an offset and then converting asks the caller's zone twice.

    A zone that answers the check and then declines left `astimezone` to fall back on the
    host's local zone, so noon UTC was stored as 11:00Z on a UTC+1 machine and as
    something else again elsewhere — in a record written to outlive the process.
    """
    zone = _StatefulZone()

    resolved = utc_instant(datetime(2026, 8, 15, 12, 0, tzinfo=zone), "moment")

    assert resolved == datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert zone.reads == 1, "the offset was observed exactly once"


def test_a_zone_that_refuses_to_answer_is_this_modules_refusal() -> None:
    with pytest.raises(ValueError, match="could not report a UTC offset"):
        utc_instant(datetime(2026, 8, 15, 12, 0, tzinfo=_HostileZone()), "moment")


def test_an_instant_that_cannot_be_converted_is_this_modules_refusal() -> None:
    """Aware, and still not convertible: the conversion underflows the date range."""
    earliest = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14)))

    with pytest.raises(ValueError, match="cannot be converted to UTC"):
        utc_instant(earliest, "moment")


@pytest.mark.parametrize(
    "value", [None, "2026-08-15T12:00:00Z", datetime(2026, 8, 15, 12, 0)]
)
def test_only_an_aware_datetime_is_an_instant(value: object) -> None:
    with pytest.raises(ValueError, match="moment must be"):
        utc_instant(value, "moment")
