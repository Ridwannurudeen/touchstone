"""Take an encrypted archive of a workspace no daemon is serving.

Offline only. It acquires the same lock the daemon holds for its serving lifetime, so if a
service is running this refuses rather than copying underneath it. An online backup is the
daemon's own job, taken between mutations while it already holds that lock.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.backup import BackupError, backup_key, take_offline  # noqa: E402
from touchstone.deployment import DeploymentError, DeploymentManifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--asset-key", required=True)
    parser.add_argument(
        "--out", required=True, help="archive destination; must not exist"
    )
    arguments = parser.parse_args(argv)

    destination = Path(arguments.out)
    if destination.exists():
        print(f"BACKUP FAIL: {destination} already exists", file=sys.stderr)
        return 1

    try:
        manifest = DeploymentManifest.load(arguments.manifest)
        key = backup_key()
        archive = take_offline(
            arguments.workspace,
            now=datetime.now(timezone.utc),
            key=key,
            asset_key=arguments.asset_key,
            registry_address=manifest.registry_address,
        )
    except (BackupError, DeploymentError, ValueError) as error:
        print(f"BACKUP FAIL: {error}", file=sys.stderr)
        return 1

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(archive)
        temporary.replace(destination)
    except OSError as error:
        print(f"BACKUP FAIL: the archive cannot be written: {error}", file=sys.stderr)
        return 1

    print(f"wrote {len(archive)} encrypted bytes to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
