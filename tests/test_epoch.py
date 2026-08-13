import json
from datetime import date, datetime, timezone
from pathlib import Path

from touchstone.controls import AssetState, EvaluationResult
from touchstone.epoch import FixtureTransport, run_ustb_epoch
from touchstone.evidence import EvidenceStore
from touchstone.sources import USTB_SOURCES


FIXTURES = Path(__file__).parents[1] / "fixtures"
RETRIEVED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)


def test_golden_fixture_epoch_runs_the_complete_offline_vertical(tmp_path: Path) -> None:
    transport = FixtureTransport(FIXTURES)

    report = run_ustb_epoch(
        transport=transport,
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 13),
        retrieved_at=RETRIEVED_AT,
    )

    assert report.asset_key == "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
    assert report.now == date(2026, 8, 13)
    assert report.state is AssetState.CONFIRMED
    assert report.evidence_deadline == date(2026, 8, 13)
    assert len(report.sources) == 3
    assert [source.source_id for source in report.sources] == [
        source.source_id for source in USTB_SOURCES
    ]
    assert all(source.content_type == "application/json" for source in report.sources)
    assert len(report.evaluations) == 5
    assert all(
        evaluation.result is EvaluationResult.SATISFIED
        for evaluation in report.evaluations
    )
    assert transport.calls == [source.url for source in USTB_SOURCES]
    assert EvidenceStore(tmp_path).verify() == 3


def test_epoch_report_mapping_is_stable_json_data(tmp_path: Path) -> None:
    report = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES),
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
    )

    mapping = report.to_mapping()
    assert mapping["state"] == "STALE"
    assert mapping["now"] == "2026-08-14"
    assert mapping["evidence_deadline"] == "2026-08-13"
    assert json.loads(json.dumps(mapping, allow_nan=False)) == mapping
    stale = {
        result["control_id"]
        for result in mapping["evaluations"]
        if result["result"] == "UNEVALUABLE"
    }
    assert stale == {"nav-row-freshness", "yield-freshness"}
