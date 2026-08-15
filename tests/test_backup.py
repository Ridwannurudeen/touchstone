"""A workspace is several files that are only meaningful together.

The tests that matter here are the concurrency ones and the refusals. An archive holding a
transparency log from one instant and an incident head from another restores into a state
the service was never in, and nothing downstream would notice until the head disagreed with
its own log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets

import pytest

from touchstone.backup import (
    ARCHIVE_VERSION,
    Lease,
    BACKUP_KEY_ENV,
    NONCE_BYTES,
    BackupError,
    backup_key,
    create,
    members,
    open_archive,
    restore,
    take_offline,
)
from touchstone.evidence import EvidenceStore
from touchstone.incidents import SOURCE_UNAVAILABLE, IncidentLog
from touchstone.locking import exclusive_lock
from touchstone.translog import TransparencyLog
from touchstone.workspace import Workspace

import sys

sys.path.insert(0, str(Path(__file__).parent))
from test_publish import _signed_report  # noqa: E402


AT = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
REGISTRY = "0x" + "ab" * 20
KEY = bytes(range(32))


def populated(tmp_path: Path) -> Workspace:
    """A workspace with every kind of file a real one carries."""
    workspace = Workspace(tmp_path / "asset")
    workspace.root.mkdir(parents=True, exist_ok=True)

    TransparencyLog(workspace.transparency_log).append(
        _signed_report(1),
        transaction_hash="0x" + "aa" * 32,
        receipt={"block_number": 1, "status": 1},
    )
    IncidentLog(workspace.incidents).open_incident(
        asset_key=ASSET,
        kind=SOURCE_UNAVAILABLE,
        detail="the feed returned 403",
        occurred_at=AT,
    )
    store = EvidenceStore(workspace.evidence)
    store.store(
        b'{"net_asset_value":"11.17558800"}',
        source_id="superstate-ustb-nav-daily",
        source_url="https://api.superstate.com/v1/funds/1/nav-daily",
        retrieved_at=AT,
        declared_mime="application/json",
    )
    return workspace


def archive_of(workspace: Workspace, **changes: object) -> bytes:
    arguments: dict[str, object] = {
        "now": AT,
        "key": KEY,
        "asset_key": ASSET,
        "registry_address": REGISTRY,
    }
    arguments.update(changes)
    return create(Lease(root=workspace.root), **arguments)


def test_an_archive_round_trips_every_file(tmp_path: Path) -> None:
    workspace = populated(tmp_path)
    expected = {member.path: member.sha256 for member in members(workspace)}

    restored = restore(
        archive_of(workspace),
        tmp_path / "restored",
        key=KEY,
        asset_key=ASSET,
        registry_address=REGISTRY,
    )

    assert {member.path: member.sha256 for member in restored} == expected
    for member in restored:
        written = tmp_path / "restored" / member.path
        assert written.read_bytes() == (workspace.root / member.path).read_bytes()


def test_the_evidence_and_both_logs_are_in_the_archive(tmp_path: Path) -> None:
    """Evidence is the only thing here that cannot be recreated from anything else."""
    paths = {member.path for member in members(populated(tmp_path))}

    assert "transparency.jsonl" in paths
    assert "incidents.jsonl" in paths
    assert "incidents.jsonl.head" in paths
    assert "evidence/index.jsonl" in paths
    assert any(path.startswith("evidence/objects/") for path in paths)


def test_the_lock_and_heartbeat_are_not_archived(tmp_path: Path) -> None:
    """Both describe a running process, and restoring one restores a false claim."""
    workspace = populated(tmp_path)
    workspace.heartbeat.write_bytes(b"{}")
    workspace.lock.write_bytes(b"")

    paths = {member.path for member in members(workspace)}

    assert "heartbeat.json" not in paths
    assert "service.lock" not in paths


def test_a_standalone_backup_refuses_while_a_daemon_holds_the_workspace(
    tmp_path: Path,
) -> None:
    """The rule the whole module rests on: no second process copies a live workspace."""
    workspace = populated(tmp_path)

    with exclusive_lock(workspace.lock):
        with pytest.raises(BackupError, match="in use by a running service"):
            take_offline(
                workspace.root,
                now=AT,
                key=KEY,
                asset_key=ASSET,
                registry_address=REGISTRY,
            )


def test_a_standalone_backup_succeeds_when_nothing_is_serving(tmp_path: Path) -> None:
    workspace = populated(tmp_path)

    archive = take_offline(
        workspace.root, now=AT, key=KEY, asset_key=ASSET, registry_address=REGISTRY
    )

    assert (
        open_archive(archive, key=KEY, asset_key=ASSET, registry_address=REGISTRY)[
            "version"
        ]
        == ARCHIVE_VERSION
    )


def test_a_backup_taken_between_mutations_holds_one_consistent_instant(
    tmp_path: Path,
) -> None:
    """Either the complete state before a mutation or the complete state after it.

    The archive is taken while the lock is held, so an append that lands afterwards is
    absent from it entirely rather than half present.
    """
    workspace = populated(tmp_path)
    log = TransparencyLog(workspace.transparency_log)

    with exclusive_lock(workspace.lock):
        before = create(
            Lease(root=workspace.root),
            now=AT,
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )

    log.append(
        _signed_report(2),
        transaction_hash="0x" + "bb" * 32,
        receipt={"block_number": 2, "status": 1},
    )
    with exclusive_lock(workspace.lock):
        after = create(
            Lease(root=workspace.root),
            now=AT,
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )

    first = restore(
        before, tmp_path / "a", key=KEY, asset_key=ASSET, registry_address=REGISTRY
    )
    second = restore(
        after, tmp_path / "b", key=KEY, asset_key=ASSET, registry_address=REGISTRY
    )
    entries_before = TransparencyLog(tmp_path / "a" / "transparency.jsonl").verify()
    entries_after = TransparencyLog(tmp_path / "b" / "transparency.jsonl").verify()

    assert len(entries_before) == 1, "the state before the append, whole"
    assert len(entries_after) == 2, "the state after it, whole"
    assert {m.path for m in first} == {m.path for m in second}


def test_a_restored_workspace_still_verifies_its_own_chains(tmp_path: Path) -> None:
    """The point of the archive is that what comes out is provably what went in."""
    workspace = populated(tmp_path)

    restore(
        archive_of(workspace),
        tmp_path / "restored",
        key=KEY,
        asset_key=ASSET,
        registry_address=REGISTRY,
    )

    restored = Workspace(tmp_path / "restored")
    assert len(TransparencyLog(restored.transparency_log).verify()) == 1
    assert len(IncidentLog(restored.incidents).verify()) == 1
    assert EvidenceStore(restored.evidence).verified_entries()


def test_a_pending_operation_survives_for_startup_reconciliation(
    tmp_path: Path,
) -> None:
    """Restoring must not quietly resolve work the service still owes."""
    from touchstone.operations import OperationsStore

    workspace = populated(tmp_path)
    operations = OperationsStore(workspace.operations)
    operations.begin_operation(
        _signed_report(1),
        report_uri="urn:touchstone:report:1",
        correction_of=None,
        scheduled_for=AT,
    )

    restore(
        archive_of(workspace),
        tmp_path / "restored",
        key=KEY,
        asset_key=ASSET,
        registry_address=REGISTRY,
    )

    reopened = OperationsStore(Workspace(tmp_path / "restored").operations)
    assert reopened.load_operation() is not None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"key": bytes(range(1, 33))}, "did not authenticate"),
        ({"asset_key": "eip155:1:0x" + "99" * 20}, "did not authenticate"),
        ({"registry_address": "0x" + "cd" * 20}, "did not authenticate"),
    ],
)
def test_an_archive_that_does_not_belong_here_creates_no_target(
    tmp_path: Path, changes: dict, reason: str
) -> None:
    """Wrong key, another asset, another deployment — all indistinguishable, all refused."""
    workspace = populated(tmp_path)
    archive = archive_of(workspace)
    arguments = {"key": KEY, "asset_key": ASSET, "registry_address": REGISTRY}
    arguments.update(changes)

    with pytest.raises(BackupError, match=reason):
        restore(archive, tmp_path / "restored", **arguments)

    assert not (tmp_path / "restored").exists(), (
        "a refused restore leaves nothing behind"
    )


def test_a_tampered_archive_creates_no_target(tmp_path: Path) -> None:
    workspace = populated(tmp_path)
    archive = bytearray(archive_of(workspace))
    archive[-1] ^= 0xFF

    with pytest.raises(BackupError, match="did not authenticate"):
        restore(
            bytes(archive),
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )

    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("archive", [b"", b"short", bytes(NONCE_BYTES)])
def test_a_truncated_archive_is_refused(tmp_path: Path, archive: bytes) -> None:
    with pytest.raises(BackupError):
        restore(
            archive,
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )
    assert not (tmp_path / "restored").exists()


def test_restore_never_overwrites_an_existing_directory(tmp_path: Path) -> None:
    """An automatic restore over a live tree is a way to lose the only copy."""
    workspace = populated(tmp_path)
    (tmp_path / "restored").mkdir()

    with pytest.raises(BackupError, match="already exists"):
        restore(
            archive_of(workspace),
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )


def test_every_archive_uses_a_fresh_nonce(tmp_path: Path) -> None:
    """A reused nonce under one key is what breaks AES-GCM, not a weak key."""
    workspace = populated(tmp_path)

    nonces = {archive_of(workspace)[:NONCE_BYTES] for _ in range(8)}

    assert len(nonces) == 8


def test_a_backup_key_that_is_another_secret_is_refused() -> None:
    """One secret behind two roles means one compromise takes both."""
    key = secrets.token_bytes(32).hex()

    with pytest.raises(BackupError, match="same secret"):
        backup_key({BACKUP_KEY_ENV: key, "TOUCHSTONE_SIGNING_SEED": key})
    with pytest.raises(BackupError, match="same secret"):
        backup_key(
            {BACKUP_KEY_ENV: key, "TOUCHSTONE_PUBLISHER_PRIVATE_KEY": "0x" + key}
        )


@pytest.mark.parametrize(
    "value", ["", "ab", "AB" * 32, "zz" * 32, "0x" + "ab" * 32, "ab" * 31]
)
def test_a_backup_key_of_the_wrong_shape_is_refused(value: str) -> None:
    with pytest.raises(BackupError, match=BACKUP_KEY_ENV):
        backup_key({BACKUP_KEY_ENV: value})


def test_a_valid_backup_key_is_returned_as_bytes() -> None:
    key = secrets.token_bytes(32)

    assert backup_key({BACKUP_KEY_ENV: key.hex()}) == key


def test_an_empty_workspace_has_nothing_to_back_up(tmp_path: Path) -> None:
    (tmp_path / "asset").mkdir()

    with pytest.raises(BackupError, match="nothing in this workspace"):
        create(
            Lease(root=tmp_path / "asset"),
            now=AT,
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )


def forged(payload: dict) -> bytes:
    """Encrypt an arbitrary payload with the real key, as a holder of it could."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from touchstone.signing import canonical_json_bytes

    associated = canonical_json_bytes(
        {
            "asset_key": ASSET,
            "registry_address": REGISTRY,
            "version": ARCHIVE_VERSION,
        }
    )
    nonce = bytes(range(NONCE_BYTES))
    return nonce + AESGCM(KEY).encrypt(
        nonce, canonical_json_bytes(payload), associated
    )


def payload(**changes: object) -> dict:
    value: dict = {
        "asset_key": ASSET,
        "captured_at": "2026-08-15T09:00:00Z",
        "files": [
            {
                "bytes": b"real bytes".hex(),
                "path": "transparency.jsonl",
                "sha256": "ab" * 32,
                "size": len(b"real bytes"),
            }
        ],
        "registry_address": REGISTRY,
        "version": ARCHIVE_VERSION,
    }
    value.update(changes)
    return value


def test_a_valid_archive_whose_inventory_lies_is_refused(tmp_path: Path) -> None:
    """Authentication proves who made the archive, not that it describes itself.

    A holder of the backup key can encrypt any payload it likes, so the digests are
    recomputed from the exact bytes about to be written rather than read from the
    inventory that travelled with them — an inventory is part of the archive, and
    trusting it to validate the archive proves nothing.
    """
    with pytest.raises(BackupError, match="does not match its recorded digest"):
        restore(
            forged(payload()),
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )

    assert not (tmp_path / "restored").exists()


def test_a_valid_archive_whose_size_lies_is_refused(tmp_path: Path) -> None:
    import hashlib

    raw = b"real bytes"
    lying = payload(
        files=[
            {
                "bytes": raw.hex(),
                "path": "transparency.jsonl",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw) + 1,
            }
        ]
    )

    with pytest.raises(BackupError, match="not the size the archive claims"):
        restore(
            forged(lying),
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../outside.json", "a/../../b.json", "C:/windows/x"]
)
def test_a_valid_archive_cannot_write_outside_its_target(
    tmp_path: Path, path: str
) -> None:
    """Path traversal in an archive is how a restore writes somewhere it was not sent."""
    import hashlib

    raw = b"escaped"
    traversing = payload(
        files=[
            {
                "bytes": raw.hex(),
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        ]
    )

    with pytest.raises(BackupError, match="unsafe archive path"):
        restore(
            forged(traversing),
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )

    assert not (tmp_path / "restored").exists()


def test_a_valid_archive_naming_one_path_twice_is_refused(tmp_path: Path) -> None:
    import hashlib

    raw = b"twice"
    member = {
        "bytes": raw.hex(),
        "path": "transparency.jsonl",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }

    with pytest.raises(BackupError, match="twice"):
        restore(
            forged(payload(files=[member, member])),
            tmp_path / "restored",
            key=KEY,
            asset_key=ASSET,
            registry_address=REGISTRY,
        )
