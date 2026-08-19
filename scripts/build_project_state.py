"""Build the canonical, machine-readable facts used by public renderers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from touchstone.approval import load_approval_ledger  # noqa: E402
from touchstone.assets import USTB  # noqa: E402
from touchstone.deployment import NETWORK_CHAIN_IDS  # noqa: E402
from touchstone.evaluate import default_controls  # noqa: E402
from touchstone.policy import load_all  # noqa: E402
from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402
from touchstone.verify import VerificationError, verify_bundle  # noqa: E402


STATE_VERSION = "touchstone.project-state.v1"


class StateError(RuntimeError):
    """The repository facts cannot be assembled into one coherent state."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_state(
    root: Path,
    *,
    chain_snapshot: Path | None = None,
    workspaces: tuple[Path, ...] = (),
) -> dict[str, object]:
    root = root.resolve()
    ledger = load_approval_ledger(root / "data" / "compilations" / "APPROVALS.json")
    approved = default_controls(USTB, ledger)
    policies = load_all(
        root / "data" / "policies",
        approved=approved,
    )
    deployments = _deployments(root / "deployments")
    bundles = _bundles(root / "site2" / "data", root=root)
    policy_bundles = [
        bundle for bundle in bundles if isinstance(bundle.get("policy"), dict)
    ]
    logs = _logs(workspaces, root=root)
    chain = _chain_snapshot(chain_snapshot)
    try:
        from build_docs import PUBLISHED
    except ImportError as error:
        raise StateError(f"documentation renderer cannot be loaded: {error}") from error

    return {
        "version": STATE_VERSION,
        "approval": {
            "ledger_sha256": sha256_file(root / "data" / "compilations" / "APPROVALS.json"),
            "approved_control_ids": sorted(
                entry["control_id"] for entry in ledger["approved"]
            ),
            "approved_count": len(ledger["approved"]),
            "declined_count": len(ledger["declined"]),
        },
        "bundles": bundles,
        "chain": chain,
        "deployments": deployments,
        "documentation": {"published_count": len(PUBLISHED)},
        "policies": [
            {
                "asset_key": policy.asset_key,
                "control_ids": list(policy.control_ids),
                "digest": policy.digest,
                "policy_id": policy.policy_id,
                "policy_version": policy.version,
                "title": policy.title,
            }
            for policy in policies
        ],
        "reports": {
            "artifact_count": len(bundles),
            "confirmed_policy_bundle_count": sum(
                bundle["state"] == "CONFIRMED" for bundle in policy_bundles
            ),
            "latest_state": bundles[-1]["state"] if bundles else None,
            "retained_verified_policy_bundle_count": len(policy_bundles),
            "states": sorted({bundle["state"] for bundle in bundles}),
        },
        "transparency_logs": logs,
    }


def encode_state(state: dict[str, object]) -> bytes:
    return (json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--chain-snapshot",
        type=Path,
        help="optional read-only chain fact snapshot exported by an operator",
    )
    parser.add_argument(
        "--workspace",
        action="append",
        type=Path,
        default=[],
        help="optional workspace whose verified transparency log is included",
    )
    arguments = parser.parse_args(argv)
    try:
        state = build_state(
            arguments.root,
            chain_snapshot=arguments.chain_snapshot,
            workspaces=tuple(arguments.workspace),
        )
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = arguments.out.with_name(arguments.out.name + ".tmp")
        temporary.write_bytes(encode_state(state))
        temporary.replace(arguments.out)
    except (OSError, StateError, ValueError, VerificationError) as error:
        print(f"PROJECT STATE FAIL: {error}", file=sys.stderr)
        return 1
    print(f"wrote {arguments.out}")
    return 0


def _deployments(directory: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(directory.glob("*.json")):
        name = path.name.lower()
        if name == "manifest.schema.json" or ".template." in name or ".attempt." in name:
            continue
        try:
            document = strict_json_loads(path.read_bytes())
        except (OSError, TypeError, ValueError) as error:
            raise StateError(f"{path.name} is not strict JSON: {error}") from error
        if not isinstance(document, dict):
            raise StateError(f"{path.name} must be a JSON object")
        network = document.get("network")
        chain_id = document.get("chain_id")
        if network not in NETWORK_CHAIN_IDS:
            raise StateError(f"{path.name} names unknown network {network!r}")
        if type(chain_id) is not int or chain_id != NETWORK_CHAIN_IDS[network]:
            raise StateError(
                f"{path.name} binds {network!r} to invalid chain id {chain_id!r}"
            )
        records.append(
            {
                "chain_id": chain_id,
                "deployment_state": document.get("deployment_state"),
                "network": network,
                "registry_address": document.get("registry_address"),
                "source": path.relative_to(directory.parent).as_posix(),
            }
        )
    return records


def _bundles(directory: Path, *, root: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "stats.json":
            continue
        try:
            raw = path.read_bytes()
            parsed = strict_json_loads(raw)
            report = verify_bundle(raw)
        except (OSError, TypeError, ValueError, VerificationError) as error:
            raise StateError(f"bundle {path.name} does not verify: {error}") from error
        if not isinstance(parsed, dict):
            raise StateError(f"bundle {path.name} must be an object")
        controls = parsed.get("control_records")
        if not isinstance(controls, list):
            raise StateError(f"bundle {path.name} has no control_records array")
        records.append(
            {
                "asset_key": report["asset_key"],
                "control_count": len(controls),
                "evidence_root": report["evidence_root"],
                "path": path.relative_to(root).as_posix(),
                "policy": report.get("policy"),
                "sequence": report["sequence"],
                "sha256": sha256_file(path),
                "state": report["state"],
            }
        )
    return records


def _chain_snapshot(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"available": False, "source": None}
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as error:
        raise StateError(f"chain snapshot is not strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise StateError("chain snapshot must be an object")
    networks = value.get("networks", [])
    if not isinstance(networks, list):
        raise StateError("chain snapshot networks must be an array")
    for network in networks:
        if not isinstance(network, dict):
            raise StateError("chain snapshot network entries must be objects")
        name, chain_id = network.get("network"), network.get("chain_id")
        if name not in NETWORK_CHAIN_IDS or chain_id != NETWORK_CHAIN_IDS[name]:
            raise StateError(f"chain snapshot has an invalid network binding: {network}")
    return {"available": True, "source": path.name, "networks": networks}


def _logs(paths: tuple[Path, ...], *, root: Path) -> list[dict[str, object]]:
    records = []
    for workspace in paths:
        log = workspace / "transparency.jsonl"
        try:
            entries = TransparencyLog(log).verify()
        except (OSError, ValueError, RuntimeError) as error:
            raise StateError(f"transparency log {log} does not verify: {error}") from error
        records.append(
            {
                "entries": len(entries),
                "path": log.relative_to(root).as_posix()
                if log.is_relative_to(root)
                else log.name,
            }
        )
    return records


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
