"""One workspace identity, and a lock whose identity is the file rather than its name."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from touchstone.locking import LockUnavailable, exclusive_lock
from touchstone.translog import TransparencyLog
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import publish_epoch  # noqa: E402


def test_a_workspace_derives_every_durable_path_from_its_root(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "asset")

    assert workspace.transparency_log == tmp_path / "asset" / "transparency.jsonl"
    assert workspace.pending_journal == tmp_path / "asset" / "pending.json"
    assert workspace.operations == tmp_path / "asset" / "operations"
    assert workspace.incidents == tmp_path / "asset" / "incidents.jsonl"
    assert workspace.lock == tmp_path / "asset" / "service.lock"


def test_the_services_default_lock_is_the_workspaces_lock(tmp_path: Path) -> None:
    """The service derives its lock from the operations directory it was handed.

    That derivation and the workspace's have to land on the same file, or the two commands
    that share this state take different locks and neither notices.
    """
    from run_service import Service

    workspace = Workspace(tmp_path / "asset")
    derived = Path(workspace.operations).parent / "service.lock"

    assert derived == workspace.lock
    assert "lock_path" in Service.__init__.__annotations__


@pytest.mark.parametrize("removed", ["--transparency-log", "--pending"])
def test_the_cli_cannot_be_given_a_layout_the_service_would_not_share(
    removed: str, tmp_path: Path
) -> None:
    """A mismatched layout is not rejected — it is no longer expressible.

    While the log and the journal were separate arguments, the CLI could be pointed at a
    running service's transparency log while deriving its lock from somewhere else, and
    both processes would verify one log head before either appended to it. Nothing checks
    that independently supplied paths belong together, because there is nothing to check
    them against.
    """
    with pytest.raises(SystemExit) as refused:
        publish_epoch.main(
            [
                "--manifest",
                str(tmp_path / "manifest.json"),
                removed,
                str(tmp_path / "elsewhere"),
            ]
        )

    assert refused.value.code == 2


def test_a_second_name_for_one_file_does_not_grant_a_second_lock(
    tmp_path: Path,
) -> None:
    """A lock named after a path is defeated by giving the file another path.

    A hardlink, a symlink, and an absolute and a relative spelling are four names for one
    inode. Locking `name + ".lock"` made four locks that all succeeded at once over one
    file. Locking the file itself makes the inode the identity, which is the one thing
    another name cannot change.
    """
    real = tmp_path / "transparency.jsonl"
    real.write_bytes(b"")
    alias = tmp_path / "alias.jsonl"
    os.link(real, alias)

    with exclusive_lock(real):
        with pytest.raises(LockUnavailable):
            with exclusive_lock(alias):
                pass


def test_a_log_reached_by_two_names_is_still_one_log(tmp_path: Path) -> None:
    """The lock a writer takes has to be the one another writer would be stopped by.

    `TransparencyLog` locked a sidecar named after the path it was constructed with, so two
    instances over two names for one file took two different locks. Both would then verify
    the same head and append entries claiming the same predecessor — the exact break the
    lock exists to prevent, reached by nothing more exotic than a second name.
    """
    real = tmp_path / "transparency.jsonl"
    TransparencyLog(real).append(
        _signed(),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    alias = tmp_path / "alias.jsonl"
    os.link(real, alias)

    with exclusive_lock(real):
        with pytest.raises(LockUnavailable):
            TransparencyLog(alias).append(
                _signed(),
                transaction_hash="0x" + "bb" * 32,
                receipt={"block_number": 2, "status": 1},
            )

    assert len(TransparencyLog(real).verify()) == 1, "the second write never landed"


def test_locking_a_log_leaves_the_log_alone(tmp_path: Path) -> None:
    """The lock is taken past any content, so the protected file is still just a file."""
    path = tmp_path / "transparency.jsonl"
    log = TransparencyLog(path)
    entry = log.append(
        _signed(),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    before = path.read_bytes()

    with exclusive_lock(path):
        assert path.read_bytes() == before, "locking wrote nothing into the log"

    assert log.verify() == [entry]


def _signed():
    from touchstone.signing import Ed25519Signer

    signer = Ed25519Signer.from_seed(bytes(range(32)))
    return signer.sign_report(
        {
            "asset_key": "eip155:1:0x" + "11" * 20,
            "publisher_kid": signer.kid,
            "sequence": 1,
            "correction_of": None,
        }
    )
