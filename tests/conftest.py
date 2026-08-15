"""Fixtures shared across test modules."""

from datetime import datetime, tzinfo

import pytest


class _NoOffset(tzinfo):
    """A ``tzinfo`` that declines to say what its offset is.

    ``datetime`` permits this, and the result is a value that answers ``tzinfo is not
    None`` while being semantically naive: ``astimezone`` falls back to the host's local
    zone, so the same input becomes a different instant on a different machine. Checking
    ``tzinfo`` alone therefore does not establish awareness — ``utcoffset()`` does.
    """

    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> str:
        return "NoOffset"


@pytest.fixture
def offsetless_instant() -> datetime:
    """A datetime that looks aware and is not."""
    return datetime(2026, 8, 15, 12, 0, tzinfo=_NoOffset())
