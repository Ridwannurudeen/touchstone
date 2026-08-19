"""Fail when canonical project state contradicts deployment or public copy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

STATE_VERSION = "touchstone.project-state.v1"
STALE_PHRASES = ("the dossier was unbuilt", "the gate never deployed")


class PublicTruthError(RuntimeError):
    """The public record disagrees with canonical project state."""


def assert_state(state: dict[str, object], public_paths: tuple[Path, ...]) -> None:
    if state.get("version") != STATE_VERSION:
        raise PublicTruthError("unsupported project-state version")
    deployments = state.get("deployments")
    if not isinstance(deployments, list):
        raise PublicTruthError("project state has no deployment list")
    for deployment in deployments:
        if not isinstance(deployment, dict):
            raise PublicTruthError("deployment records must be objects")
        address = deployment.get("registry_address")
        if isinstance(address, str) and address.lower().startswith("0x") and set(
            address[2:]
        ) == {"0"}:
            raise PublicTruthError(
                f"{deployment.get('source')} describes a zero address as deployed"
            )
        if address and address != "not_deployed" and deployment.get("deployment_state") not in {
            "active",
            "superseded",
        }:
            raise PublicTruthError(
                f"{deployment.get('source')} has an address but no deployed state"
            )
        if not isinstance(deployment.get("network"), str) or type(
            deployment.get("chain_id")
        ) is not int:
            raise PublicTruthError(
                f"{deployment.get('source')} names a network without a chain id"
            )
    reports = state.get("reports")
    bundles = state.get("bundles")
    if not isinstance(reports, dict) or not isinstance(bundles, list):
        raise PublicTruthError("project state has incomplete report facts")
    if reports.get("artifact_count") != len(bundles):
        raise PublicTruthError("report count disagrees with bundled artifacts")
    approval = state.get("approval")
    if not isinstance(approval, dict) or approval.get("approved_count") != len(
        approval.get("approved_control_ids", [])
    ):
        raise PublicTruthError("approved control count disagrees with the ledger")
    for path in public_paths:
        if path.is_dir():
            files = sorted(path.rglob("*.html"))
        else:
            files = [path]
        for public_file in files:
            text = public_file.read_text(encoding="utf-8").lower()
            for phrase in STALE_PHRASES:
                if phrase in text:
                    raise PublicTruthError(
                        f"stale public phrase {phrase!r} remains in {public_file}"
                    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("paths", nargs="*", type=Path)
    arguments = parser.parse_args(argv)
    paths = tuple(
        (arguments.root / path if not path.is_absolute() else path)
        for path in (arguments.paths or (Path("README.md"), Path("site2")))
    )
    try:
        state = json.loads(arguments.state.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise PublicTruthError("project state must be an object")
        assert_state(state, paths)
    except (OSError, json.JSONDecodeError, PublicTruthError) as error:
        print(f"PUBLIC TRUTH FAIL: {error}", file=sys.stderr)
        return 1
    print("public truth is consistent with project state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
