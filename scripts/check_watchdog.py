"""Ask, from outside, whether the service is healthy. Exit nonzero if it is not.

Read-only by construction: this takes no lock and writes nothing into the workspace it
inspects. A supervisor runs it on a timer and acts on the exit status.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402
from touchstone.heartbeat import HeartbeatError  # noqa: E402
from touchstone.watchdog import inspect, render  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--asset-key", required=True)
    parser.add_argument(
        "--previous-sequence",
        type=int,
        default=None,
        help="the last heartbeat sequence this supervisor saw; refuses one that has not "
        "advanced, which is what a restored backup or a second daemon looks like",
    )
    parser.add_argument(
        "--due-slot",
        default=None,
        help="the UTC instant of the slot that should have run by now, e.g. "
        "2026-08-15T09:00:00Z. Epoch health is judged against this exact slot: an epoch "
        "older than it does not satisfy it. Omit when no slot is yet due.",
    )
    arguments = parser.parse_args(argv)

    try:
        manifest = DeploymentManifest.load(arguments.manifest)
    except DeploymentError as error:
        print(f"WATCHDOG FAIL: {error}", file=sys.stderr)
        return 1

    due_slot = None
    if arguments.due_slot is not None:
        try:
            due_slot = datetime.fromisoformat(
                arguments.due_slot.replace("Z", "+00:00")
            )
        except ValueError:
            print("WATCHDOG FAIL: --due-slot must be an ISO instant", file=sys.stderr)
            return 1

    try:
        report = inspect(
            arguments.workspace,
            now=datetime.now(timezone.utc),
            asset_key=arguments.asset_key,
            registry_address=manifest.registry_address,
            previous_sequence=arguments.previous_sequence,
            due_slot=due_slot,
        )
    except (HeartbeatError, OSError, ValueError) as error:
        # Indeterminate is unhealthy. A watchdog that cannot decide must not be silent,
        # because silence is how a healthy result is reported.
        print(f"WATCHDOG FAIL: {error}", file=sys.stderr)
        return 1

    print(render(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
