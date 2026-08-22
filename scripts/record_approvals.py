"""Append new signed decisions to a version-2 approval ledger.

The decisions file is an unsigned review input, never an approval artifact. ``--review``
resolves every named control to an accepted compiler candidate and prints exactly what a
later signing run would add. ``--sign`` repeats those checks, creates and verifies every
EIP-712 approval at the current instant, validates the complete candidate ledger, and only
then atomically replaces the ledger. Existing decisions are immutable: a control id already
present anywhere in the ledger can never be added again through this command.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sign_approvals import (  # noqa: E402
    APPROVER_KEY_ENV,
    _proposed_digest,
)
from touchstone.approval import (  # noqa: E402
    APPROVED_KEY,
    APPROVAL_SCOPE_GLOBAL,
    COMPILATIONS,
    DECLINED_KEY,
    LEDGER,
    LEDGER_VERSION,
    ApprovalError,
    load_approval_ledger,
    sign_approval,
    verify_signed_approval,
)


DECISIONS_VERSION = "touchstone.approval-decisions.v1"


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ApprovalError(f"{label} does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ApprovalError(f"{label} is not readable JSON: {error}") from error


def decisions(
    path: Path, ledger: dict[str, list], *, compilations: Path
) -> list[dict[str, str]]:
    value = _read_json(path, "the decisions file")
    if not isinstance(value, dict) or set(value) != {"version", "decisions"}:
        raise ApprovalError(
            "the decisions file must contain exactly version and decisions"
        )
    if value["version"] != DECISIONS_VERSION:
        raise ApprovalError("the decisions file is not a supported version")
    supplied = value["decisions"]
    if not isinstance(supplied, list) or not supplied:
        raise ApprovalError("the decisions file must contain at least one decision")

    existing_ids = {
        entry.get("control_id")
        for key in (APPROVED_KEY, DECLINED_KEY)
        for entry in ledger[key]
    }
    added_ids: set[str] = set()
    rows = []
    for index, row in enumerate(supplied, start=1):
        if not isinstance(row, dict):
            raise ApprovalError(f"decision {index} must be a mapping")
        decision = row.get("decision")
        required = {"decision", "control_id", "compilation_sha256"}
        if decision == DECLINED_KEY:
            required.add("reason")
        if set(row) != required:
            raise ApprovalError(
                f"decision {index} must contain exactly " + ", ".join(sorted(required))
            )
        if decision not in {APPROVED_KEY, DECLINED_KEY}:
            raise ApprovalError(f"decision {index} must be approved or declined")
        control_id = row["control_id"]
        digest = row["compilation_sha256"]
        if not isinstance(control_id, str) or not control_id:
            raise ApprovalError(
                f"decision {index} control_id must be a non-empty string"
            )
        if control_id in existing_ids:
            raise ApprovalError(
                f"{control_id!r} already has a recorded decision; existing decisions "
                "cannot be replaced or flipped"
            )
        if control_id in added_ids:
            raise ApprovalError(
                f"{control_id!r} appears more than once in the decisions file"
            )
        if not isinstance(digest, str):
            raise ApprovalError(
                f"decision {index} compilation_sha256 must be a lowercase SHA-256 digest"
            )
        reason = row.get("reason")
        if decision == DECLINED_KEY and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise ApprovalError(f"decision {index} decline reason must be non-empty")
        try:
            control_digest = _proposed_digest(
                digest, control_id, directory=compilations
            )
        except SystemExit as error:
            raise ApprovalError(str(error)) from error
        resolved = {
            "decision": decision,
            "control_id": control_id,
            "compilation_sha256": digest,
            "control_digest": control_digest,
        }
        if reason is not None:
            resolved["reason"] = reason
        rows.append(resolved)
        added_ids.add(control_id)
    return rows


def review(rows: list[dict[str, str]], ledger: Path) -> None:
    noun = "decision" if len(rows) == 1 else "decisions"
    print(f"{len(rows)} new {noun} will be recorded in {ledger} after signing:\n")
    for row in rows:
        verb = "APPROVE" if row["decision"] == APPROVED_KEY else "DECLINE"
        print(f"  {verb:<9} {row['control_id']}")
        print(f"            compilation {row['compilation_sha256']}")
        print(f"            proposal    {row['control_digest']}")
        if "reason" in row:
            print(f"            reason: {row['reason']}")
    print(
        "\nNothing signed or written. Run the same command with --sign only after "
        "confirming every row."
    )


def _sign_rows(rows: list[dict[str, str]], key: str, timestamp: int) -> list[dict]:
    entries = []
    approver = None
    decided_on = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
    for row in rows:
        decision = row["decision"]
        approval = sign_approval(
            key,
            control_digest=row["control_digest"],
            compilation_digest=row["compilation_sha256"],
            decision=decision,
            reason_code=(
                "operator-approved" if decision == APPROVED_KEY else "operator-declined"
            ),
            timestamp=timestamp,
        )
        recovered = verify_signed_approval(
            approval,
            expected_decision=decision,
            expected_scope=APPROVAL_SCOPE_GLOBAL,
        )
        if approver is None:
            approver = recovered
        elif recovered != approver:
            raise ApprovalError("recovered approver changed during one recording act")
        date_field = "approved_on" if decision == APPROVED_KEY else "declined_on"
        entry = {
            "control_id": row["control_id"],
            "compilation_sha256": row["compilation_sha256"],
            date_field: decided_on,
        }
        if "reason" in row:
            entry["reason"] = row["reason"]
        entry["approval"] = approval
        entries.append(entry)
    return entries


def _signatures(ledger: dict[str, list], lengths: dict[str, int]) -> list[str]:
    return [
        entry["approval"]["signature"]
        for key in (APPROVED_KEY, DECLINED_KEY)
        for entry in ledger[key][: lengths[key]]
    ]


def record(
    rows: list[dict[str, str]],
    *,
    ledger_path: Path,
    compilations: Path,
    key: str,
) -> tuple[int, str]:
    original_bytes = ledger_path.read_bytes()
    original = load_approval_ledger(ledger_path, directory=compilations)
    if original.get("version") != LEDGER_VERSION:
        raise ApprovalError("new decisions can only be recorded in a version-2 ledger")
    existing_ids = {
        entry["control_id"]
        for decision in (APPROVED_KEY, DECLINED_KEY)
        for entry in original[decision]
    }
    repeated = sorted(
        row["control_id"] for row in rows if row["control_id"] in existing_ids
    )
    if repeated:
        raise ApprovalError(
            f"{repeated[0]!r} acquired a recorded decision during signing; nothing written"
        )
    lengths = {key: len(original[key]) for key in (APPROVED_KEY, DECLINED_KEY)}
    before_signatures = _signatures(original, lengths)
    timestamp = int(time.time())
    newest_existing = max(
        (
            entry["approval"]["timestamp"]
            for decision in (APPROVED_KEY, DECLINED_KEY)
            for entry in original[decision]
        ),
        default=0,
    )
    if timestamp <= newest_existing:
        raise ApprovalError(
            "the signing clock is not later than the newest recorded signature; "
            "backdating is forbidden"
        )
    signed_entries = _sign_rows(rows, key, timestamp)

    candidate = deepcopy(original)
    for row, entry in zip(rows, signed_entries, strict=True):
        candidate[row["decision"]].append(entry)
    if candidate[APPROVED_KEY][: lengths[APPROVED_KEY]] != original[APPROVED_KEY]:
        raise ApprovalError("existing approved decisions changed while appending")
    if candidate[DECLINED_KEY][: lengths[DECLINED_KEY]] != original[DECLINED_KEY]:
        raise ApprovalError("existing declined decisions changed while appending")
    if _signatures(candidate, lengths) != before_signatures:
        raise ApprovalError("an existing approval signature changed while appending")

    raw = (json.dumps(candidate, indent=2) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=ledger_path.parent,
            prefix=f".{ledger_path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        validated = load_approval_ledger(temporary, directory=compilations)
        if _signatures(validated, lengths) != before_signatures:
            raise ApprovalError("validation changed an existing approval signature")
        if ledger_path.read_bytes() != original_bytes:
            raise ApprovalError(
                "the approval ledger changed during signing; nothing written"
            )
        os.replace(temporary, ledger_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    final = load_approval_ledger(ledger_path, directory=compilations)
    if _signatures(final, lengths) != before_signatures:
        raise ApprovalError("an existing approval signature changed after writing")
    return timestamp, signed_entries[0]["approval"]["approver"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review", action="store_true", help="print additions, sign nothing"
    )
    parser.add_argument(
        "--sign", action="store_true", help="sign and append all additions"
    )
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--compilations", type=Path, default=COMPILATIONS)
    arguments = parser.parse_args(argv)
    if arguments.review == arguments.sign:
        parser.error("exactly one of --review or --sign")

    try:
        ledger = load_approval_ledger(
            arguments.ledger, directory=arguments.compilations
        )
        rows = decisions(
            arguments.decisions, ledger, compilations=arguments.compilations
        )
        if arguments.review:
            review(rows, arguments.ledger)
            return 0
        key = os.environ.get(APPROVER_KEY_ENV)
        if not key:
            raise ApprovalError(f"{APPROVER_KEY_ENV} is not set")
        timestamp, approver = record(
            rows,
            ledger_path=arguments.ledger,
            compilations=arguments.compilations,
            key=key,
        )
    except (ApprovalError, OSError, ValueError) as error:
        print(f"RECORD FAIL: {error}", file=sys.stderr)
        return 1
    print(f"recorded {len(rows)} signed decisions as {approver} at {timestamp}")
    print(f"ledger written to {arguments.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
