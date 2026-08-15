"""Verify an archive into a fresh directory. Activation is a separate, deliberate act.

This never writes into a live workspace and never signs or broadcasts anything. It decrypts,
recomputes every digest from the bytes it is about to write, proves the restored chains
verify, and stops. Moving the result into place is an operator decision, and the service's
normal startup reconciliation then handles any pending publication it finds.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.backup import BackupError, backup_key, restore  # noqa: E402
from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.incidents import IncidentLog, IncidentLogError  # noqa: E402
from touchstone.translog import TransparencyLog, TransparencyLogError  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--asset-key", required=True)
    parser.add_argument("--into", required=True, help="a directory that must not exist")
    arguments = parser.parse_args(argv)

    try:
        manifest = DeploymentManifest.load(arguments.manifest)
        key = backup_key()
        archive = Path(arguments.archive).read_bytes()
    except (BackupError, DeploymentError, OSError, ValueError) as error:
        print(f"RESTORE FAIL: {error}", file=sys.stderr)
        return 1

    try:
        restored = restore(
            archive,
            arguments.into,
            key=key,
            asset_key=arguments.asset_key,
            registry_address=manifest.registry_address,
        )
    except BackupError as error:
        print(f"RESTORE FAIL: {error}", file=sys.stderr)
        return 1

    # Digests prove the bytes survived the round trip. They do not prove the chains those
    # bytes form still verify, and that is the claim a restore actually has to make.
    workspace = Workspace(arguments.into)
    try:
        entries = TransparencyLog(workspace.transparency_log).verify()
        incidents = IncidentLog(workspace.incidents).verify()
        evidence = EvidenceStore(workspace.evidence).verified_entries()
    except (IncidentLogError, TransparencyLogError, ValueError) as error:
        print(
            f"RESTORE FAIL: the archive restored but does not verify: {error}",
            file=sys.stderr,
        )
        return 1

    print(f"restored {len(restored)} files into {workspace.root}")
    print(f"transparency log: {len(entries)} entries verify")
    print(f"incident log: {len(incidents)} entries verify")
    print(f"evidence: {len(evidence)} captures verify")
    print("NOT ACTIVATED — moving this into place is a separate operator decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
