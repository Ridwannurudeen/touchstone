"""Publish one already-signed observation report to the registry a manifest names.

This is the operational entry point. It never signs a report — the reporting key is a
separate identity that is not required to be present here — so its only job is to place a
report that already exists onto the chain the manifest describes.

``--preflight`` runs every chain check and stops before signing anything, which is how a
new deployment is proved reachable and correctly authorized without sending a transaction.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402
from touchstone.keyring import (  # noqa: E402
    IdentityError,
    PublisherKey,
    assert_role_separation,
)
from touchstone.locking import LockUnavailable, exclusive_lock  # noqa: E402
from touchstone.publish import (  # noqa: E402
    PublicationError,
    PublisherClient,
    SignedRegistryBackend,
)
from touchstone.rpc_quorum import QuorumRPC  # noqa: E402
from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


def run(arguments: argparse.Namespace) -> dict[str, object]:
    """Preflight, and unless asked to stop there, publish."""
    manifest = DeploymentManifest.load(arguments.manifest)
    assert_role_separation()
    backend = SignedRegistryBackend(
        manifest,
        PublisherKey.from_env(manifest),
        quorum=QuorumRPC.from_env(),
    )
    preflight = backend.preflight()
    result: dict[str, object] = {
        "network": manifest.network,
        "chain_id": preflight.chain_id,
        "registry": preflight.registry_address,
        "registry_runtime_bytecode_sha256": (
            preflight.registry_runtime_bytecode_sha256
        ),
        "publisher": preflight.publisher_address,
        "publisher_authorized": preflight.publisher_authorized,
        "publisher_identity": preflight.publisher_identity,
        "active_reporting_kid": manifest.active_key.kid,
        "publisher_balance_wei": preflight.publisher_balance_wei,
        "block_number": preflight.block_number,
    }
    if arguments.preflight:
        result["published"] = False
        return result

    signed_reports = _batch_values(arguments.signed_report)
    report_uris = _batch_values(arguments.report_uri)
    workspaces = _batch_values(arguments.workspace)
    if not (len(signed_reports) == len(report_uris) == len(workspaces)):
        raise PublicationError(
            "--signed-report, --report-uri and --workspace must be supplied the same "
            "number of times"
        )
    roots = [Workspace(path) for path in workspaces]
    if len({workspace.root for workspace in roots}) != len(roots):
        raise PublicationError("each report must use a distinct workspace")

    publications: list[dict[str, object]] = []
    for signed_path, uri, workspace in zip(signed_reports, report_uris, roots):
        signed_report = strict_json_loads(Path(signed_path).read_bytes())
        if not isinstance(signed_report, dict):
            raise PublicationError("the signed report must be an object")
        client = PublisherClient(
            backend, TransparencyLog(workspace.transparency_log), workspace.pending_journal
        )
        # The active-key rule lives in PublisherClient, not here. It used to live here,
        # which meant anything calling the client directly bypassed it entirely.
        publish = client.publish_correction if arguments.correction else client.publish
        with exclusive_lock(workspace.lock):
            publication = publish(signed_report, report_uri=uri)
        publications.append(
            {
                "workspace": str(workspace.root),
                "reporting_kid": signed_report.get("kid"),
                "transaction_hash": publication.transaction_hash,
                "reconciled": publication.reconciled,
                "log_entry_hash": publication.log_entry_hash,
                "receipt": publication.receipt,
            }
        )
    result["published"] = True
    if len(publications) == 1:
        result.update(publications[0])
    else:
        result["publications"] = publications
    return result


def _batch_values(value: object) -> list[object]:
    """Treat a legacy scalar CLI value as a one-report batch."""
    if isinstance(value, list):
        return value
    return [value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="deployment manifest path")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="verify the chain against the manifest and stop without signing",
    )
    parser.add_argument(
        "--signed-report",
        action="append",
        help="signed observation report envelope; repeat for policy reports",
    )
    parser.add_argument(
        "--report-uri",
        action="append",
        help="URI recorded onchain for this report; repeat in report order",
    )
    parser.add_argument(
        "--workspace",
        action="append",
        help="directory holding this asset's transparency log, journal and lock",
    )
    parser.add_argument(
        "--correction",
        action="store_true",
        help="publish through the registry's correction entry point",
    )
    arguments = parser.parse_args(argv)
    if not arguments.preflight:
        missing = [
            name
            for name in ("signed_report", "report_uri", "workspace")
            if getattr(arguments, name) is None
        ]
        if missing:
            parser.error(
                "publishing requires "
                + ", ".join(f"--{n.replace('_', '-')}" for n in missing)
            )
    try:
        result = run(arguments)
    except (
        DeploymentError,
        IdentityError,
        LockUnavailable,
        PublicationError,
        OSError,
        ValueError,
    ) as error:
        print(f"PUBLISH FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
