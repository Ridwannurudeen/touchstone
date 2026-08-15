"""Hold a workspace lock in a separate process until killed.

The single-process version of this test took the lock and then tried to take it again,
which proves the lock is non-reentrant and nothing about two processes. The invariant under
test — that a second process cannot copy a live workspace — is a statement about processes,
so it needs one.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.locking import exclusive_lock  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


def main() -> int:
    workspace = Workspace(sys.argv[1])
    with exclusive_lock(workspace.lock):
        # Announce only after the lock is actually held, so the parent never races the
        # acquisition and mistake a not-yet-locked workspace for a refusal it did not get.
        print("HELD", flush=True)
        while True:
            time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
