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
