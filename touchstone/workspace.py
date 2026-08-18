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
        # Anchored to one absolute location at construction. A relative root is not a
        # location — it is a location *plus* the process's current directory, which is
        # not part of the workspace and can change under it. The same stored
        # `asset/service.lock` then names two different files from two working
        # directories, which is exactly the divergence a single identity exists to
        # prevent. Resolving also collapses symlinks and `..`, so two spellings of one
        # directory are one workspace.
        location = Path(root).resolve()
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
    def observation_log(self) -> Path:
        """The watcher's append-only record of what it saw.

        Workspace state, not scratch: it is the only place the transition history lives, and
        a restore without it makes the next pass a first observation while the evidence store
        still holds the captures those observations describe.
        """
        return self.root / "observations.jsonl"

    @property
    def observer_lock(self) -> Path:
        """The watcher's own lock, held for its whole run.

        Deliberately not `lock`: the daily service holds that one for its entire lifetime,
        so a watcher that waited on it could never run at all. It lives here rather than in
        the watcher because a second definition of this path is a second answer to "is
        anything writing to this workspace", and backup asks exactly that question.
        """
        return self.root / "observer.lock"

    @property
    def operations(self) -> Path:
        return self.root / "operations"

    @property
    def incidents(self) -> Path:
        return self.root / "incidents.jsonl"

    @property
    def evidence(self) -> Path:
        """Where retained artifacts live.

        Derived here rather than rooted independently, because evidence is the one thing in
        this project that cannot be recreated: a report can be rebuilt from its inputs, and
        the inputs cannot be rebuilt from anything. An adapter free to store it outside the
        workspace is an adapter free to store it outside the backup.
        """
        return self.root / "evidence"

    @property
    def bundles(self) -> Path:
        """Where each published report's offline verification bundle is written.

        Inside the workspace, so a backup that captures operational state captures the
        bundles too. They are recreatable in principle — but only from the evidence store
        *and* the approval ledger as it stood when the report was signed, and the ledger
        moves whenever a control is approved. Recreating a bundle for last month's report
        after a recompilation is therefore not a rebuild, it is an archaeology exercise. So
        the bundle is kept, not reconstructed.
        """
        return self.root / "bundles"

    @property
    def heartbeat(self) -> Path:
        """What the daemon writes to say it is alive, and when that stops being true.

        Deliberately not inside `operations`: that directory is durable state the service
        reasons about, while this is a liveness artifact that is expected to go stale and
        is never restored from a backup.
        """
        return self.root / "heartbeat.json"

    @property
    def lock(self) -> Path:
        """The single lock every writer to this workspace takes.

        One lock, not one per file: the operations that matter span several of these files
        at once — check the journal, publish, append to the log, save the state — and a
        per-file lock lets two processes interleave across that sequence while each holds
        every individual lock it touches.
        """
        return self.root / "service.lock"
