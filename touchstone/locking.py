"""An exclusive lock the operating system releases when the holder dies.

A sentinel file created with ``O_EXCL`` and removed in a ``finally`` looks like a lock and
is not one: kill the process, cut the power, and the file remains, locking out every
future run until somebody deletes it by hand. Recovering from a crash is exactly when a
service can least afford to need a human first.

So the lock is an OS-level lock on an open descriptor — ``fcntl.flock`` where it exists,
``msvcrt.locking`` on Windows. The kernel drops it when the process exits, however it
exits, so a crash leaves the workspace usable and a live second daemon is still refused.

The descriptor is opened on the protected file itself, which makes the lock's identity the
file's inode rather than the spelling of its path.

This protects one workspace from two *processes*. It is not a distributed lock and makes no
claim about a shared filesystem where the underlying primitives are unreliable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path

try:  # POSIX
    import fcntl

    _WINDOWS = False
except ImportError:  # Windows
    import msvcrt

    _WINDOWS = True


# The lock is taken on a byte range far past any content a durable file of this project
# will ever hold, so the protected file can be locked *directly* rather than through a
# sidecar named after its path. A path is not an identity: a symlink, a hardlink, and an
# absolute and relative spelling of one file are four names for one inode, and four
# path-derived sidecars are four locks that all succeed at once. The inode is the identity
# the kernel cannot be fooled about.
#
# Verified on Windows: locking here neither extends the file nor blocks appends through a
# separate handle, and a hardlink alias is refused. On POSIX ``flock`` is per-inode by
# construction and ignores the offset entirely.
_LOCK_OFFSET = 1 << 40


class LockUnavailable(RuntimeError):
    """Another live process holds this lock."""


@dataclass(slots=True)
class Held:
    """Evidence that this process holds one specific lock, right now.

    Handed out only by :func:`exclusive_lock`, after the kernel has granted the lock, and
    invalidated when the block exits. That is what separates it from a value a caller can
    simply construct: a function requiring one cannot be satisfied by intent alone, and a
    caller holding a stale one is refused rather than believed.

    It cannot defend against code that deliberately forges internals — nothing in a Python
    process can — but it makes the ordinary mistake, calling a lock-requiring operation
    without the lock, impossible rather than merely discouraged.
    """

    path: Path
    _active: bool = True

    @property
    def active(self) -> bool:
        return self._active

    def verify(self, expected: str | os.PathLike[str]) -> None:
        """Refuse unless this is a live hold on exactly the lock named."""
        if not self._active:
            raise LockUnavailable(
                f"the hold on {self.path.name} has been released; it proves nothing now"
            )
        if self.path != Path(expected).resolve():
            raise LockUnavailable(
                f"this holds {self.path}, not {Path(expected).resolve()}"
            )


@contextmanager
def exclusive_lock(path: str | os.PathLike[str]) -> Iterator[Held]:
    """Hold an exclusive lock on ``path`` for the duration of the block.

    Raises :class:`LockUnavailable` immediately rather than waiting: a second daemon on
    one workspace is a configuration mistake, and blocking would hide it as a hang.

    Yields a :class:`Held` so an operation that requires the lock can be given proof of it
    rather than a comment asking for it.
    """
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(location, os.O_CREAT | os.O_RDWR)
    try:
        _acquire(descriptor, location)
    except LockUnavailable:
        os.close(descriptor)
        raise
    held = Held(path=location.resolve())
    try:
        yield held
    finally:
        # Invalidated before the descriptor closes, so a reference kept past the block
        # cannot be used in the window where the lock is gone but the object looks fine.
        held._active = False
        try:
            _release(descriptor)
        finally:
            os.close(descriptor)


def _acquire(descriptor: int, location: Path) -> None:
    try:
        if _WINDOWS:
            os.lseek(descriptor, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise LockUnavailable(f"another live process holds {location.name}") from error


def _release(descriptor: int) -> None:
    try:
        if _WINDOWS:
            os.lseek(descriptor, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        # The descriptor is about to be closed, which releases the lock regardless.
        pass
