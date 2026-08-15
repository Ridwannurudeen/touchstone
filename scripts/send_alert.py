"""Send one alert to the configured webhook. Exit nonzero if it did not leave.

Takes codes, never free text: an alert is a signal to go and look, not a transport for
evidence. A failure here is reported to the supervisor journal and never turned into
another webhook call, which would loop precisely when the endpoint is the thing that broke.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.alerts import (  # noqa: E402
    AlertError,
    Event,
    Severity,
    build,
    render,
    send,
    webhook_from_env,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event", required=True, choices=[event.value for event in Event]
    )
    parser.add_argument(
        "--severity", required=True, choices=[level.value for level in Severity]
    )
    parser.add_argument("--asset-key", required=True)
    parser.add_argument("--detail-code", default=None)
    parser.add_argument("--incident-hash", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and print the exact body without sending it",
    )
    arguments = parser.parse_args(argv)

    try:
        body = build(
            event=Event(arguments.event),
            severity=Severity(arguments.severity),
            asset_key=arguments.asset_key,
            observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            detail_code=arguments.detail_code,
            incident_hash=arguments.incident_hash,
        )
    except AlertError as error:
        print(f"ALERT FAIL: {error}", file=sys.stderr)
        return 1

    print(render(body))
    if arguments.dry_run:
        return 0

    try:
        status = send(body, webhook_from_env())
    except AlertError as error:
        # Straight to the journal. Never another webhook call: that loops at exactly the
        # moment the endpoint is what has failed.
        print(f"ALERT FAIL: {error}", file=sys.stderr)
        return 1
    print(f"delivered HTTP {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
