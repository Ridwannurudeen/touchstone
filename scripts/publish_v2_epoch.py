"""Preflight or publish one signed policy report to Touchstone RegistryV2."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import (  # noqa: E402
    DeploymentError,
    RegistryV2DeploymentManifest,
)
from touchstone.keyring import (  # noqa: E402
    IdentityError,
    PublisherKey,
    assert_role_separation,
)
from touchstone.locking import LockUnavailable, exclusive_lock  # noqa: E402
from touchstone.publish_v2 import (  # noqa: E402
    RegistryV2Backend,
    RegistryV2PublicationError,
    RelayerKey,
)
from touchstone.publish_v2_journal import RegistryV2PublisherClient  # noqa: E402
from touchstone.rpc_quorum import QuorumError, QuorumRPC  # noqa: E402
from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


RELAYER_KEY_ENV = "TOUCHSTONE_RELAYER_PRIVATE_KEY"


def run(arguments: argparse.Namespace) -> dict[str, object]:
    manifest = RegistryV2DeploymentManifest.load(arguments.manifest)
    assert_role_separation()
    publisher = PublisherKey.from_env(manifest)
    relayer_secret = os.environ.get(RELAYER_KEY_ENV)
    if relayer_secret is None:
        raise RegistryV2PublicationError(f"{RELAYER_KEY_ENV} is not set")
    relayer = RelayerKey.from_hex(relayer_secret)
    quorum = QuorumRPC.from_env()
    backend = RegistryV2Backend(
        manifest,
        publisher,
        relayer_key=relayer,
        quorum=quorum,
    )
    preflight = backend.preflight()
    result: dict[str, object] = {
        "network": manifest.network,
        "chain_id": preflight.chain_id,
        "registry": preflight.registry_address,
        "legacy_registry": preflight.legacy_registry_address,
        "owner": preflight.owner_address,
        "publisher": preflight.publisher_address,
        "publisher_identity": preflight.publisher_identity_address,
        "relayer": preflight.relayer_address,
        "relayer_balance_wei": preflight.relayer_balance_wei,
        "block_number": preflight.block_number,
        "quorum": quorum is not None,
    }
    if arguments.preflight:
        result["published"] = False
        return result
    signed_report = strict_json_loads(Path(arguments.signed_report).read_bytes())
    if not isinstance(signed_report, dict):
        raise RegistryV2PublicationError("signed report must be an object")
    workspace = Workspace(arguments.workspace)
    client = RegistryV2PublisherClient(
        backend, workspace.registry_v2_pending_journal
    )
    publish = client.publish_correction if arguments.correction else client.publish
    with exclusive_lock(workspace.lock):
        publication = publish(signed_report, report_uri=arguments.report_uri)
    result.update(
        {
            "published": True,
            "transaction_hash": publication.transaction_hash,
            "reconciled": publication.reconciled,
            "receipt": publication.receipt,
            "sequence": publication.report.sequence,
            "report_digest": publication.report.report_digest,
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--signed-report")
    parser.add_argument("--report-uri")
    parser.add_argument("--workspace")
    parser.add_argument("--correction", action="store_true")
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
                + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            )
    try:
        result = run(arguments)
    except (
        DeploymentError,
        IdentityError,
        LockUnavailable,
        QuorumError,
        RegistryV2PublicationError,
        OSError,
        ValueError,
    ) as error:
        print(f"PUBLISH V2 FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
