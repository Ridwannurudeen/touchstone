"""Derive measured slot metrics from one workspace without merging histories."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.incidents import IncidentLog  # noqa: E402
from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402


_EPOCH_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})$")
_MISSED_COUNT = re.compile(r"^(\d+) slot\(s\)")


def summarize(
    entries: list[dict[str, object]],
    incidents: list[dict[str, object]],
    *,
    start: date,
    through: date,
) -> dict[str, object]:
    if through < start:
        raise ValueError("through must not precede start")
    scheduled = (through - start).days + 1
    completed_epochs: set[str] = set()
    corrected = 0
    for entry in entries:
        signed_report = entry.get("signed_report")
        report = signed_report.get("report") if isinstance(signed_report, dict) else None
        if not isinstance(report, dict):
            raise ValueError("transparency entry has no report")
        epoch_id = report.get("epoch_id")
        if not isinstance(epoch_id, str):
            raise ValueError("transparency report has no epoch_id")
        match = _EPOCH_DATE.search(epoch_id)
        if match is None:
            raise ValueError(f"epoch_id has no ISO date suffix: {epoch_id}")
        epoch_date = date.fromisoformat(match.group(1))
        if not start <= epoch_date <= through:
            continue
        if report.get("correction_of") is None:
            completed_epochs.add(epoch_id)
        else:
            corrected += 1

    missed = 0
    for incident in incidents:
        if incident.get("kind") != "SLOT_MISSED":
            continue
        occurred = incident.get("occurred_at")
        if not isinstance(occurred, str) or not occurred[:10]:
            raise ValueError("slot-missed incident has no timestamp")
        occurred_on = date.fromisoformat(occurred[:10])
        if not start <= occurred_on <= through:
            continue
        detail = incident.get("detail")
        if not isinstance(detail, str):
            raise ValueError("slot-missed incident has no detail")
        match = _MISSED_COUNT.match(detail)
        missed += int(match.group(1)) if match else 1

    completed = len(completed_epochs)
    return {
        "window_start": start.isoformat(),
        "window_through": through.isoformat(),
        "scheduled_slots": scheduled,
        "completed_slots": completed,
        "missed_slots": missed,
        "corrected_publications": corrected,
        "unaccounted_slots": max(0, scheduled - completed - missed),
    }


def load_workspace(workspace: Path, *, start: date, through: date) -> dict[str, object]:
    entries = TransparencyLog(workspace / "transparency.jsonl").verify()
    incidents = IncidentLog(workspace / "incidents.jsonl").verify()
    metrics = summarize(entries, incidents, start=start, through=through)
    metrics["workspace"] = str(workspace.resolve())
    heartbeat = workspace / "heartbeat.json"
    if heartbeat.is_file():
        value = strict_json_loads(heartbeat.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("heartbeat must be an object")
        metrics["heartbeat"] = {
            "written_at": value.get("written_at"),
            "last_attempted_slot": value.get("last_attempted_slot"),
            "next_scheduled_slot": value.get("next_scheduled_slot"),
        }
    else:
        metrics["heartbeat"] = None
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", action="append", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--through", required=True)
    parser.add_argument("--out", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        start = date.fromisoformat(arguments.start)
        through = date.fromisoformat(arguments.through)
        metrics = [
            load_workspace(path, start=start, through=through)
            for path in arguments.workspace
        ]
        payload = {
            "schema": "touchstone.operations-metrics.v1",
            "histories": metrics,
            "note": "Each workspace is measured independently; hash chains are never concatenated.",
        }
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"OPERATIONS METRICS FAIL: {error}", file=sys.stderr)
        return 1
    print(f"wrote {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
