"""Liveness that cannot survive the process it describes.

A heartbeat records facts and an expiry. It never records health. That distinction is the
whole point: a file containing ``"status": "green"`` keeps saying green after the daemon
writing it has died, and the operator learns nothing until someone thinks to look at the
timestamp. Here the only durable claims are *when this was written* and *when it stops
counting*, and health is computed at read time from those against the reader's own clock.

Two facts are therefore separate throughout, because conflating them hides the failure that
matters most. Whether the daemon is alive is one question; whether it is still producing
epochs is another. A process that is running perfectly while its source has been unreachable
for two days is alive and unhealthy, and a heartbeat that answered only the first question
would call that green.

The record is written atomically, so a crash mid-write leaves the previous heartbeat rather
than a truncated one — and that previous heartbeat then expires on schedule, which is what
makes death detectable rather than merely likely.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from touchstone.quantities import finite_positive, utc_instant
from touchstone.signing import canonical_json_bytes, frozen_snapshot, strict_json_loads


HEARTBEAT_VERSION = "touchstone.heartbeat.v1"

# How often a healthy daemon rewrites its heartbeat, and how long one counts for. The
# expiry is three intervals rather than one: a single missed write is a slow disk, not a
# dead process, and a watchdog that alerts on the first one teaches operators to ignore it.
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_EXPIRY_SECONDS = 180.0

_FIELDS = frozenset(
    {
        "asset_key",
        "expires_at",
        "last_attempted_slot",
        "last_backup_at",
        "last_successful_epoch",
        "next_scheduled_slot",
        "process_identity",
        "process_id",
        "registry_address",
        "runway_checked_at",
        "sequence",
        "version",
        "written_at",
    }
)


class HeartbeatError(RuntimeError):
    """A heartbeat could not be written, or could not be read as one."""


@dataclass(frozen=True, slots=True)
class Health:
    """A read-time verdict, and the reasons behind it.

    ``daemon_alive`` and ``epoch_healthy`` are answered separately and never collapsed into
    a single boolean by this module. A caller that wants one can take ``ok``; a caller that
    wants to know *which* thing is wrong has it without parsing prose.
    """

    daemon_alive: bool
    epoch_healthy: bool
    reasons: tuple[str, ...]
    record: Mapping[str, object] | None

    @property
    def ok(self) -> bool:
        return self.daemon_alive and self.epoch_healthy


def process_identity(process_id: int | None = None) -> str:
    """Return something that distinguishes this process from a later one reusing its PID.

    PIDs are recycled, and on a machine that has restarted the daemon a few times the new
    process can inherit the dead one's number within minutes. A heartbeat naming only the
    PID would then be confirmed alive by an unrelated program. The boot-relative start time
    is the cheap portable discriminator: two processes may share a PID, but not a PID and a
    start instant.
    """
    pid = os.getpid() if process_id is None else process_id
    if type(pid) is not int or pid < 0:
        raise HeartbeatError("process_id must be a non-negative integer")
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return f"{pid}:{handle.read().rsplit(b') ', 1)[1].split()[19].decode()}"
    except (OSError, IndexError, ValueError):
        # No procfs, or a kernel that lays it out differently. The creation time from the
        # OS is the same idea by another route; where neither is available the identity
        # degrades to the PID alone, and `verify` says so rather than pretending.
        try:
            import psutil  # noqa: PLC0415 - a runtime pin; imported here only when needed
        except ImportError:
            # Reached only by an install that dropped a declared dependency. The degradation
            # is kept because `verify` names it, and a heartbeat that says "I could not
            # establish this" is worth more than one that quietly claims a PID is enough.
            return f"{pid}:unknown"
        try:
            return f"{pid}:{psutil.Process(pid).create_time()}"
        except Exception:
            return f"{pid}:unknown"


def build_record(
    *,
    asset_key: str,
    registry_address: str,
    sequence: int,
    now: datetime,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    expiry_seconds: float = DEFAULT_EXPIRY_SECONDS,
    last_attempted_slot: str | None = None,
    last_successful_epoch: str | None = None,
    next_scheduled_slot: str | None = None,
    last_backup_at: str | None = None,
    runway_checked_at: str | None = None,
    process_id: int | None = None,
) -> dict[str, object]:
    """Build one heartbeat from one reading of the clock.

    Every timestamp below is derived from the single ``now`` this was given. Reading the
    clock again for the expiry would let a record claim a window that never contained its
    own write, which is the same defect that made an epoch's evidence root disagree with
    the report that carried it.
    """
    del (
        interval_seconds
    )  # cadence is the caller's business; the record states its expiry
    written_at = utc_instant(now, "now")
    expiry = finite_positive(expiry_seconds, "expiry_seconds")
    if type(sequence) is not int or sequence < 1:
        raise HeartbeatError("sequence must be a positive integer")
    for name, value in (
        ("asset_key", asset_key),
        ("registry_address", registry_address),
    ):
        if not isinstance(value, str) or not value:
            raise HeartbeatError(f"{name} must be a non-empty string")
    return {
        "asset_key": asset_key,
        "expires_at": _stamp(written_at + timedelta(seconds=expiry)),
        "last_attempted_slot": last_attempted_slot,
        "last_backup_at": last_backup_at,
        "last_successful_epoch": last_successful_epoch,
        "next_scheduled_slot": next_scheduled_slot,
        "process_id": os.getpid() if process_id is None else process_id,
        "process_identity": process_identity(process_id),
        "registry_address": registry_address,
        "runway_checked_at": runway_checked_at,
        "sequence": sequence,
        "version": HEARTBEAT_VERSION,
        "written_at": _stamp(written_at),
    }


def write(path: str | os.PathLike[str], record: Mapping[str, object]) -> None:
    """Replace the heartbeat in one step, or leave the previous one intact.

    A half-written heartbeat is worse than a stale one: the stale one expires and is
    correctly called dead, while the truncated one is unreadable and has to be guessed at.
    """
    target = Path(path).resolve()
    frozen = frozen_snapshot(record, "heartbeat")
    if set(frozen) != _FIELDS:
        raise HeartbeatError("heartbeat fields must be exactly the documented set")
    temporary = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as output:
            output.write(canonical_json_bytes(dict(frozen)) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except OSError as error:
        raise HeartbeatError(f"the heartbeat cannot be written: {error}") from error


def read(path: str | os.PathLike[str]) -> Mapping[str, object]:
    """Read one heartbeat, or say precisely why what is there is not one."""
    target = Path(path).resolve()
    try:
        raw = target.read_bytes()
    except FileNotFoundError as error:
        raise HeartbeatError("no heartbeat has been written") from error
    except OSError as error:
        raise HeartbeatError(f"the heartbeat cannot be read: {error}") from error
    try:
        value = strict_json_loads(raw)
    except (TypeError, ValueError) as error:
        raise HeartbeatError(f"the heartbeat is not strict JSON: {error}") from error
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise HeartbeatError("heartbeat fields must be exactly the documented set")
    if value["version"] != HEARTBEAT_VERSION:
        raise HeartbeatError("heartbeat version is not supported")
    return value


def verify(
    path: str | os.PathLike[str],
    *,
    now: datetime,
    asset_key: str,
    registry_address: str,
    previous_sequence: int | None = None,
    due_slot: datetime | None = None,
) -> Health:
    """Decide, at read time, whether the daemon is alive and whether epochs are healthy.

    Nothing here trusts a stored verdict, because none is stored. The record supplies an
    identity and a window; this compares them against the reader's own clock and the
    deployment the reader believes it is looking at.
    """
    moment = utc_instant(now, "now")
    try:
        record = read(path)
    except HeartbeatError as error:
        return Health(False, False, (str(error),), None)

    reasons: list[str] = []
    try:
        written_at = _parse(record["written_at"], "written_at")
        expires_at = _parse(record["expires_at"], "expires_at")
    except HeartbeatError as error:
        return Health(False, False, (str(error),), record)

    if record["asset_key"] != asset_key:
        reasons.append("this heartbeat describes a different asset")
    if record["registry_address"] != registry_address:
        reasons.append("this heartbeat describes a different deployment")
    if written_at > moment:
        # Not pedantry: a future write time makes the expiry window unfalsifiable, so a
        # clock that jumped forward once would keep the heartbeat green indefinitely.
        reasons.append("the heartbeat was written in the future")
    if expires_at <= moment:
        reasons.append("the heartbeat has expired")
    if expires_at <= written_at:
        reasons.append("the heartbeat expires no later than it was written")
    if record["process_identity"].endswith(":unknown"):
        reasons.append("the process identity could not be established on this platform")
    if previous_sequence is not None and record["sequence"] <= previous_sequence:
        # A sequence that stalls or goes backwards means the reader is looking at an older
        # record than it already saw — a restored backup, or a second daemon writing here.
        reasons.append("the heartbeat sequence did not advance")

    daemon_alive = not reasons
    epoch_reasons: list[str] = []
    if due_slot is not None:
        # Bound to *this* slot, not to any epoch ever recorded. The first version asked
        # only whether the fields were truthy, so a two-day-old attempt marked today's
        # overdue slot healthy — which is precisely the silence the check exists to break.
        due = utc_instant(due_slot, "due_slot")
        latest = _latest_epoch(record)
        if latest is None:
            epoch_reasons.append("a scheduled slot is due with no epoch recorded")
        elif latest < due:
            epoch_reasons.append(
                f"the newest epoch is {_stamp(latest)}, older than the slot due at "
                f"{_stamp(due)}"
            )
    return Health(
        daemon_alive=daemon_alive,
        epoch_healthy=daemon_alive and not epoch_reasons,
        reasons=tuple(reasons + epoch_reasons),
        record=record,
    )


def _latest_epoch(record: Mapping[str, object]) -> datetime | None:
    """The newest instant this heartbeat claims an epoch happened at, if any is readable.

    A malformed field is treated as absent rather than raising: this is a health check, and
    a record it cannot parse is a record that establishes nothing — which is the unhealthy
    answer, not an exception for the caller to handle.
    """
    latest: datetime | None = None
    for field in ("last_successful_epoch", "last_attempted_slot"):
        value = record.get(field)
        if not isinstance(value, str):
            continue
        try:
            parsed = _parse(value, field)
        except HeartbeatError:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HeartbeatError(f"{field} must be a normalized UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as error:
        raise HeartbeatError(f"{field} must be a normalized UTC timestamp") from error
