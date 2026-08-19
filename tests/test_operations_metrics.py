"""Slot metrics count reports without treating corrections as new slots."""

from datetime import date

from scripts.build_operations_metrics import summarize


def _entry(epoch_id: str, correction_of: int | None = None) -> dict[str, object]:
    return {
        "signed_report": {
            "report": {"epoch_id": epoch_id, "correction_of": correction_of}
        }
    }


def test_metrics_separate_completed_missed_and_corrected_slots() -> None:
    entries = [
        _entry("ustb-2026-08-17"),
        _entry("ustb-2026-08-17", correction_of=1),
        _entry("ustb-2026-08-18"),
    ]
    incidents = [
        {
            "kind": "SLOT_MISSED",
            "occurred_at": "2026-08-19T01:00:00Z",
            "detail": "the slot scheduled for 2026-08-19 did not run",
        }
    ]

    assert summarize(
        entries,
        incidents,
        start=date(2026, 8, 17),
        through=date(2026, 8, 19),
    ) == {
        "window_start": "2026-08-17",
        "window_through": "2026-08-19",
        "scheduled_slots": 3,
        "completed_slots": 2,
        "missed_slots": 1,
        "corrected_publications": 1,
        "unaccounted_slots": 0,
    }
