"""An exclusive lock the operating system releases when the holder dies.

A sentinel file created with ``O_EXCL`` and removed in a ``finally`` looks like a lock and
is not one: kill the process, cut the power, and the file remains, locking out every
future run until somebody deletes it by hand. Recovering from a crash is exactly when a
service can least afford to need a human first.

So the lock is an OS-level lock on an open descriptor — ``fcntl.flock`` where it exists,
``msvcrt.locking`` on Windows. The kernel drops it when the process exits, however it
exits, so a crash leaves the workspace usable and a live second daemon is still refused.

This protects one workspace from two *processes*. It is not a distributed lock and makes no
claim about a shared filesystem where the underlying primitives are unreliable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path

try:  # POSIX
    import fcntl

    _WINDOWS = False
except ImportError:  # Windows
    import msvcrt

    _WINDOWS = True


class LockUnavailable(RuntimeError):
    """Another live process holds this lock."""


@contextmanager
def exclusive_lock(path: str | os.PathLike[str]) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` for the duration of the block.

    Raises :class:`LockUnavailable` immediately rather than waiting: a second daemon on
    one workspace is a configuration mistake, and blocking would hide it as a hang.
    """
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(location, os.O_CREAT | os.O_RDWR)
    try:
        _acquire(descriptor, location)
    except LockUnavailable:
        os.close(descriptor)
        raise
    try:
        os.truncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        try:
            _release(descriptor)
        finally:
            os.close(descriptor)


def _acquire(descriptor: int, location: Path) -> None:
    try:
        if _WINDOWS:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise LockUnavailable(f"another live process holds {location.name}") from error


def _release(descriptor: int) -> None:
    try:
        if _WINDOWS:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        # The descriptor is about to be closed, which releases the lock regardless.
        pass
