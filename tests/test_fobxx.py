"""Fixture and hostile-input tests for the FOBXX SEC source path."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

from touchstone.approval import LEDGER_VERSION_V1, ledger_bytes
from touchstone.assets import (
    FOBXX,
    FOBXX_ASSET_KEY,
    FOBXX_EVIDENCE_IDENTITY,
    USTB,
    get_asset,
)
from touchstone.controls import (
    AssetState,
    ComparisonOperator,
    ControlRecord,
    EvaluationResult,
)
from touchstone.compiler import (
    CompilationStatus,
    DeterministicFixtureProvider,
    compile_evidence,
)
from touchstone.epoch import run_epoch
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import evaluate, supports
from touchstone.incidents import IncidentLog
from touchstone.normalize.fobxx import (
    FOBXX_CIK,
    FOBXX_HISTORY_SOURCE_ID,
    FOBXX_LOOKUP_SOURCE_ID,
    FOBXX_SERIES_ID,
    FobxxNormalizationError,
    FobxxObservation,
    FobxxPriceHistoryObservation,
    FobxxProductLookupObservation,
    FobxxSubmissionsObservation,
    parse_price_history,
    parse_product_lookup,
    parse_nmfp3,
    parse_submissions,
)
from touchstone.operations import OperationsStore
from touchstone.publish import PublisherClient, asset_key_bytes
from touchstone.report import FOBXX_LIMITATIONS, USTB_LIMITATIONS
from touchstone.signing import Ed25519Signer
from touchstone.sources import TransportResponse
from touchstone.translog import TransparencyLog
from touchstone.ustb_daemon import (
    EpochProductionError,
    epoch_id_for,
    make_producer,
    report_uri,
    write_bundle,
)
from touchstone.verify import verify_bundle
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from run_service import Service, serve  # noqa: E402
from test_publish import FakeBackend  # noqa: E402
from test_service import Clock  # noqa: E402


FIXTURES = Path(__file__).parents[1] / "fixtures"


def fobxx_control(
    *,
    control_id: str,
    source_id: str,
    adapter: str,
    authority: str,
    span: str,
    operator: str,
    expected_value: dict[str, object],
    grace_period: int = 0,
) -> ControlRecord:
    source = FOBXX.source_by_id[source_id]
    return ControlRecord.from_mapping(
        {
            "asset_key": FOBXX.asset_key,
            "control_id": control_id,
            "control_version": 1,
            "predicate_type": "observation",
            "subject": control_id,
            "source_id": source_id,
            "source_authority_class": authority,
            "evidence_span": span,
            "cadence": source.cadence,
            "grace_period": grace_period,
            "observation_adapter": adapter,
            "comparison_operator": operator,
            "expected_value": expected_value,
            "effective_from": "2026-07-31",
            "effective_until": None,
            "compiler_confidence": 1.0,
            "approval_state": "approved",
            "compilation_sha256": "00" * 32,
        }
    )


def compiled_fobxx_controls(tmp_path: Path) -> tuple[str, bytes, bytes]:
    """Fixture-only compilation and legacy ledger for the publication boundary test."""
    retrieved_at = datetime(2026, 8, 22, 2, 20, tzinfo=timezone.utc)
    raw = (FIXTURES / "fobxx-price-history-20260822.json").read_bytes()
    source = FOBXX.source_by_id[FOBXX_HISTORY_SOURCE_ID]
    store = EvidenceStore(tmp_path / "compiler")
    evidence_digest = store.store(
        raw,
        source_id=source.source_id,
        source_url=source.url,
        retrieved_at=retrieved_at,
        declared_mime=source.expected_mime,
    )
    controls = []
    for control in (
        fobxx_control(
            control_id="fobxx-issuer-row-fresh",
            source_id=FOBXX_HISTORY_SOURCE_ID,
            adapter="fobxx-price-history",
            authority="issuer-api",
            span='"navdate":"2026-08-21"',
            operator="fresh_within",
            expected_value={"business_days": 3},
            grace_period=3,
        ),
        fobxx_control(
            control_id="fobxx-issuer-nav-peg",
            source_id=FOBXX_HISTORY_SOURCE_ID,
            adapter="fobxx-price-history",
            authority="issuer-api",
            span='"navstd":"1.00000000"',
            operator="eq",
            expected_value={"field": "nav_std", "value": "1.00000000"},
        ),
        fobxx_control(
            control_id="fobxx-nav-reconciliation",
            source_id=FOBXX_HISTORY_SOURCE_ID,
            adapter="fobxx-price-history",
            authority="issuer-api",
            span='"navstd":"1.00000000"',
            operator="reconciles_with",
            expected_value={
                "field": "nav_std",
                "comparison_source_id": "sec-edgar-fobxx-nmfp3",
                "comparison_field": "stable_price_per_share",
                "tolerance": "0",
            },
        ),
    ):
        candidate = control.to_mapping()
        candidate.update(
            {
                "effective_from": "2026-08-22",
                "approval_state": "proposed",
            }
        )
        candidate.pop("compilation_sha256")
        controls.append(candidate)
    result = compile_evidence(
        DeterministicFixtureProvider(
            json.dumps({"controls": controls}, separators=(",", ":"))
        ),
        evidence_sha256=evidence_digest,
        source_manifest=source,
        store=store,
        retrieved_at=retrieved_at,
        asset=FOBXX,
    )
    assert all(
        outcome.status is CompilationStatus.ACCEPTED for outcome in result.outcomes
    ), [(outcome.status, outcome.reason) for outcome in result.outcomes]
    artifact = (store.objects_dir / result.compilation_sha256).read_bytes()
    ledger = json.dumps(
        {
            "version": LEDGER_VERSION_V1,
            "approved": [
                {
                    "control_id": control["control_id"],
                    "compilation_sha256": result.compilation_sha256,
                }
                for control in controls
            ],
            "declined": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return result.compilation_sha256, artifact, ledger


class FobxxFixtureTransport:
    def __init__(self) -> None:
        self.responses = {
            FOBXX.sources[2].url: TransportResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=(FIXTURES / "fobxx-submissions-20260815.json").read_bytes(),
            ),
            FOBXX.sources[3].url: TransportResponse(
                status_code=200,
                headers={"Content-Type": "text/xml"},
                body=(FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes(),
            ),
        }

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        del timeout, max_bytes
        return self.responses[url]

    def post(
        self, url: str, body: bytes, *, timeout: float, max_bytes: int
    ) -> TransportResponse:
        del timeout, max_bytes
        if url != FOBXX.sources[0].url:
            raise ValueError("fixture transport received an unregistered POST URL")
        if body == FOBXX.sources[0].request_body:
            fixture = "fobxx-product-lookup-20260822.json"
        elif b"PricesHistoryFOBXX" in body:
            fixture = "fobxx-price-history-20260822.json"
        else:
            raise ValueError("fixture transport received an unregistered POST body")
        return TransportResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=(FIXTURES / fixture).read_bytes(),
        )


def _wrong_general_info_cik(raw: bytes) -> bytes:
    start = raw.index(b"<generalInfo>")
    return raw[:start] + raw[start:].replace(
        b"<cik>0001786958</cik>", b"<cik>0000000001</cik>", 1
    )


def test_fobxx_descriptor_and_sec_fixtures_are_registered() -> None:
    assert get_asset(FOBXX_ASSET_KEY) is FOBXX
    assert FOBXX.source_manifest.is_file()
    assert FOBXX.evidence_identity == FOBXX_EVIDENCE_IDENTITY
    assert [source.source_id for source in FOBXX.sources] == [
        "franklin-fobxx-product-lookup",
        "franklin-fobxx-price-performance",
        "sec-edgar-fobxx-submissions",
        "sec-edgar-fobxx-nmfp3",
    ]
    assert parse_nmfp3(
        (FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()
    ).series_id == (FOBXX_SERIES_ID)


def test_franklin_lookup_uses_the_verified_shclcode_field() -> None:
    observation = parse_product_lookup(
        (FIXTURES / "fobxx-product-lookup-20260822.json").read_bytes()
    )

    assert isinstance(observation, FobxxProductLookupObservation)
    assert observation.fund_id == "29386"
    assert observation.share_class_code == "SINGLCLASS"


def test_franklin_history_normalizes_percent_ratios_and_preserves_blanks() -> None:
    observation = parse_price_history(
        (FIXTURES / "fobxx-price-history-20260822.json").read_bytes()
    )

    assert isinstance(observation, FobxxPriceHistoryObservation)
    assert observation.rows[0].date == date(2026, 8, 21)
    assert observation.rows[0].nav_std == Decimal("1.00000000")
    assert observation.rows[0].daily_liquid_asset_ratio is None
    assert observation.rows[0].weekly_liquid_asset_ratio is None
    july = next(row for row in observation.rows if row.date == date(2026, 7, 31))
    assert july.daily_liquid_asset_ratio == Decimal("0.637420")
    assert july.weekly_liquid_asset_ratio == Decimal("0.734485")


def test_franklin_parsers_refuse_schema_drift() -> None:
    lookup = (FIXTURES / "fobxx-product-lookup-20260822.json").read_bytes()
    with pytest.raises(FobxxNormalizationError, match="fields"):
        parse_product_lookup(lookup.replace(b'"shclcode"', b'"shareclasscode"'))

    history = json.loads((FIXTURES / "fobxx-price-history-20260822.json").read_text())
    history["data"]["PricesHistory"]["prices"][0]["unexpected"] = True
    with pytest.raises(FobxxNormalizationError, match="fields"):
        parse_price_history(json.dumps(history).encode())


def test_franklin_source_ids_are_distinct_operations() -> None:
    assert FOBXX_LOOKUP_SOURCE_ID != FOBXX_HISTORY_SOURCE_ID


def test_fobxx_issuer_nav_and_freshness_controls_use_the_latest_row() -> None:
    history = parse_price_history(
        (FIXTURES / "fobxx-price-history-20260822.json").read_bytes()
    )
    controls = (
        fobxx_control(
            control_id="fobxx-issuer-row-fresh",
            source_id=FOBXX_HISTORY_SOURCE_ID,
            adapter="fobxx-price-history",
            authority="issuer-api",
            span='"navdate":"2026-08-21"',
            operator="fresh_within",
            expected_value={"business_days": 3},
            grace_period=3,
        ),
        fobxx_control(
            control_id="fobxx-issuer-nav-peg",
            source_id=FOBXX_HISTORY_SOURCE_ID,
            adapter="fobxx-price-history",
            authority="issuer-api",
            span='"navstd":"1.00000000"',
            operator="eq",
            expected_value={"field": "nav_std", "value": "1.00000000"},
        ),
    )

    report = evaluate(
        FOBXX,
        controls,
        {FOBXX_HISTORY_SOURCE_ID: history},
        prior_observations={},
        now=date(2026, 8, 26),
    )

    assert [item.result for item in report.evaluations] == [
        EvaluationResult.SATISFIED,
        EvaluationResult.SATISFIED,
    ]
    assert all(
        item.evidence_deadline == date(2026, 8, 26)
        for item in report.evaluations
    )


@pytest.mark.parametrize(
    ("field", "threshold"),
    [
        ("daily_liquid_asset_ratio", "0.25"),
        ("weekly_liquid_asset_ratio", "0.50"),
    ],
)
def test_blank_latest_issuer_ratio_is_no_data_never_a_breach(
    field: str, threshold: str
) -> None:
    history = parse_price_history(
        (FIXTURES / "fobxx-price-history-20260822.json").read_bytes()
    )
    control = fobxx_control(
        control_id=f"fobxx-issuer-{field}-floor",
        source_id=FOBXX_HISTORY_SOURCE_ID,
        adapter="fobxx-price-history",
        authority="issuer-api",
        span=f'"{field.replace("_liquid_asset_ratio", "liquidassetratio")}":""',
        operator="non_decreasing",
        expected_value={"field": field, "value": threshold},
    )

    evaluation = evaluate(
        FOBXX,
        (control,),
        {FOBXX_HISTORY_SOURCE_ID: history},
        prior_observations={},
        now=date(2026, 8, 22),
    ).evaluations[0]

    assert evaluation.result is EvaluationResult.UNEVALUABLE
    assert evaluation.observed_value is None


def test_present_issuer_ratio_below_the_current_floor_is_contradicted() -> None:
    history = parse_price_history(
        (FIXTURES / "fobxx-price-history-20260822.json").read_bytes()
    )
    latest = replace(
        history.rows[0], daily_liquid_asset_ratio=Decimal("0.249999")
    )
    observation = replace(history, rows=(latest, *history.rows[1:]))
    control = fobxx_control(
        control_id="fobxx-issuer-daily-floor",
        source_id=FOBXX_HISTORY_SOURCE_ID,
        adapter="fobxx-price-history",
        authority="issuer-api",
        span='"dailyliquidassetratio":"63.7387"',
        operator="non_decreasing",
        expected_value={"field": "daily_liquid_asset_ratio", "value": "0.25"},
    )

    evaluation = evaluate(
        FOBXX,
        (control,),
        {FOBXX_HISTORY_SOURCE_ID: observation},
        prior_observations={},
        now=date(2026, 8, 22),
    ).evaluations[0]

    assert evaluation.result is EvaluationResult.CONTRADICTED


def test_fobxx_control_shapes_are_deterministically_decidable() -> None:
    assert supports(
        FOBXX_HISTORY_SOURCE_ID,
        ComparisonOperator.EQ,
        {"field": "nav_std", "value": "1.00000000"},
        presence_fields=FOBXX.presence_fields,
        freshness_units=FOBXX.freshness_units,
    )
    assert supports(
        FOBXX_HISTORY_SOURCE_ID,
        ComparisonOperator.RECONCILES_WITH,
        {
            "field": "nav_std",
            "comparison_source_id": "sec-edgar-fobxx-nmfp3",
            "comparison_field": "stable_price_per_share",
            "tolerance": "0",
        },
        presence_fields=FOBXX.presence_fields,
        freshness_units=FOBXX.freshness_units,
    )
    assert not supports(
        FOBXX_HISTORY_SOURCE_ID,
        ComparisonOperator.RECONCILES_WITH,
        {
            "field": "daily_liquid_asset_ratio",
            "comparison_source_id": "sec-edgar-fobxx-nmfp3",
            "comparison_field": "stable_price_per_share",
            "tolerance": "0",
        },
        presence_fields=FOBXX.presence_fields,
        freshness_units=FOBXX.freshness_units,
    )
    assert not supports(
        FOBXX_HISTORY_SOURCE_ID,
        ComparisonOperator.EQ,
        {"field": "nav_std", "value": "0.9999"},
        presence_fields=FOBXX.presence_fields,
        freshness_units=FOBXX.freshness_units,
    )
    assert not supports(
        FOBXX_HISTORY_SOURCE_ID,
        ComparisonOperator.RECONCILES_WITH,
        {
            "field": "nav_std",
            "comparison_source_id": "sec-edgar-fobxx-nmfp3",
            "comparison_field": "stable_price_per_share",
            "tolerance": "0.01",
        },
        presence_fields=FOBXX.presence_fields,
        freshness_units=FOBXX.freshness_units,
    )


def test_sec_stable_price_and_reported_liquidity_floors_are_supported() -> None:
    filing = replace(
        parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()),
        filing_date=date(2026, 8, 6),
    )
    controls = (
        fobxx_control(
            control_id="fobxx-sec-stable-price",
            source_id="sec-edgar-fobxx-nmfp3",
            adapter="fobxx-nmfp3",
            authority="regulator-filing",
            span="<stablePricePerShare>1.0000</stablePricePerShare>",
            operator="eq",
            expected_value={"field": "stable_price_per_share", "value": "1.0000"},
        ),
        fobxx_control(
            control_id="fobxx-sec-daily-floor",
            source_id="sec-edgar-fobxx-nmfp3",
            adapter="fobxx-nmfp3",
            authority="regulator-filing",
            span=(
                "<percentageDailyLiquidAssets>0.6463"
                "</percentageDailyLiquidAssets>"
            ),
            operator="non_decreasing",
            expected_value={"field": "daily_percentage", "value": "0.25"},
        ),
        fobxx_control(
            control_id="fobxx-sec-weekly-floor",
            source_id="sec-edgar-fobxx-nmfp3",
            adapter="fobxx-nmfp3",
            authority="regulator-filing",
            span=(
                "<percentageWeeklyLiquidAssets>0.7305"
                "</percentageWeeklyLiquidAssets>"
            ),
            operator="non_decreasing",
            expected_value={"field": "weekly_percentage", "value": "0.50"},
        ),
    )

    report = evaluate(
        FOBXX,
        controls,
        {"sec-edgar-fobxx-nmfp3": filing},
        prior_observations={},
        now=date(2026, 8, 22),
    )

    assert all(
        item.result is EvaluationResult.SATISFIED for item in report.evaluations
    )
    assert all(
        item.evidence_deadline == date(2026, 9, 14)
        for item in report.evaluations
    )


def test_same_date_issuer_sec_reconciliation_reports_real_disagreement() -> None:
    history = parse_price_history(
        (FIXTURES / "fobxx-price-history-20260822.json").read_bytes()
    )
    filing = replace(
        parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()),
        filing_date=date(2026, 8, 6),
    )
    controls = tuple(
        fobxx_control(
            control_id=f"fobxx-reconcile-{issuer_field}",
            source_id=FOBXX_HISTORY_SOURCE_ID,
            adapter="fobxx-price-history",
            authority="issuer-api",
            span=span,
            operator="reconciles_with",
            expected_value={
                "field": issuer_field,
                "comparison_source_id": "sec-edgar-fobxx-nmfp3",
                "comparison_field": sec_field,
                "tolerance": "0",
            },
        )
        for issuer_field, sec_field, span in (
            ("nav_std", "stable_price_per_share", '"navstd":"1.00000000"'),
            (
                "daily_liquid_asset_ratio",
                "daily_percentage",
                '"dailyliquidassetratio":"63.7420"',
            ),
            (
                "weekly_liquid_asset_ratio",
                "weekly_percentage",
                '"weeklyliquidassetratio":"73.4485"',
            ),
        )
    )

    report = evaluate(
        FOBXX,
        controls,
        {
            FOBXX_HISTORY_SOURCE_ID: history,
            "sec-edgar-fobxx-nmfp3": filing,
        },
        prior_observations={},
        now=date(2026, 8, 22),
    )

    assert [item.result for item in report.evaluations] == [
        EvaluationResult.SATISFIED,
        EvaluationResult.CONTRADICTED,
        EvaluationResult.CONTRADICTED,
    ]
    assert all(item.observed_on == date(2026, 7, 31) for item in report.evaluations)
    assert all(
        item.evidence_deadline == date(2026, 9, 14)
        for item in report.evaluations
    )


def test_fobxx_asset_identity_crosses_the_publication_boundary() -> None:
    assert len(asset_key_bytes(FOBXX.asset_key)) == 32


def test_nmfp3_reads_the_dated_series_not_the_first_row() -> None:
    observation = parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes())
    assert isinstance(observation, FobxxObservation)
    assert observation.cik == FOBXX_CIK
    assert observation.report_date.isoformat() == "2026-07-31"
    assert observation.net_assets > 0
    assert observation.liquidity_rows[0].date.isoformat() == "2026-07-01"
    last = next(
        row
        for row in observation.liquidity_rows
        if row.date.isoformat() == "2026-07-31"
    )
    assert last.daily_percentage == Decimal("0.6528")
    assert last.weekly_percentage == Decimal("0.7455")


def test_submissions_discovers_the_known_n_mfp3_filing() -> None:
    observation = parse_submissions(
        (FIXTURES / "fobxx-submissions-20260815.json").read_bytes()
    )
    assert isinstance(observation, FobxxSubmissionsObservation)
    assert observation.cik == FOBXX_CIK
    assert observation.filings[0].accession_number == "0002071691-26-017542"
    assert observation.filings[0].report_date.isoformat() == "2026-07-31"


def test_nmfp3_amendments_are_not_silently_ignored() -> None:
    raw = (
        (FIXTURES / "fobxx-nmfp3-20260731.xml")
        .read_bytes()
        .replace(
            b"<submissionType>N-MFP3</submissionType>",
            b"<submissionType>N-MFP3/A</submissionType>",
            1,
        )
    )

    observation = parse_nmfp3(raw)
    assert observation.report_date == date(2026, 7, 31)
    assert observation.submission_type == "N-MFP3/A"


def test_fobxx_observations_run_through_the_epoch_boundary(tmp_path: Path) -> None:
    report = run_epoch(
        FOBXX,
        transport=FobxxFixtureTransport(),
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 22),
        retrieved_at=datetime(2026, 8, 22, 2, 12, tzinfo=timezone.utc),
        controls=(),
    )

    assert [source.observed_on for source in report.sources] == [
        date(2026, 8, 22),
        date(2026, 8, 21),
        date(2026, 7, 31),
        date(2026, 7, 31),
    ]


def test_epoch_fetches_the_newest_filing_discovered_from_submissions(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (FIXTURES / "fobxx-submissions-20260815.json").read_text(encoding="utf-8")
    )
    recent = payload["filings"]["recent"]
    index = recent["form"].index("N-MFP3")
    recent["accessionNumber"][index] = "0002071691-26-019999"
    expected_url = (
        "https://www.sec.gov/Archives/edgar/data/1786958/"
        "000207169126019999/primary_doc.xml"
    )
    transport = FobxxFixtureTransport()
    transport.responses[FOBXX.sources[2].url] = TransportResponse(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode(),
    )
    transport.responses[expected_url] = transport.responses.pop(FOBXX.sources[3].url)

    report = run_epoch(
        FOBXX,
        transport=transport,
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 22),
        retrieved_at=datetime(2026, 8, 22, 2, 12, tzinfo=timezone.utc),
        controls=(),
    )

    assert report.sources[3].source_url == expected_url


def test_epoch_refuses_a_filing_that_does_not_match_discovery(tmp_path: Path) -> None:
    payload = json.loads(
        (FIXTURES / "fobxx-submissions-20260815.json").read_text(encoding="utf-8")
    )
    recent = payload["filings"]["recent"]
    index = recent["form"].index("N-MFP3")
    recent["reportDate"][index] = "2026-08-31"
    transport = FobxxFixtureTransport()
    transport.responses[FOBXX.sources[2].url] = TransportResponse(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode(),
    )

    with pytest.raises(ValueError, match="does not match SEC discovery"):
        run_epoch(
            FOBXX,
            transport=transport,
            store=EvidenceStore(tmp_path),
            now=date(2026, 8, 31),
            retrieved_at=datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc),
            controls=(),
        )


def test_epoch_refuses_a_filing_form_that_does_not_match_discovery(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (FIXTURES / "fobxx-submissions-20260815.json").read_text(encoding="utf-8")
    )
    recent = payload["filings"]["recent"]
    index = recent["form"].index("N-MFP3")
    recent["form"][index] = "N-MFP3/A"
    transport = FobxxFixtureTransport()
    transport.responses[FOBXX.sources[2].url] = TransportResponse(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode(),
    )

    with pytest.raises(ValueError, match="form does not match SEC discovery"):
        run_epoch(
            FOBXX,
            transport=transport,
            store=EvidenceStore(tmp_path),
            now=date(2026, 8, 15),
            retrieved_at=datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc),
            controls=(),
        )


@pytest.mark.parametrize(
    ("now", "expected_result"),
    [
        (date(2026, 8, 31), EvaluationResult.SATISFIED),
        (date(2026, 9, 14), EvaluationResult.SATISFIED),
        (date(2026, 9, 15), EvaluationResult.UNEVALUABLE),
    ],
)
def test_fobxx_filing_freshness_spans_until_the_next_filing_deadline(
    now: date, expected_result: EvaluationResult
) -> None:
    observation = replace(
        parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()),
        filing_date=date(2026, 8, 6),
    )
    control = ControlRecord.from_mapping(
        {
            "asset_key": FOBXX.asset_key,
            "control_id": "fobxx-monthly-filing-freshness",
            "control_version": 1,
            "predicate_type": "observation",
            "subject": "FOBXX N-MFP3 report date remains within ten business days",
            "source_id": "sec-edgar-fobxx-nmfp3",
            "source_authority_class": "regulator-filing",
            "evidence_span": "<reportDate>2026-07-31</reportDate>",
            "cadence": "monthly",
            "grace_period": 10,
            "observation_adapter": "fobxx-nmfp3",
            "comparison_operator": "fresh_within",
            "expected_value": {"business_days": 10},
            "effective_from": "2026-07-31",
            "effective_until": None,
            "compiler_confidence": 1.0,
            "approval_state": "approved",
            "compilation_sha256": "00" * 32,
        }
    )

    report = evaluate(
        FOBXX,
        (control,),
        {control.source_id: observation},
        prior_observations={},
        now=now,
        previous=AssetState.UNVERIFIABLE,
    )

    assert report.evaluations[0].result is expected_result
    assert report.evaluations[0].evidence_deadline == date(2026, 9, 14)


def test_fobxx_filing_freshness_refuses_a_late_filing() -> None:
    observation = replace(
        parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()),
        filing_date=date(2026, 8, 17),
    )
    control = ControlRecord.from_mapping(
        {
            "asset_key": FOBXX.asset_key,
            "control_id": "fobxx-monthly-filing-freshness",
            "control_version": 1,
            "predicate_type": "observation",
            "subject": "FOBXX N-MFP3 appears within ten business days",
            "source_id": "sec-edgar-fobxx-nmfp3",
            "source_authority_class": "regulator-filing",
            "evidence_span": "<reportDate>2026-07-31</reportDate>",
            "cadence": "monthly",
            "grace_period": 10,
            "observation_adapter": "fobxx-nmfp3",
            "comparison_operator": "fresh_within",
            "expected_value": {"business_days": 10},
            "effective_from": "2026-07-31",
            "effective_until": None,
            "compiler_confidence": 1.0,
            "approval_state": "approved",
            "compilation_sha256": "00" * 32,
        }
    )

    report = evaluate(
        FOBXX,
        (control,),
        {control.source_id: observation},
        prior_observations={},
        now=date(2026, 8, 17),
        previous=AssetState.UNVERIFIABLE,
    )

    assert report.evaluations[0].result is EvaluationResult.CONTRADICTED
    assert report.evaluations[0].evidence_deadline == date(2026, 9, 14)


def test_fobxx_report_uri_does_not_mislabel_the_asset_as_ustb() -> None:
    assert (
        report_uri({"report": {"epoch_id": "fobxx-2026-08-21", "sequence": 1}})
        == "urn:touchstone:fobxx:fobxx-2026-08-21:1"
    )


def test_fobxx_has_asset_specific_report_limitations() -> None:
    assert FOBXX_LIMITATIONS
    assert FOBXX_LIMITATIONS != USTB_LIMITATIONS
    assert all("Superstate" not in limitation for limitation in FOBXX_LIMITATIONS)


def test_fobxx_unattended_publication_path_writes_a_verifying_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import touchstone.approval as approval
    import touchstone.verify as verification

    digest, artifact, approval_ledger = compiled_fobxx_controls(tmp_path)
    artifacts = {digest: artifact}
    monkeypatch.setattr(
        approval,
        "from_directory",
        lambda directory=None: approval.from_mapping(artifacts),
    )
    monkeypatch.setattr(
        verification, "compilation_bytes", lambda requested: artifacts[requested]
    )

    scheduled_at = datetime(2026, 8, 22, 2, 20, tzinfo=timezone.utc)
    backend = FakeBackend()
    workspace = Workspace(tmp_path / "fobxx")
    workspace.root.mkdir(parents=True, exist_ok=True)
    operations = OperationsStore(workspace.operations, now=lambda: scheduled_at)
    service = Service(
        PublisherClient(
            backend,
            TransparencyLog(workspace.transparency_log),
            workspace.pending_journal,
        ),
        operations,
        IncidentLog(workspace.incidents),
        asset_key=FOBXX.asset_key,
        lock_path=workspace.lock,
        sleep=lambda seconds: None,
        now=lambda: scheduled_at,
        heartbeat_path=workspace.heartbeat,
        registry_address=backend.manifest.registry_address,
    )
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    produce = make_producer(
        store=EvidenceStore(workspace.evidence),
        signer=signer,
        next_sequence=lambda: backend.latest_sequence(
            asset_key_bytes(FOBXX.asset_key)
        )
        + 1,
        previous_state=lambda on: (
            AssetState.UNVERIFIABLE
            if operations.load_state(FOBXX.asset_key) is None
            else operations.load_state(FOBXX.asset_key).projected(on)
        ),
        transport=FobxxFixtureTransport(),
        bundle_sink=write_bundle(workspace.bundles, backend.manifest.chain_id),
        approval_ledger=approval_ledger,
        asset=FOBXX,
    )
    clock = Clock()

    outcome = serve(
        service,
        produce,
        report_uri=report_uri,
        interval_seconds=86_400,
        max_runs=1,
        epoch_of=lambda at: epoch_id_for(at, FOBXX),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: scheduled_at,
    )

    assert outcome.completed == 1
    assert not outcome.failed
    assert len(backend.submissions) == 1
    written = list(workspace.bundles.glob("*.json"))
    assert [path.name for path in written] == [
        f"eip155-{backend.manifest.chain_id}-fobxx-2026-08-22-1.json"
    ]
    report = verify_bundle(json.loads(written[0].read_text(encoding="utf-8")))
    assert report["asset_key"] == FOBXX.asset_key
    assert report["epoch_id"] == "fobxx-2026-08-22"
    assert [item["control_id"] for item in report["controls"]] == [
        "fobxx-issuer-nav-peg",
        "fobxx-issuer-row-fresh",
        "fobxx-nav-reconciliation",
    ]


def test_fobxx_systemd_units_invoke_the_shared_asset_paths() -> None:
    root = Path(__file__).parents[1] / "deploy" / "systemd"
    publisher = (root / "touchstone-fobxx-publisher@.service").read_text(
        encoding="utf-8"
    )
    observer = (root / "touchstone-fobxx-observer@.service").read_text(
        encoding="utf-8"
    )

    assert "scripts/run_service.py" in publisher
    assert "--workspace /var/lib/touchstone/%i/fobxx" in publisher
    assert f"--asset-key {FOBXX.asset_key}" in publisher
    assert "EnvironmentFile=/etc/touchstone/%i.env" in publisher
    assert "EnvironmentFile=/etc/touchstone/%i-fobxx-source.env" in publisher
    assert "--source-user-agent=${TOUCHSTONE_SEC_USER_AGENT}" in publisher
    assert "--policy-manifest" not in publisher
    assert USTB.asset_key not in publisher
    assert "Touchstone FOBXX source observer" in observer


def test_fobxx_producer_refuses_to_sign_without_approved_controls(
    tmp_path: Path,
) -> None:
    produce = make_producer(
        store=EvidenceStore(tmp_path),
        signer=Ed25519Signer.from_seed(bytes(range(32))),
        next_sequence=lambda: 1,
        previous_state=lambda on: AssetState.UNVERIFIABLE,
        transport=FobxxFixtureTransport(),
        approval_ledger=ledger_bytes(),
        asset=FOBXX,
    )

    with pytest.raises(EpochProductionError, match="no approved controls"):
        produce(datetime(2026, 8, 15, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    "mutate",
    [
        _wrong_general_info_cik,
        lambda raw: raw.replace(b"S000067043", b"S000000001", 1),
        lambda raw: raw.replace(
            b"<liquidAssetsDetails>", b"<!DOCTYPE bad><liquidAssetsDetails>", 1
        ),
    ],
)
def test_nmfp3_refuses_wrong_identity_and_unsafe_xml(mutate) -> None:
    raw = (FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()
    with pytest.raises(FobxxNormalizationError):
        parse_nmfp3(mutate(raw))


def test_submissions_refuses_wrong_identity_and_column_drift() -> None:
    payload = json.loads((FIXTURES / "fobxx-submissions-20260815.json").read_text())
    payload["cik"] = "0000000001"
    with pytest.raises(FobxxNormalizationError, match="CIK"):
        parse_submissions(json.dumps(payload).encode())
    payload = json.loads((FIXTURES / "fobxx-submissions-20260815.json").read_text())
    payload["filings"]["recent"]["form"] = payload["filings"]["recent"]["form"][:-1]
    with pytest.raises(FobxxNormalizationError, match="different lengths"):
        parse_submissions(json.dumps(payload).encode())
