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
    verification_keys,
)
from touchstone.publish import (  # noqa: E402
    PublicationError,
    PublisherClient,
    SignedRegistryBackend,
)
from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402


def run(arguments: argparse.Namespace) -> dict[str, object]:
    """Preflight, and unless asked to stop there, publish."""
    manifest = DeploymentManifest.load(arguments.manifest)
    assert_role_separation()
    backend = SignedRegistryBackend(manifest, PublisherKey.from_env(manifest))
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

    signed_report = strict_json_loads(Path(arguments.signed_report).read_bytes())
    if not isinstance(signed_report, dict):
        raise PublicationError("the signed report must be an object")
    kid = signed_report.get("kid")
    # A new publication is signed by the active key and nothing else. Superseded keys stay
    # published so that what they already signed keeps verifying, but that is a statement
    # about the past: accepting one here would mean a retired key could still put new
    # reports onchain, which is exactly what rolling over is supposed to end. A date check
    # would not substitute, because a compromised key can backdate its own report.
    if kid != manifest.active_key.kid:
        known = verification_keys(manifest)
        raise PublicationError(
            f"{kid!r} is not this deployment's active reporting key "
            f"({manifest.active_key.kid}); it is "
            + (
                "superseded or revoked"
                if kid in known or manifest.key(kid)
                else "unknown"
            )
            + " and must not sign new publications"
        )
    published_key = verification_keys(manifest)[kid]
    client = PublisherClient(
        backend,
        TransparencyLog(arguments.transparency_log),
        arguments.pending,
    )
    publish = client.publish_correction if arguments.correction else client.publish
    publication = publish(
        signed_report,
        published_key=published_key,
        report_uri=arguments.report_uri,
    )
    result.update(
        {
            "published": True,
            "reporting_kid": kid,
            "transaction_hash": publication.transaction_hash,
            "reconciled": publication.reconciled,
            "log_entry_hash": publication.log_entry_hash,
            "receipt": publication.receipt,
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="deployment manifest path")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="verify the chain against the manifest and stop without signing",
    )
    parser.add_argument("--signed-report", help="signed observation report envelope")
    parser.add_argument("--report-uri", help="URI recorded onchain for this report")
    parser.add_argument("--transparency-log", help="append-only publication log path")
    parser.add_argument("--pending", help="pending-submission journal path")
    parser.add_argument(
        "--correction",
        action="store_true",
        help="publish through the registry's correction entry point",
    )
    arguments = parser.parse_args(argv)
    if not arguments.preflight:
        missing = [
            name
            for name in ("signed_report", "report_uri", "transparency_log", "pending")
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
