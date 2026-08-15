"""An outside opinion about whether the service is doing its job.

The daemon cannot be the thing that reports the daemon is dead, so this runs as a separate
process and reads only what the daemon leaves behind. It never takes the workspace lock and
never writes into the workspace it is watching: a watchdog that mutates the thing it
observes is a second writer, and this project already refuses second writers everywhere
else for the same reason.

It answers a narrower question than "is everything fine". It answers: is the daemon alive,
does the durable record still verify, and is the publication state coherent. Anything it
cannot establish is reported as unhealthy rather than assumed well — an indeterminate
watchdog that stays quiet is indistinguishable from a working one, and only one of those is
worth having.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from touchstone.heartbeat import Health, verify as verify_heartbeat
from touchstone.incidents import IncidentLog, IncidentLogError
from touchstone.operations import OperationsError, OperationsStore
from touchstone.quantities import utc_instant
from touchstone.signing import strict_json_loads
from touchstone.translog import TransparencyLog, TransparencyLogError
from touchstone.workspace import Workspace


@dataclass(frozen=True, slots=True)
class Check:
    """One named question and what answering it produced."""

    name: str
    healthy: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Report:
    """Every check, and the single verdict a supervisor acts on."""

    checks: tuple[Check, ...]
    health: Health | None

    @property
    def healthy(self) -> bool:
        return all(check.healthy for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.healthy)

    @property
    def exit_code(self) -> int:
        """Nonzero for unhealthy *and* for indeterminate. Silence must mean healthy."""
        return 0 if self.healthy else 1


def inspect(
    workspace: str | Path,
    *,
    now: datetime,
    asset_key: str,
    registry_address: str,
    previous_sequence: int | None = None,
    slot_overdue: bool = False,
) -> Report:
    """Read the workspace from outside and judge it, without writing anything to it."""
    moment = utc_instant(now, "now")
    root = Workspace(workspace)
    checks: list[Check] = []

    health = verify_heartbeat(
        root.heartbeat,
        now=moment,
        asset_key=asset_key,
        registry_address=registry_address,
        previous_sequence=previous_sequence,
        slot_overdue=slot_overdue,
    )
    checks.append(
        Check(
            "heartbeat",
            health.daemon_alive,
            "the daemon is writing heartbeats"
            if health.daemon_alive
            else "; ".join(health.reasons),
        )
    )
    checks.append(
        Check(
            "epoch",
            health.epoch_healthy,
            "epochs are current"
            if health.epoch_healthy
            else "; ".join(health.reasons) or "no epoch has been recorded",
        )
    )

    checks.append(_verified(root.transparency_log))
    checks.append(_incidents(root.incidents))
    checks.append(_publication_coherent(root))
    return Report(tuple(checks), health)


def _verified(path: Path) -> Check:
    """The transparency log still proves its own chain."""
    try:
        entries = TransparencyLog(path).verify()
    except TransparencyLogError as error:
        return Check("transparency-log", False, str(error))
    return Check("transparency-log", True, f"{len(entries)} entries verify")


def _incidents(path: Path) -> Check:
    try:
        entries = IncidentLog(path).verify()
    except (IncidentLogError, ValueError) as error:
        return Check("incident-log", False, str(error))
    open_count = sum(1 for entry in entries if entry["closes"] is None) - sum(
        1 for entry in entries if entry["closes"] is not None
    )
    return Check(
        "incident-log", True, f"{len(entries)} entries verify, {open_count} open"
    )


def _publication_coherent(root: Workspace) -> Check:
    """A journalled transaction with no operation behind it is the dangerous state.

    The journal says a transaction was broadcast; the operation says what it was meant to
    publish. One without the other means a restart cannot tell whether the chain already
    holds this report, which is exactly the position from which a service publishes twice.
    """
    journal = root.pending_journal
    try:
        pending = strict_json_loads(journal.read_bytes()) if journal.exists() else None
    except (OSError, TypeError, ValueError) as error:
        return Check(
            "publication", False, f"the pending journal cannot be read: {error}"
        )
    try:
        operation = OperationsStore(root.operations).load_operation()
    except (OperationsError, ValueError) as error:
        return Check("publication", False, f"the operation cannot be read: {error}")

    if pending is not None and operation is None:
        return Check(
            "publication",
            False,
            "a transaction is journalled with no operation describing it",
        )
    if pending is None and operation is not None:
        # Legitimate and expected: the operation is written before the broadcast. It is
        # reported rather than hidden, because an operation that stays here across several
        # checks is a slot that never completed.
        return Check("publication", True, "an operation is open, nothing broadcast yet")
    if pending is None:
        return Check("publication", True, "nothing in flight")
    return Check("publication", True, "a broadcast transaction has its operation")


def render(report: Report) -> str:
    """One line per check, so a supervisor journal shows what was actually asked."""
    lines = [
        f"{'ok  ' if check.healthy else 'FAIL'}  {check.name}: {check.detail}"
        for check in report.checks
    ]
    lines.append("HEALTHY" if report.healthy else "UNHEALTHY")
    return "\n".join(lines)


def restart_command(argv: Sequence[str]) -> tuple[str, ...]:
    """Validate a restart command as an exact argument vector.

    Never a shell string. The watchdog runs unattended with the ability to start
    processes, and a shell string is an injection surface that a fixed argv simply does not
    have — there is nothing for a crafted workspace path or asset key to escape into.
    """
    # A string is itself a sequence, and `tuple("systemctl restart")` is a vector of
    # perfectly valid one-character strings. Accepting it would pass the shell command
    # straight through the check written to refuse it, one letter per argument.
    if isinstance(argv, (str, bytes)):
        raise ValueError(
            "a restart command must be a vector of strings, not one shell string"
        )
    command = tuple(argv)
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("a restart command must be a non-empty vector of strings")
    return command
