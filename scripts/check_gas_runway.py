"""Report how many more publications the publisher can afford, or that it is unknown.

Reads the chain once, at one confirmed block, and divides by the largest cost this
publisher has actually paid. Exits nonzero when funding does not reach the required date,
so a supervisor can gate on it.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402
from touchstone.gas import (  # noqa: E402
    GasError,
    daily_schedule,
    render,
    runway,
    utc_today,
)
from touchstone.translog import TransparencyLog, TransparencyLogError  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--through",
        default="2026-09-03",
        help="the date funding must reach; the operating window requires 2026-09-03",
    )
    arguments = parser.parse_args(argv)

    try:
        required = date.fromisoformat(arguments.through)
    except ValueError:
        print("GAS FAIL: --through must be an ISO date", file=sys.stderr)
        return 1

    try:
        manifest = DeploymentManifest.load(arguments.manifest)
    except DeploymentError as error:
        print(f"GAS FAIL: {error}", file=sys.stderr)
        return 1

    try:
        from web3 import Web3  # noqa: PLC0415 - only needed on the reading path

        web3 = Web3(Web3.HTTPProvider(manifest.rpc_url))
        # One reading of the head, one confirmed block derived from it, and the balance
        # read *at that block*. Reading the balance at "latest" and the block separately
        # gives two numbers from two moments, which is the whole defect class this project
        # keeps finding: an answer describing neither observation.
        head = int(web3.eth.block_number)
        confirmed = max(0, head - manifest.confirmations)
        balance = int(
            web3.eth.get_balance(manifest.publisher_address, block_identifier=confirmed)
        )
    except Exception as error:  # noqa: BLE001 - any endpoint failure is indeterminate
        print(f"GAS FAIL: the endpoint could not be read: {error}", file=sys.stderr)
        return 1

    try:
        entries = TransparencyLog(
            Workspace(arguments.workspace).transparency_log
        ).verify()
    except (TransparencyLogError, ValueError) as error:
        print(f"GAS FAIL: {error}", file=sys.stderr)
        return 1

    today = utc_today(datetime.now(timezone.utc))
    try:
        value = runway(
            balance_wei=balance,
            entries=entries,
            schedule=daily_schedule(today, max(today, required)),
            read_at_block=confirmed,
        )
    except GasError as error:
        print(f"GAS FAIL: {error}", file=sys.stderr)
        return 1

    print(render(value))
    if not value.covers(required):
        # Unknown is not covered. A runway that cannot be established must fail the gate,
        # because the alternative is treating "we could not tell" as "we are fine".
        print(f"GAS GATE FAILED: funding does not reach {required}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
