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


# Descriptors this process currently holds a lock on. The set is the authority for whether
# a hold is live, rather than a flag on the object: a flag can be set back to True by anyone
# holding the object, and a set entry can only be created by an acquisition and removed by
# the release that follows it.
_LIVE: set[int] = set()


@dataclass(frozen=True, slots=True)
class Held:
    """Proof that this process holds one specific lock, right now.

    The first version of this carried a path and a boolean, and was therefore exactly as
    forgeable as the comment it replaced: `Held(path=the_expected_lock)` satisfied every
    check and produced a real archive with no kernel lock ever taken. Nothing about naming
    a file proves you locked it.

    So the proof is the descriptor. It is registered when the kernel grants the lock and
    unregistered when the block exits, and :meth:`verify` asks the operating system whether
    that descriptor still refers to the very file being claimed — an inode comparison, not
    a path comparison, so a second name for one file cannot be passed off as a different
    lock and a stale descriptor number cannot be reused.

    A determined caller can still reach into module internals; nothing in a Python process
    prevents that. What this makes impossible is the *ordinary* mistake, which is the one
    that actually happens: calling a lock-requiring operation without the lock.
    """

    path: Path
    descriptor: int

    @property
    def active(self) -> bool:
        return self.descriptor in _LIVE

    def verify(self, expected: str | os.PathLike[str]) -> None:
        """Refuse unless this is a live hold, by this process, on exactly that file."""
        target = Path(expected).resolve()
        if not self.active:
            raise LockUnavailable(
                f"the hold on {self.path.name} has been released; it proves nothing now"
            )
        if self.path != target:
            raise LockUnavailable(f"this holds {self.path}, not {target}")
        try:
            held = os.fstat(self.descriptor)
            named = os.stat(target)
        except OSError as error:
            raise LockUnavailable(
                f"the hold on {target} cannot be confirmed: {error}"
            ) from error
        # The inode, not the name. This is what a fabricated descriptor number cannot
        # satisfy: it would have to already be an open handle on this exact file.
        if (held.st_ino, held.st_dev) != (named.st_ino, named.st_dev):
            raise LockUnavailable(f"the descriptor offered does not refer to {target}")


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
    _LIVE.add(descriptor)
    try:
        yield Held(path=location.resolve(), descriptor=descriptor)
    finally:
        # Unregistered before the descriptor closes, so a reference kept past the block
        # cannot be used in the window where the lock is gone but the object looks fine.
        # Discarded rather than removed, because a failure earlier in this block must not
        # be masked by a KeyError raised while cleaning up after it.
        _LIVE.discard(descriptor)
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
