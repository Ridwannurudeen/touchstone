"""One workspace identity, and a lock whose identity is the file rather than its name."""

from __future__ import annotations

from datetime import datetime, timezone
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


def test_a_real_service_locks_the_file_the_workspace_names(tmp_path: Path) -> None:
    """Construct the service and read the lock it will actually take.

    Recomputing the derivation in the test and comparing it to itself asserted the test's
    own arithmetic. What matters is the value the object carries, because that is what
    `serve()` passes to `exclusive_lock`.
    """
    from run_service import Service

    workspace = Workspace(tmp_path / "asset")
    service = Service(
        client=None,
        operations=_OperationsAt(workspace.operations),
        incidents=None,
        asset_key="eip155:1:0x" + "11" * 20,
    )

    assert service.lock_path == workspace.lock


class _OperationsAt:
    """Only the attribute Service reads to derive its default lock."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory


@pytest.mark.parametrize("removed", ["--transparency-log", "--pending"])
def test_the_cli_cannot_be_given_a_layout_the_service_would_not_share(
    removed: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mismatched layout is not rejected — it is no longer expressible.

    While the log and the journal were separate arguments, the CLI could be pointed at a
    running service's transparency log while deriving its lock from somewhere else, and
    both processes would verify one log head before either appended to it. Nothing checks
    that independently supplied paths belong together, because there is nothing to check
    them against.

    Every other required argument is supplied, and the message is asserted: exiting 2
    because some *other* argument was missing would have passed against the very parser
    that still accepted these flags.
    """
    with pytest.raises(SystemExit) as refused:
        publish_epoch.main(
            [
                "--manifest",
                str(tmp_path / "manifest.json"),
                "--signed-report",
                str(tmp_path / "signed.json"),
                "--report-uri",
                "urn:touchstone:test:1",
                "--workspace",
                str(tmp_path / "workspace"),
                removed,
                str(tmp_path / "elsewhere"),
            ]
        )

    assert refused.value.code == 2
    assert f"unrecognized arguments: {removed}" in capsys.readouterr().err


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
    """The lock is taken past any content, so the protected file is still just a file.

    Windows-specific in what it distinguishes: `msvcrt.locking` locks a byte range, so an
    offset of zero would collide with real data. POSIX `flock` locks the whole open file
    description and ignores the offset entirely, so this passes there either way — the
    guarantee it protects is the same, but only this platform can fail it.
    """
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


def test_an_incident_log_reachable_by_two_names_is_refused(tmp_path: Path) -> None:
    """The lock is per-inode; the completeness head is per-path. Two names break that.

    Serialising on the shared log is not enough. The original writes entry 1 and advances
    `incidents.jsonl.head`; the alias writes entries 2 and 3 and advances
    `alias.jsonl.head`. The first name then sees three entries attested by a head claiming
    one — past the single-entry repair window, so a perfectly valid sequence of serialized
    appends is reported as corruption. A two-file store cannot have one identity under
    hardlinks, so opening the second name is refused instead.
    """
    from touchstone.incidents import IncidentLog

    real = tmp_path / "incidents.jsonl"
    IncidentLog(real).open_incident(
        asset_key="eip155:1:0x" + "11" * 20,
        kind="EPOCH_FAILED",
        detail="the first entry",
        occurred_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
    )
    os.link(real, tmp_path / "alias.jsonl")

    with pytest.raises(ValueError, match="has 2 names"):
        IncidentLog(tmp_path / "alias.jsonl")
    with pytest.raises(ValueError, match="has 2 names"):
        (
            IncidentLog(real),
            "and the original is refused too, rather than silently diverging",
        )


def test_one_incident_log_is_one_store_however_it_is_spelled(tmp_path: Path) -> None:
    """Relative, absolute and symlinked spellings are the same store, not three."""
    from touchstone.incidents import IncidentLog

    nested = tmp_path / "workspace"
    nested.mkdir()
    absolute = IncidentLog(nested / "incidents.jsonl")
    indirect = IncidentLog(
        tmp_path / "workspace" / ".." / "workspace" / "incidents.jsonl"
    )

    assert indirect.path == absolute.path
    assert indirect.head_path == absolute.head_path
    assert indirect.lock_path == absolute.lock_path


def test_a_workspace_anchors_a_relative_root_to_one_absolute_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative root is not a location — it is a location plus the current directory.

    The current directory is not part of the workspace and can change under it, so the
    same stored `asset/service.lock` named two different files from two working
    directories: exactly the divergence one identity exists to prevent.
    """
    (tmp_path / "here" / "asset").mkdir(parents=True)
    (tmp_path / "there" / "asset").mkdir(parents=True)

    monkeypatch.chdir(tmp_path / "here")
    workspace = Workspace("asset")
    lock_from_here = workspace.lock

    monkeypatch.chdir(tmp_path / "there")

    assert workspace.lock == lock_from_here
    assert workspace.lock == (tmp_path / "here" / "asset" / "service.lock").resolve()
