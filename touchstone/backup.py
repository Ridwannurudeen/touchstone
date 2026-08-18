"""An encrypted snapshot of one workspace, taken at one instant.

The hard part is not the encryption. It is that a workspace is several files which are only
meaningful together: a transparency log, an incident log and its completeness head, an
operations state, a pending journal, and the evidence every report cites. Copy them at
different moments and the archive holds a log from one instant, a head from another and an
operation from a third — an archive that restores into a state the service was never in,
and whose incident head disagrees with its own log.

So a backup is not a file copier. The rule is that whoever takes it must already hold the
workspace lock. The daemon owns that lock for its entire serving lifetime, which means the
scheduled backup runs *inside* the daemon between mutations, and the standalone command
acquires the same lock or refuses. There is no mode in which a second process copies a live
workspace.

Restore never touches a running workspace. It decrypts, verifies chains and digests into a
fresh staging directory, and stops. Activation is a separate deliberate act, because an
automatic restore that overwrites the live tree is a way to lose the only copy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from touchstone.locking import Held, LockUnavailable, exclusive_lock
from touchstone.quantities import utc_instant
from touchstone.signing import canonical_json_bytes, strict_json_loads
from touchstone.workspace import Workspace


ARCHIVE_VERSION = "touchstone.backup.v1"
BACKUP_KEY_ENV = "TOUCHSTONE_BACKUP_KEY"
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class BackupError(RuntimeError):
    """A backup could not be taken, or an archive could not be trusted."""


@dataclass(frozen=True, slots=True)
class Member:
    """One file in the archive, named relative to the workspace root."""

    path: str
    size: int
    sha256: str


# A `Lease` used to stand here: a dataclass holding a path, which anyone could construct.
# It stated the requirement in the signature and established nothing, so a caller who had
# never taken the lock satisfied it exactly as well as one who had. `create` now requires
# the `Held` that `exclusive_lock` yields, which exists only after the kernel granted the
# lock and stops being valid when the block exits — and it checks that the hold is on this
# workspace's lock rather than on some other one the caller happened to be holding.


def backup_key(environ: Mapping[str, str] | None = None) -> bytes:
    """Read the archive key, and refuse one that is doing another job as well."""
    source = os.environ if environ is None else environ
    encoded = source.get(BACKUP_KEY_ENV)
    if not encoded:
        raise BackupError(f"{BACKUP_KEY_ENV} is not set")
    if len(encoded) != KEY_BYTES * 2 or any(
        character not in "0123456789abcdef" for character in encoded
    ):
        raise BackupError(
            f"{BACKUP_KEY_ENV} must be exactly 64 lowercase hexadecimal characters"
        )
    # One secret behind two roles means one compromise takes both. The backup key opens
    # every archive ever taken; the reporting seed signs what those archives contain.
    for other in ("TOUCHSTONE_SIGNING_SEED", "TOUCHSTONE_PUBLISHER_PRIVATE_KEY"):
        value = source.get(other)
        if value and value.removeprefix("0x") == encoded:
            raise BackupError(
                f"{BACKUP_KEY_ENV} is the same secret as {other}; one compromise would "
                "both read every archive and forge what they contain"
            )
    return bytes.fromhex(encoded)


def capture(workspace: Workspace) -> list[tuple[Member, bytes]]:
    """Read each file exactly once, and describe it from the bytes that were read.

    The first version read every file twice — once in this function to take its digest,
    and again while building the archive to obtain its contents. That is the defect this
    project has now found in six other modules wearing a different hat: the bytes that
    were measured and the bytes that were stored need not be the same bytes, so the
    archive could carry one file's digest over another file's contents and satisfy every
    check on the way out. The bytes travel with their own description from here on.

    The exclusions are as deliberate as the inclusions. The lock is a live artifact of a
    running process; the heartbeat is expected to be stale and is never restored; both
    would restore a claim about a process that is not running.
    """
    root = workspace.root
    found: list[tuple[Member, bytes]] = []
    for path in sorted(_candidates(workspace)):
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            # Symlinks, devices and directories-as-members are all ways for an archive to
            # describe something other than bytes. Only regular files are represented.
            raise BackupError(f"refusing to archive a non-regular path: {path.name}")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise BackupError(f"{path.name} cannot be read: {error}") from error
        found.append(
            (
                Member(
                    path=path.relative_to(root).as_posix(),
                    size=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(),
                ),
                raw,
            )
        )
    if not found:
        raise BackupError("there is nothing in this workspace to back up")
    return found


def members(workspace: Workspace) -> list[Member]:
    """The shape of what would be archived, without the contents."""
    return [member for member, _ in capture(workspace)]


def _candidates(workspace: Workspace) -> list[Path]:
    evidence = workspace.evidence
    paths = [
        workspace.transparency_log,
        workspace.pending_journal,
        workspace.incidents,
        workspace.incidents.with_name(workspace.incidents.name + ".head"),
        evidence / "index.jsonl",
    ]
    paths.extend(sorted((evidence / "objects").glob("*")))
    operations = workspace.operations
    if operations.is_dir():
        paths.extend(sorted(path for path in operations.rglob("*") if path.is_file()))
    return paths


def create(
    held: Held,
    workspace: str | Path,
    *,
    now: datetime,
    key: bytes,
    asset_key: str,
    registry_address: str,
    nonce: bytes | None = None,
) -> bytes:
    """Build one encrypted archive from one reading of the workspace.

    Requires the live `Held` that `exclusive_lock` yields, and checks it is a hold on
    *this* workspace's lock. A caller who never took the lock cannot produce one, and a
    caller whose block has exited holds one that refuses.
    """
    if not isinstance(held, Held):
        raise BackupError(
            "a backup requires the Held that exclusive_lock yields, as proof the "
            "workspace lock is actually held"
        )
    root = Workspace(workspace)
    try:
        held.verify(root.lock)
    except LockUnavailable as error:
        raise BackupError(str(error)) from error
    captured_at = utc_instant(now, "now")
    taken = capture(root)
    payload = {
        "asset_key": asset_key,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "files": [
            {
                "bytes": raw.hex(),
                "path": member.path,
                "sha256": member.sha256,
                "size": member.size,
            }
            for member, raw in taken
        ],
        "registry_address": registry_address,
        "version": ARCHIVE_VERSION,
    }
    plaintext = canonical_json_bytes(payload)
    if len(plaintext) > MAX_ARCHIVE_BYTES:
        raise BackupError("this workspace is larger than the archive limit")
    # Identity is authenticated, not merely stored: an archive from another asset or
    # another deployment cannot be decrypted into this one by editing a field.
    associated = canonical_json_bytes(
        {
            "asset_key": asset_key,
            "registry_address": registry_address,
            "version": ARCHIVE_VERSION,
        }
    )
    chosen = secrets.token_bytes(NONCE_BYTES) if nonce is None else nonce
    if len(chosen) != NONCE_BYTES:
        raise BackupError(f"the nonce must be exactly {NONCE_BYTES} bytes")
    return chosen + AESGCM(key).encrypt(chosen, plaintext, associated)


def open_archive(
    archive: bytes,
    *,
    key: bytes,
    asset_key: str,
    registry_address: str,
) -> Mapping[str, object]:
    """Decrypt and authenticate before anything is created on disk."""
    if not isinstance(archive, (bytes, bytearray)) or len(archive) <= NONCE_BYTES:
        raise BackupError("the archive is too short to be one")
    associated = canonical_json_bytes(
        {
            "asset_key": asset_key,
            "registry_address": registry_address,
            "version": ARCHIVE_VERSION,
        }
    )
    try:
        plaintext = AESGCM(key).decrypt(
            bytes(archive[:NONCE_BYTES]), bytes(archive[NONCE_BYTES:]), associated
        )
    except InvalidTag:
        raise BackupError(
            "the archive did not authenticate: wrong key, altered bytes, or an archive "
            "belonging to another asset or deployment"
        ) from None
    try:
        value = strict_json_loads(plaintext)
    except (TypeError, ValueError) as error:
        # Authenticated and still not a Touchstone archive: the key was right and the
        # contents are not what this module writes. That is this module's refusal.
        raise BackupError(f"the archive is not readable JSON: {error}") from error
    if not isinstance(value, Mapping) or value.get("version") != ARCHIVE_VERSION:
        raise BackupError("the archive is not a supported Touchstone backup")
    return value


def restore(
    archive: bytes,
    destination: str | Path,
    *,
    key: bytes,
    asset_key: str,
    registry_address: str,
) -> list[Member]:
    """Verify an archive into a new empty directory. Never into a live workspace.

    Every digest is recomputed from the exact bytes about to be written, not read from the
    inventory that accompanies them: an inventory is part of the archive, so trusting it
    to validate the archive proves nothing.
    """
    target = Path(destination).resolve()
    if target.exists():
        raise BackupError(f"the restore target already exists: {target}")
    value = open_archive(
        archive, key=key, asset_key=asset_key, registry_address=registry_address
    )
    files = value.get("files")
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
        raise BackupError("the archive has no file inventory")

    verified: list[Member] = []
    seen: set[str] = set()
    for item in tuple(files):
        if not isinstance(item, Mapping):
            raise BackupError("each archive member must be a mapping")
        member = _member(item)
        if member.path in seen:
            raise BackupError(f"the archive names {member.path} twice")
        seen.add(member.path)
        try:
            raw = bytes.fromhex(str(item["bytes"]))
        except ValueError as error:
            raise BackupError(
                f"{member.path} does not carry decodable content"
            ) from error
        if len(raw) != member.size:
            raise BackupError(f"{member.path} is not the size the archive claims")
        if hashlib.sha256(raw).hexdigest() != member.sha256:
            raise BackupError(f"{member.path} does not match its recorded digest")
        verified.append(member)

    try:
        target.mkdir(parents=True)
        for member, item in zip(verified, tuple(files), strict=True):
            written = target / member.path
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_bytes(bytes.fromhex(str(item["bytes"])))
    except OSError as error:
        # Everything above this point was verification; this is the first write. A
        # half-created target is reported in this module's terms rather than as whatever
        # the filesystem raised, because the caller's recovery is the same either way.
        raise BackupError(f"the restore target cannot be written: {error}") from error
    return verified


def _member(item: Mapping[str, object]) -> Member:
    path = item.get("path")
    if not isinstance(path, str) or not path:
        raise BackupError("each archive member must name a path")
    # Judged under both path flavours, because an archive is portable and the check must
    # not be. `Path("/etc/passwd").is_absolute()` is False on Windows and True on POSIX,
    # so a single-flavour check would let a POSIX-absolute member through on one host and
    # refuse it on another — and an archive written on one is restored on the other.
    posix, windows = PurePosixPath(path), PureWindowsPath(path)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or path.startswith(("/", "\\"))
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        # Traversal in an archive is how a restore writes outside its target. The check is
        # on the parts rather than the string, so "a/../../b" cannot slip past either.
        raise BackupError(f"refusing an unsafe archive path: {path}")
    size = item.get("size")
    digest = item.get("sha256")
    if type(size) is not int or size < 0:
        raise BackupError(f"{path} has no valid size")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise BackupError(f"{path} has no valid digest")
    if not isinstance(item.get("bytes"), str):
        raise BackupError(f"{path} has no content")
    return Member(path=path, size=size, sha256=digest)


def take_offline(
    workspace: str | Path,
    *,
    now: datetime,
    key: bytes,
    asset_key: str,
    registry_address: str,
) -> bytes:
    """Back up a workspace nothing is serving, by taking every lock a writer would hold.

    If a daemon is running this refuses rather than proceeding, which is the entire point:
    a second process copying a live workspace is how an archive ends up holding files from
    three different instants.

    **Both locks, not just the daemon's.** The observer writes evidence into this workspace
    and deliberately does not hold the daemon's lock — it could not, because the daemon holds
    that one for its whole lifetime. Taking only the daemon lock therefore proved the daemon
    was stopped and said nothing about the watcher, so an archive could be taken mid-append
    and the quiescence this function exists to establish would simply not have been
    established.
    """
    root = Workspace(workspace)
    try:
        with exclusive_lock(root.lock) as held, exclusive_lock(root.observer_lock):
            return create(
                held,
                root.root,
                now=now,
                key=key,
                asset_key=asset_key,
                registry_address=registry_address,
            )
    except OSError as error:
        # Acquiring an OS lock is I/O and can fail for reasons that are not contention: a
        # permission refusal, a full descriptor table. Those escaped as raw OSError while
        # every other failure here was this module's own.
        raise BackupError(f"the workspace lock cannot be taken: {error}") from error
    except LockUnavailable as error:
        raise BackupError(
            "this workspace is in use by a running service; an online backup must be "
            "taken by the daemon itself, between mutations, while it holds the lock"
        ) from error
