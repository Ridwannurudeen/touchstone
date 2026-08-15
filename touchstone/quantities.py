"""One place that decides whether a number is usable as a duration.

Every timeout, interval and delay in this project had its own check, and each of them was
slightly different: some accepted NaN, some accepted infinity, and all of them raised a raw
``OverflowError`` on an integer too large to convert. A duration that never elapses is not
a long duration — it is the absence of one — and finding that out at the socket boundary
rather than at the configuration boundary is finding out too late.

``bool`` is refused everywhere because ``True`` is an ``int`` in Python, and a timeout of
``True`` second is not something anyone meant to configure.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math


def finite_number(value: object, field: str) -> float:
    """Return ``value`` as a float, or say precisely why it cannot be one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        # An integer literal can be far larger than any float. Converting it is where that
        # shows up, and it surfaced as a bare OverflowError from inside math.isfinite.
        raise ValueError(f"{field} is too large to be a duration") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def finite_positive(value: object, field: str) -> float:
    """A duration that must actually elapse."""
    number = finite_number(value, field)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def finite_non_negative(value: object, field: str) -> float:
    """A duration that may be zero, but must still be a duration."""
    number = finite_number(value, field)
    if number < 0:
        raise ValueError(f"{field} must not be negative")
    return number


def utc_instant(value: object, field: str) -> datetime:
    """Return ``value`` as one UTC instant, resolved from a single offset observation.

    Awareness used to be established by asking the caller's ``tzinfo`` for an offset, and
    the value was then converted with ``astimezone``, which asks it again. A ``tzinfo`` is
    an object rather than a constant: one that answered the check and then declined left
    the conversion to fall back on the host's local zone, so the same input became a
    different instant on a different machine — in records written to outlive the process.
    The offset that was validated is therefore the offset that is used.

    ``utcoffset`` runs caller-supplied code and may raise anything at all, so everything
    it can do is turned into this module's refusal rather than the caller's surprise.
    """
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    try:
        offset = value.utcoffset()
    except Exception as error:
        raise ValueError(f"{field} could not report a UTC offset: {error}") from error
    if offset is None:
        raise ValueError(f"{field} must be timezone-aware")
    try:
        return value.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError) as error:
        raise ValueError(f"{field} cannot be converted to UTC: {error}") from error
