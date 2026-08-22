"""Fixture and hostile-input tests for the FOBXX SEC source path."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from touchstone.approval import ledger_bytes
from touchstone.assets import (
    FOBXX,
    FOBXX_ASSET_KEY,
    FOBXX_EVIDENCE_IDENTITY,
    get_asset,
)
from touchstone.controls import AssetState, ControlRecord, EvaluationResult
from touchstone.epoch import run_epoch
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import evaluate
from touchstone.normalize.fobxx import (
    FOBXX_CIK,
    FOBXX_SERIES_ID,
    FobxxNormalizationError,
    FobxxObservation,
    FobxxSubmissionsObservation,
    parse_nmfp3,
    parse_submissions,
)
from touchstone.publish import asset_key_bytes
from touchstone.report import FOBXX_LIMITATIONS, USTB_LIMITATIONS
from touchstone.signing import Ed25519Signer
from touchstone.sources import TransportResponse
from touchstone.ustb_daemon import EpochProductionError, make_producer, report_uri


FIXTURES = Path(__file__).parents[1] / "fixtures"


class FobxxFixtureTransport:
    def __init__(self) -> None:
        self.responses = {
            FOBXX.sources[0].url: TransportResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=(FIXTURES / "fobxx-submissions-20260815.json").read_bytes(),
            ),
            FOBXX.sources[1].url: TransportResponse(
                status_code=200,
                headers={"Content-Type": "text/xml"},
                body=(FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes(),
            ),
        }

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        del timeout, max_bytes
        return self.responses[url]


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
        "sec-edgar-fobxx-submissions",
        "sec-edgar-fobxx-nmfp3",
    ]
    assert parse_nmfp3(
        (FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()
    ).series_id == (FOBXX_SERIES_ID)


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
        now=date(2026, 8, 15),
        retrieved_at=datetime(2026, 8, 15, 0, 40, tzinfo=timezone.utc),
        controls=(),
    )

    assert [source.observed_on for source in report.sources] == [
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
    transport.responses[FOBXX.sources[0].url] = TransportResponse(
        status_code=200,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode(),
    )
    transport.responses[expected_url] = transport.responses.pop(FOBXX.sources[1].url)

    report = run_epoch(
        FOBXX,
        transport=transport,
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 15),
        retrieved_at=datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc),
        controls=(),
    )

    assert report.sources[1].source_url == expected_url


def test_epoch_refuses_a_filing_that_does_not_match_discovery(tmp_path: Path) -> None:
    payload = json.loads(
        (FIXTURES / "fobxx-submissions-20260815.json").read_text(encoding="utf-8")
    )
    recent = payload["filings"]["recent"]
    index = recent["form"].index("N-MFP3")
    recent["reportDate"][index] = "2026-08-31"
    transport = FobxxFixtureTransport()
    transport.responses[FOBXX.sources[0].url] = TransportResponse(
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
    transport.responses[FOBXX.sources[0].url] = TransportResponse(
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

    assert report.evaluations[0].result is EvaluationResult.UNEVALUABLE
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
