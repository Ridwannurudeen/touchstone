"""Kill a real daemon and prove the watchdog notices, then that a replacement clears it.

The timings under test are shape, not duration. Production uses a 60-second refresh and a
180-second expiry; this compresses both so the suite stays fast, and asserts the property
that matters at any scale: **detection happens because nothing is refreshing the file**,
and recovery happens because something is again.

A test that stayed in one process could not show either. The point is that no code is
running, and only a killed process demonstrates that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time


from touchstone.watchdog import inspect
from touchstone.workspace import Workspace


ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
REGISTRY = "0x" + "ab" * 20
CHILD = Path(__file__).parent / "heartbeat_child.py"

# Compressed from production's 60/180. The ratio is what the contract rests on: an expiry
# of three refreshes means one missed write is a slow disk, not a death sentence.
INTERVAL_SECONDS = 0.2
EXPIRY_SECONDS = 0.6


def spawn(workspace: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(CHILD),
            str(workspace),
            ASSET,
            REGISTRY,
            str(EXPIRY_SECONDS),
            str(INTERVAL_SECONDS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def healthy_now(workspace: Path) -> bool:
    report = inspect(
        workspace,
        now=datetime.now(timezone.utc),
        asset_key=ASSET,
        registry_address=REGISTRY,
    )
    return report.healthy


def wait_until(predicate, *, timeout: float) -> float:
    """Return how long the condition took, or fail having waited the whole budget."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return time.monotonic() - started
        time.sleep(0.05)
    raise AssertionError(f"condition was not met within {timeout}s")


def test_a_killed_daemon_is_detected_and_a_replacement_recovers(tmp_path: Path) -> None:
    workspace = tmp_path / "asset"
    workspace.mkdir()
    Workspace(workspace)

    daemon = spawn(workspace)
    try:
        wait_until(lambda: healthy_now(workspace), timeout=20)
    except AssertionError:
        daemon.kill()
        stdout, stderr = daemon.communicate(timeout=10)
        raise AssertionError(
            f"the daemon never became healthy: {stderr.decode(errors='replace')[-800:]}"
        ) from None

    # SIGKILL, not a graceful stop: no handlers, no flush, nothing unwound. The heartbeat
    # on disk is whatever the last completed write left, which is exactly the production
    # case this has to survive.
    daemon.kill()
    daemon.wait(timeout=10)

    detection = wait_until(lambda: not healthy_now(workspace), timeout=20)
    assert detection < EXPIRY_SECONDS * 5, (
        f"detection took {detection:.2f}s against a {EXPIRY_SECONDS}s expiry"
    )

    replacement = spawn(workspace)
    try:
        recovery = wait_until(lambda: healthy_now(workspace), timeout=20)
        assert recovery < EXPIRY_SECONDS * 10
    finally:
        replacement.kill()
        replacement.wait(timeout=10)


def test_the_heartbeat_left_by_a_killed_daemon_is_still_well_formed(
    tmp_path: Path,
) -> None:
    """A killed writer must leave a readable stale heartbeat, never a truncated one.

    Atomic replacement is what makes the expiry meaningful: a half-written file would be
    unreadable and would have to be guessed at, where a whole stale one is simply old.
    """
    workspace = tmp_path / "asset"
    workspace.mkdir()

    daemon = spawn(workspace)
    try:
        wait_until(lambda: healthy_now(workspace), timeout=20)
    finally:
        daemon.kill()
        daemon.wait(timeout=10)

    report = inspect(
        workspace,
        now=datetime.now(timezone.utc),
        asset_key=ASSET,
        registry_address=REGISTRY,
    )
    heartbeat = next(check for check in report.checks if check.name == "heartbeat")
    assert report.health is not None
    assert report.health.record is not None, (
        "the record parsed; it is stale, not damaged"
    )
    assert "expired" in heartbeat.detail or heartbeat.healthy
