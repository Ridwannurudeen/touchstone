"""Fixture and hostile-input tests for the FOBXX SEC source path."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from touchstone.assets import FOBXX, FOBXX_ASSET_KEY, get_asset
from touchstone.normalize.fobxx import (
    FOBXX_CIK,
    FOBXX_SERIES_ID,
    FobxxNormalizationError,
    FobxxObservation,
    FobxxSubmissionsObservation,
    parse_nmfp3,
    parse_submissions,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


def _wrong_general_info_cik(raw: bytes) -> bytes:
    start = raw.index(b"<generalInfo>")
    return raw[:start] + raw[start:].replace(
        b"<cik>0001786958</cik>", b"<cik>0000000001</cik>", 1
    )


def test_fobxx_descriptor_and_sec_fixtures_are_registered() -> None:
    assert get_asset(FOBXX_ASSET_KEY) is FOBXX
    assert FOBXX.source_manifest.is_file()
    assert [source.source_id for source in FOBXX.sources] == [
        "sec-edgar-fobxx-submissions",
        "sec-edgar-fobxx-nmfp3",
    ]
    assert parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()).series_id == (
        FOBXX_SERIES_ID
    )


def test_nmfp3_reads_the_dated_series_not_the_first_row() -> None:
    observation = parse_nmfp3((FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes())
    assert isinstance(observation, FobxxObservation)
    assert observation.cik == FOBXX_CIK
    assert observation.report_date.isoformat() == "2026-07-31"
    assert observation.net_assets > 0
    assert observation.liquidity_rows[0].date.isoformat() == "2026-07-01"
    last = next(row for row in observation.liquidity_rows if row.date.isoformat() == "2026-07-31")
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


@pytest.mark.parametrize(
    "mutate",
    [
        _wrong_general_info_cik,
        lambda raw: raw.replace(b"S000067043", b"S000000001", 1),
        lambda raw: raw.replace(b"<liquidAssetsDetails>", b"<!DOCTYPE bad><liquidAssetsDetails>", 1),
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
