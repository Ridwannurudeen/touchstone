import json
from datetime import date, datetime, timezone
from pathlib import Path

from touchstone.controls import AssetState, EvaluationResult
from touchstone.epoch import FixtureTransport, run_ustb_epoch
from touchstone.evidence import EvidenceStore
from touchstone.sources import USTB_SOURCES


FIXTURES = Path(__file__).parents[1] / "fixtures"
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)


def seed_confirmation(store: EvidenceStore) -> None:
    run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
    )


def test_first_epoch_abstains_on_values_with_no_predecessor(tmp_path: Path) -> None:
    report = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
    )
    results = {item.control_id: item for item in report.evaluations}

    assert report.confirmation is None
    assert report.state is AssetState.UNVERIFIABLE
    assert results["aum-published"].result is EvaluationResult.UNEVALUABLE
    assert results["value-vs-expected"].result is EvaluationResult.UNEVALUABLE
    assert results["nav-row-freshness"].result is EvaluationResult.SATISFIED


def test_golden_fixture_epoch_runs_the_complete_offline_vertical(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)
    seed_confirmation(store)
    transport = FixtureTransport(FIXTURES, date(2026, 8, 14))

    report = run_ustb_epoch(
        transport=transport,
        store=store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
    )

    assert report.asset_key == "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
    assert report.now == date(2026, 8, 14)
    assert report.state is AssetState.CONFIRMED
    assert report.evidence_deadline == date(2026, 8, 16)
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
    assert EvidenceStore(tmp_path).verify() == 6


def test_epoch_binds_the_confirmation_capture_it_evaluated_against(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)
    seed_confirmation(store)

    report = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
    )
    values = {
        item.control_id: item
        for item in report.evaluations
        if item.control_id in {"aum-published", "value-vs-expected"}
    }

    assert report.confirmation is not None
    assert report.confirmation.source_id == "superstate-ustb-nav-daily"
    assert report.confirmation.retrieved_at == CONFIRMED_AT
    assert report.confirmation.sha256 == (
        "4830bc348b621f70682cd41c0d48484987b6b5f3c1a99193e0ca33e7ccba3a25"
    )
    assert {item.observed_on for item in values.values()} == {date(2026, 8, 11)}


def test_epoch_report_mapping_is_stable_json_data(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    seed_confirmation(store)

    report = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=store,
        now=date(2026, 8, 17),
        retrieved_at=RETRIEVED_AT,
    )

    mapping = report.to_mapping()
    assert mapping["state"] == "STALE"
    assert mapping["now"] == "2026-08-17"
    assert mapping["evidence_deadline"] == "2026-08-16"
    assert json.loads(json.dumps(mapping, allow_nan=False)) == mapping
    stale = {
        result["control_id"]
        for result in mapping["evaluations"]
        if result["result"] == "UNEVALUABLE"
    }
    assert stale == {"nav-row-freshness"}
    assert mapping["confirmation"]["source_id"] == "superstate-ustb-nav-daily"
