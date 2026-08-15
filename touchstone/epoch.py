"""USTB epoch orchestration for offline fixtures and explicit live retrieval."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile

from touchstone.controls import AssetState, EvaluationResult
from touchstone.evidence import read_object, CaptureRecord, EvidenceStore
from touchstone.evaluate import USTB_ASSET_KEY, default_ustb_controls, evaluate_ustb
from touchstone.normalize.ustb import (
    USTB_NAV_SOURCE_ID,
    USTBObservation,
    normalize_ustb_payload,
)
from touchstone.sources import (
    USTB_SOURCE_BY_ID,
    USTB_SOURCES,
    FetchResult,
    LiveTransport,
    Transport,
    TransportResponse,
    fetch_source,
)


FIXTURE_CAPTURES = {
    date(2026, 8, 13): {
        "superstate-ustb-nav-daily": "ustb-nav.json",
        "superstate-ustb-yield": "ustb-yield.json",
        "superstate-ustb-holdings": "ustb-holdings.json",
    },
    date(2026, 8, 14): {
        "superstate-ustb-nav-daily": "ustb-nav-20260814.json",
        "superstate-ustb-yield": "ustb-yield-20260814.json",
        "superstate-ustb-holdings": "ustb-holdings.json",
    },
}
FIXTURE_RETRIEVED_AT = {
    date(2026, 8, 13): datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc),
    date(2026, 8, 14): datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc),
}
_FIXTURE_CONFIRMATION = date(2026, 8, 13)
_FIXTURE_NOW = date(2026, 8, 14)


@dataclass(frozen=True, slots=True)
class EpochSourceReport:
    source_id: str
    source_url: str
    content_type: str
    byte_size: int
    evidence_sha256: str
    retrieved_at: datetime
    observed_on: date


@dataclass(frozen=True, slots=True)
class EpochControlReport:
    control_id: str
    result: EvaluationResult
    observed_value: str | None
    evidence_deadline: date | None
    observed_on: date | None


@dataclass(frozen=True, slots=True)
class USTBEpochReport:
    asset_key: str
    now: date
    state: AssetState
    evidence_deadline: date
    sources: tuple[EpochSourceReport, ...]
    evaluations: tuple[EpochControlReport, ...]
    confirmation: CaptureRecord | None

    def to_mapping(self) -> dict[str, object]:
        return {
            "asset_key": self.asset_key,
            "confirmation": (
                {
                    "retrieved_at": self.confirmation.retrieved_at.isoformat(),
                    "sha256": self.confirmation.sha256,
                    "source_id": self.confirmation.source_id,
                }
                if self.confirmation is not None
                else None
            ),
            "evaluations": [
                {
                    "control_id": evaluation.control_id,
                    "evidence_deadline": (
                        evaluation.evidence_deadline.isoformat()
                        if evaluation.evidence_deadline is not None
                        else None
                    ),
                    "observed_on": (
                        evaluation.observed_on.isoformat()
                        if evaluation.observed_on is not None
                        else None
                    ),
                    "observed_value": evaluation.observed_value,
                    "result": evaluation.result.value,
                }
                for evaluation in self.evaluations
            ],
            "evidence_deadline": self.evidence_deadline.isoformat(),
            "now": self.now.isoformat(),
            "sources": [
                {
                    "byte_size": source.byte_size,
                    "content_type": source.content_type,
                    "evidence_sha256": source.evidence_sha256,
                    "observed_on": source.observed_on.isoformat(),
                    "retrieved_at": source.retrieved_at.isoformat(),
                    "source_id": source.source_id,
                    "source_url": source.source_url,
                }
                for source in self.sources
            ],
            "state": self.state.value,
        }


class FixtureTransport:
    """Offline transport serving one dated capture of committed fixture bytes."""

    def __init__(
        self, fixtures_dir: str | Path, capture: date = _FIXTURE_CONFIRMATION
    ) -> None:
        if capture not in FIXTURE_CAPTURES:
            raise ValueError(f"no committed fixture capture for {capture}")
        self.fixtures_dir = Path(fixtures_dir).resolve()
        self.capture = capture
        self.calls: list[str] = []
        self._paths = {
            source.url: self.fixtures_dir / FIXTURE_CAPTURES[capture][source.source_id]
            for source in USTB_SOURCES
        }

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        del timeout
        self.calls.append(url)
        path = self._paths.get(url)
        if path is None:
            raise ValueError("fixture transport received an unregistered URL")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise ValueError("committed fixture exceeds source manifest byte cap")
        return TransportResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=raw,
        )


def run_ustb_epoch(
    *,
    transport: Transport,
    store: EvidenceStore,
    now: date,
    retrieved_at: datetime,
) -> USTBEpochReport:
    """Fetch, store, isolate-normalize, evaluate, and transition one USTB epoch.

    The NAV predecessor is resolved before this epoch's own capture is appended, so the
    current fetch can never confirm itself.
    """
    nav_manifest = USTB_SOURCE_BY_ID[USTB_NAV_SOURCE_ID]
    confirmation = store.confirmation_capture(USTB_NAV_SOURCE_ID, before=retrieved_at)
    prior_observations: dict[str, USTBObservation] = {}
    if confirmation is not None:
        prior_observations[USTB_NAV_SOURCE_ID] = normalize_ustb_payload(
            USTB_NAV_SOURCE_ID,
            read_object(store, confirmation.sha256),
            max_bytes=nav_manifest.max_bytes,
            isolated=True,
        )

    fetches: list[FetchResult] = []
    observations: dict[str, USTBObservation] = {}
    for manifest in USTB_SOURCES:
        fetched = fetch_source(
            manifest.source_id,
            store=store,
            transport=transport,
            retrieved_at=retrieved_at,
        )
        fetches.append(fetched)
        raw = read_object(store, fetched.evidence_sha256)
        observations[manifest.source_id] = normalize_ustb_payload(
            manifest.source_id,
            raw,
            max_bytes=manifest.max_bytes,
            isolated=True,
        )

    evaluation = evaluate_ustb(
        default_ustb_controls(),
        observations,
        prior_observations=prior_observations,
        now=now,
    )
    return USTBEpochReport(
        asset_key=USTB_ASSET_KEY,
        now=now,
        state=evaluation.state,
        evidence_deadline=evaluation.evidence_deadline,
        confirmation=confirmation,
        sources=tuple(
            EpochSourceReport(
                source_id=fetched.source_id,
                source_url=fetched.source_url,
                content_type=fetched.content_type,
                byte_size=fetched.byte_size,
                evidence_sha256=fetched.evidence_sha256,
                retrieved_at=fetched.retrieved_at,
                observed_on=_observation_date(observations[fetched.source_id]),
            )
            for fetched in fetches
        ),
        evaluations=tuple(
            EpochControlReport(
                control_id=item.control_id,
                result=item.result,
                observed_value=(
                    str(item.observed_value)
                    if item.observed_value is not None
                    else None
                ),
                evidence_deadline=item.evidence_deadline,
                observed_on=item.observed_on,
            )
            for item in evaluation.evaluations
        ),
    )


def _observation_date(observation: USTBObservation) -> date:
    rows = getattr(observation, "rows", None)
    if isinstance(rows, tuple) and rows:
        return max(row.observed_on for row in rows)
    return observation.as_of_date


def _print_report(report: USTBEpochReport) -> None:
    print(json.dumps(report.to_mapping(), indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the USTB ingestion epoch")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixtures", action="store_true", help="use committed fixtures")
    mode.add_argument(
        "--live", action="store_true", help="perform allowlisted live GETs"
    )
    args = parser.parse_args(argv)

    if args.fixtures:
        fixtures = Path(__file__).parents[1] / "fixtures"
        with tempfile.TemporaryDirectory(prefix="touchstone-epoch-") as root:
            store = EvidenceStore(root)
            run_ustb_epoch(
                transport=FixtureTransport(fixtures, _FIXTURE_CONFIRMATION),
                store=store,
                now=_FIXTURE_CONFIRMATION,
                retrieved_at=FIXTURE_RETRIEVED_AT[_FIXTURE_CONFIRMATION],
            )
            report = run_ustb_epoch(
                transport=FixtureTransport(fixtures, _FIXTURE_NOW),
                store=store,
                now=_FIXTURE_NOW,
                retrieved_at=FIXTURE_RETRIEVED_AT[_FIXTURE_NOW],
            )
    else:
        retrieved_at = datetime.now(timezone.utc)
        report = run_ustb_epoch(
            transport=LiveTransport(),
            store=EvidenceStore(),
            now=retrieved_at.date(),
            retrieved_at=retrieved_at,
        )
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
