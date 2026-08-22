"""The restore command itself, which had no test at all.

`test_backup.py` proves the library round-trips and refuses bad archives. It does not prove
the command a person actually runs verifies anything — and the round-3 audit pointed out
that the pending-operation test showed only that the file survived, not that its signature
was checked. These drive `main()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys

import pytest

from touchstone.backup import BACKUP_KEY_ENV
from touchstone.locking import exclusive_lock
from touchstone.operations import OperationsStore
from touchstone.signing import Ed25519Signer, canonical_json_bytes
from touchstone.translog import TransparencyLog
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import restore_workspace  # noqa: E402
from test_publish import _manifest, _signed_report  # noqa: E402


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
KEY = secrets.token_bytes(32)


def written_manifest(tmp_path: Path) -> Path:
    """The committed manifest shape, on disk, so the CLI can load it."""
    manifest = _manifest()
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical_json_bytes(manifest.to_mapping()) + b"\n")
    return path


def workspace_with(tmp_path: Path, *, pending: bool = False) -> Workspace:
    workspace = Workspace(tmp_path / "asset")
    workspace.root.mkdir(parents=True, exist_ok=True)
    TransparencyLog(workspace.transparency_log).append(
        _signed_report(1),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    if pending:
        OperationsStore(workspace.operations, now=lambda: AT).begin_operation(
            _signed_report(2),
            report_uri="urn:touchstone:report:2",
            correction_of=None,
            scheduled_for=AT,
        )
    return workspace


def archived(workspace: Workspace, manifest_path: Path, tmp_path: Path) -> Path:
    from touchstone.backup import create
    from touchstone.deployment import DeploymentManifest

    manifest = DeploymentManifest.load(str(manifest_path))
    with (
        exclusive_lock(workspace.lock) as held,
        exclusive_lock(workspace.evidence_lock) as evidence_held,
    ):
        archive = create(
            held,
            workspace.root,
            evidence_held=evidence_held,
            now=AT,
            key=KEY,
            asset_key=ASSET,
            registry_address=manifest.registry_address,
        )
    path = tmp_path / "archive.bin"
    path.write_bytes(archive)
    return path


def run(tmp_path: Path, manifest_path: Path, archive: Path, into: str) -> int:
    return restore_workspace.main(
        [
            "--manifest",
            str(manifest_path),
            "--archive",
            str(archive),
            "--asset-key",
            ASSET,
            "--into",
            str(tmp_path / into),
        ]
    )


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BACKUP_KEY_ENV, KEY.hex())


def test_the_command_restores_and_reports_what_it_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = written_manifest(tmp_path)
    workspace = workspace_with(tmp_path)

    code = run(
        tmp_path, manifest_path, archived(workspace, manifest_path, tmp_path), "out"
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "transparency log: 1 entries verify" in output
    assert "NOT ACTIVATED" in output, "the command must not imply it activated anything"


def test_the_command_verifies_a_pending_operations_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operation is written before its transparency entry, so this is the report the
    next startup acts on — and it was the one nothing verified."""
    manifest_path = written_manifest(tmp_path)
    workspace = workspace_with(tmp_path, pending=True)

    code = run(
        tmp_path, manifest_path, archived(workspace, manifest_path, tmp_path), "out"
    )

    assert code == 0
    assert "pending operation reports verified: 1" in capsys.readouterr().out


def test_a_pending_operation_signed_by_an_unknown_key_fails_the_restore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case the whole check exists for: bytes that survived intact and are not ours."""
    manifest_path = written_manifest(tmp_path)
    workspace = workspace_with(tmp_path, pending=True)

    # Re-sign the pending operation's report with a key the manifest does not list. Every
    # digest and chain still verifies; only the signature is foreign.
    stranger = Ed25519Signer.from_seed(bytes([9]) * 32)
    operation_path = workspace.operations / "operation.json"
    record = json.loads(operation_path.read_text(encoding="utf-8"))
    record["signed_report"] = stranger.sign_report(record["signed_report"]["report"])
    operation_path.write_bytes(canonical_json_bytes(record) + b"\n")

    code = run(
        tmp_path, manifest_path, archived(workspace, manifest_path, tmp_path), "out"
    )

    assert code == 1
    assert "does not verify against the manifest" in capsys.readouterr().err


def test_a_missing_backup_key_fails_before_anything_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = written_manifest(tmp_path)
    workspace = workspace_with(tmp_path)
    archive = archived(workspace, manifest_path, tmp_path)
    monkeypatch.delenv(BACKUP_KEY_ENV, raising=False)

    assert run(tmp_path, manifest_path, archive, "out") == 1
    assert not (tmp_path / "out").exists()


def test_an_existing_target_is_refused_by_the_command(tmp_path: Path) -> None:
    manifest_path = written_manifest(tmp_path)
    workspace = workspace_with(tmp_path)
    archive = archived(workspace, manifest_path, tmp_path)
    (tmp_path / "out").mkdir()

    assert run(tmp_path, manifest_path, archive, "out") == 1


def test_a_pending_v2_journal_fails_closed_until_it_can_be_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = written_manifest(tmp_path)
    workspace = workspace_with(tmp_path)
    workspace.registry_v2_pending_journal.write_bytes(b'{"not":"verified"}\n')
    archive = archived(workspace, manifest_path, tmp_path)

    assert run(tmp_path, manifest_path, archive, "out") == 1
    assert (
        "pending v2 journal cannot be independently verified" in capsys.readouterr().err
    )
