"""The one identity every durable path is derived from.

The service took a ``--workspace`` directory and derived its transparency log, pending
journal, operations store, incident log and lock from it. The publishing CLI took the log
and the journal as separate arguments and derived its lock from the journal. Both were
internally consistent, so both accepted a configuration where they shared the transparency
log and the registry but took *different* locks — and then verified the same log head
concurrently and appended entries claiming the same predecessor.

Nothing checks that a set of independently supplied paths belongs together, because there
is nothing to check it against. So the paths stop being independent: one directory is named,
every durable path is derived from it, and a mismatched layout is not something the
operator can express.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Workspace:
    """A directory holding one asset's durable operational state."""

    root: Path

    def __init__(self, root: str | os.PathLike[str]) -> None:
        location = Path(root)
        if location.exists() and not location.is_dir():
            raise ValueError(f"workspace must be a directory: {location}")
        object.__setattr__(self, "root", location)

    @property
    def transparency_log(self) -> Path:
        return self.root / "transparency.jsonl"

    @property
    def pending_journal(self) -> Path:
        return self.root / "pending.json"

    @property
    def operations(self) -> Path:
        return self.root / "operations"

    @property
    def incidents(self) -> Path:
        return self.root / "incidents.jsonl"

    @property
    def lock(self) -> Path:
        """The single lock every writer to this workspace takes.

        One lock, not one per file: the operations that matter span several of these files
        at once — check the journal, publish, append to the log, save the state — and a
        per-file lock lets two processes interleave across that sequence while each holds
        every individual lock it touches.
        """
        return self.root / "service.lock"
