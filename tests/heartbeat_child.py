"""A child that writes heartbeats until it is killed.

Run as a subprocess by ``test_watchdog_recovery.py``. A detection test that never leaves
the parent process proves nothing: the property under test is that *no code is running* to
refresh the file, and in-process state Python happens to keep alive cannot demonstrate that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.heartbeat import build_record, write  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


def main() -> int:
    workspace = Workspace(sys.argv[1])
    asset_key = sys.argv[2]
    registry_address = sys.argv[3]
    expiry_seconds = float(sys.argv[4])
    interval_seconds = float(sys.argv[5])
    # A compressed clock, so the test proves the shape of the guarantee in seconds rather
    # than the production three minutes. The expiry is passed in for the same reason.
    sequence = 1
    while True:
        write(
            workspace.heartbeat,
            build_record(
                asset_key=asset_key,
                registry_address=registry_address,
                sequence=sequence,
                now=datetime.now(timezone.utc),
                expiry_seconds=expiry_seconds,
                last_attempted_slot=(datetime.now(timezone.utc) - timedelta(minutes=1))
                .isoformat()
                .replace("+00:00", "Z"),
            ),
        )
        sequence += 1
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
