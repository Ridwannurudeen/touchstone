"""One workspace identity, and a lock whose identity is the file rather than its name."""

from __future__ import annotations

from datetime import date, datetime, timezone
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
    """A path containing `..` is the same store as the path it normalises to.

    The relative-spelling and symlink cases are separate tests below, because this one
    constructs two absolute paths and proves only normalisation.
    """
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


def test_a_link_created_after_the_store_was_opened_is_still_refused(
    tmp_path: Path,
) -> None:
    """Checking identity once, at construction, is a time-of-check and nothing more.

    A second name for this inode can appear at any moment after the store is opened, and
    from then on two heads describe one log. The check that protects anything is the one
    taken under the lock immediately before the log is judged — so the link is created
    here *after* both stores exist, which the construction-time check walks straight past.
    """
    from touchstone.incidents import IncidentLog

    real = tmp_path / "incidents.jsonl"
    real.write_bytes(b"")
    alias = tmp_path / "alias.jsonl"
    opened = IncidentLog(real)

    os.link(real, alias)

    with pytest.raises(ValueError, match="has 2 names"):
        opened.open_incident(
            asset_key="eip155:1:0x" + "11" * 20,
            kind="EPOCH_FAILED",
            detail="an append after the second name appeared",
            occurred_at=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="has 2 names"):
        opened.verify()


def test_an_unidentifiable_incident_log_is_refused_rather_than_assumed_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a missing file means "no file yet".

    Treating every OSError as absence made a permissions or I/O failure — a state in which
    identity was never established at all — indistinguishable from a log that has not been
    created, and the store opened anyway on the strength of a question it could not ask.
    """
    from touchstone.incidents import IncidentLog

    real = tmp_path / "incidents.jsonl"
    real.write_bytes(b"")
    real_stat = Path.stat

    def unreadable(self, *arguments, **keywords):
        if self.name == "incidents.jsonl":
            raise PermissionError(13, "permission denied")
        return real_stat(self, *arguments, **keywords)

    monkeypatch.setattr(Path, "stat", unreadable)

    with pytest.raises(ValueError, match="cannot be identified"):
        IncidentLog(real)


def test_a_relative_store_does_not_move_when_the_process_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every durable path holder, not only the workspace root.

    A relative path is a location plus the process working directory. The directory is not
    part of the store and can change under it — in a callback, or in another thread — so
    the same object could select and verify one tree and then write to a different one.
    """
    from touchstone.evidence import EvidenceStore
    from touchstone.incidents import IncidentLog
    from touchstone.operations import OperationsStore
    from touchstone.translog import TransparencyLog

    (tmp_path / "here").mkdir()
    (tmp_path / "there").mkdir()
    monkeypatch.chdir(tmp_path / "here")

    held = {
        "evidence": (EvidenceStore("evidence"), "root"),
        "incidents": (IncidentLog("incidents.jsonl"), "path"),
        "operations": (OperationsStore("operations"), "directory"),
        "transparency": (TransparencyLog("transparency.jsonl"), "path"),
    }
    before = {name: getattr(store, field) for name, (store, field) in held.items()}

    monkeypatch.chdir(tmp_path / "there")

    for name, (store, field) in held.items():
        assert getattr(store, field) == before[name], f"{name} moved with the process"
        assert getattr(store, field).is_absolute(), f"{name} is not anchored"
        assert (tmp_path / "here").resolve() in getattr(store, field).parents


def test_a_relative_path_holder_operates_where_it_was_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anchoring is proved by operating after the process has moved, not by reading it.

    Asserting that the held attribute is absolute passes just as well when a path
    *derived* from it stayed relative and followed the process instead: the transport's
    per-source paths, the journal's sibling temporary file, and the lock the service hands
    to `exclusive_lock` are each computed somewhere other than where they are displayed.
    So each one is exercised here, and the assertion is where the bytes landed.
    """
    from run_service import Service
    from touchstone.epoch import FIXTURE_CAPTURES, FixtureTransport
    from touchstone.publish import PublisherClient
    from touchstone.sources import USTB_SOURCES

    committed = Path(__file__).parents[1] / "fixtures"
    here = tmp_path / "here"
    there = tmp_path / "there"
    (here / "fixtures").mkdir(parents=True)
    there.mkdir()
    for name in FIXTURE_CAPTURES[date(2026, 8, 13)].values():
        (here / "fixtures" / name).write_bytes((committed / name).read_bytes())

    monkeypatch.chdir(here)
    transport = FixtureTransport("fixtures", date(2026, 8, 13))
    client = PublisherClient(None, None, "asset/pending.json")
    service = Service(
        client=None,
        operations=_OperationsAt(Path("asset/operations")),
        incidents=None,
        asset_key="eip155:1:0x" + "11" * 20,
    )

    monkeypatch.chdir(there)

    response = transport.get(USTB_SOURCES[0].url, timeout=1.0, max_bytes=1 << 20)
    assert response.status_code == 200, "the transport lost its committed fixtures"

    client._write_pending({"transaction_hash": "0x" + "11" * 32})
    assert (here / "asset" / "pending.json").is_file()

    with exclusive_lock(service.lock_path):
        assert (here / "asset" / "service.lock").is_file()

    assert list(there.iterdir()) == [], "a durable path followed the process"


def test_a_symlinked_incident_log_is_the_same_store(tmp_path: Path) -> None:
    """A symlink is one more spelling of one inode, and resolving collapses it.

    Skipped rather than faked where the platform will not create one: on Windows this
    needs a privilege this account does not hold, and a test that silently does nothing is
    worse than one that says so.
    """
    from touchstone.incidents import IncidentLog

    real = tmp_path / "incidents.jsonl"
    real.write_bytes(b"")
    link = tmp_path / "linked.jsonl"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"this platform cannot create a symlink here: {error}")

    assert IncidentLog(link).path == IncidentLog(real).path
    assert IncidentLog(link).head_path == IncidentLog(real).head_path
