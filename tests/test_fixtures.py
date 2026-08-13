import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from touchstone.controls import is_fresh


FIXTURES = Path(__file__).parents[1] / "fixtures"
NAV_FIELDS = {
    "fund_id",
    "net_asset_value_date",
    "net_asset_value",
    "subscription_nav_per_share",
    "assets_under_management",
    "outstanding_shares",
    "net_income_expenses",
}
NAV_VALUE_FIELDS = NAV_FIELDS - {"net_asset_value_date"}


def load_json(name: str) -> object:
    return json.loads((FIXTURES / name).read_bytes())


def nav_row(records: list[dict[str, object]], observed_on: str) -> dict[str, object]:
    return next(
        record for record in records if record["net_asset_value_date"] == observed_on
    )


@pytest.mark.parametrize(
    ("name", "byte_length", "sha256"),
    [
        (
            "ustb-nav.json",
            224_837,
            "4830bc348b621f70682cd41c0d48484987b6b5f3c1a99193e0ca33e7ccba3a25",
        ),
        (
            "ustb-yield.json",
            122,
            "02ef1e14a867a5d034916021fecde3c6e555e78ac830ce78a8a3d6d49e55ce1f",
        ),
        (
            "ustb-holdings.json",
            5_396,
            "0c42d7949ccfbcd10256718199361daa4ff1c81ad90ba62c415fa173f8d22bdf",
        ),
        (
            "uscc-nav.json",
            180_286,
            "8ae682980e0f524b3ceb08976b361ccc161d5efa1d0245109b9677b2ba72d8f2",
        ),
    ],
)
def test_fixture_bytes_match_manifest(name: str, byte_length: int, sha256: str) -> None:
    raw = (FIXTURES / name).read_bytes()

    assert len(raw) == byte_length
    assert hashlib.sha256(raw).hexdigest() == sha256


@pytest.mark.parametrize(
    ("name", "fund_id", "record_count"),
    [("ustb-nav.json", 1, 954), ("uscc-nav.json", 2, 763)],
)
def test_nav_fixture_schema(name: str, fund_id: int, record_count: int) -> None:
    records = load_json(name)

    assert isinstance(records, list)
    assert len(records) == record_count
    for record in records:
        assert isinstance(record, dict)
        assert set(record) == NAV_FIELDS
        assert record["fund_id"] == fund_id
        assert isinstance(record["fund_id"], int)
        assert isinstance(record["net_asset_value_date"], str)
        datetime.strptime(record["net_asset_value_date"], "%m/%d/%Y")
        assert isinstance(record["net_asset_value"], str)
        assert record["subscription_nav_per_share"] is None
        assert isinstance(record["assets_under_management"], str)
        assert isinstance(record["outstanding_shares"], str)
        assert isinstance(record["net_income_expenses"], str)


def test_yield_fixture_schema() -> None:
    observation = load_json("ustb-yield.json")

    assert isinstance(observation, dict)
    assert set(observation) == {"as_of_date", "thirty_day", "seven_day", "one_day"}
    assert isinstance(observation["as_of_date"], str)
    date.fromisoformat(observation["as_of_date"])
    assert all(
        isinstance(observation[field], float)
        for field in ("thirty_day", "seven_day", "one_day")
    )


def test_holdings_fixture_schema() -> None:
    observation = load_json("ustb-holdings.json")

    assert isinstance(observation, dict)
    assert set(observation) == {"as_of_date", "holdings"}
    assert isinstance(observation["as_of_date"], str)
    datetime.strptime(observation["as_of_date"], "%m/%d/%Y")
    assert isinstance(observation["holdings"], list)
    assert len(observation["holdings"]) == 36
    for holding in observation["holdings"]:
        assert isinstance(holding, dict)
        assert set(holding) == {
            "Security Name",
            "Base Value/Cost",
            "Maturity",
            "Current Yld",
            "% of Fund",
        }
        assert all(isinstance(value, str) for value in holding.values())


def test_ustb_source_audit_values() -> None:
    records = load_json("ustb-nav.json")
    assert isinstance(records, list)
    current = nav_row(records, "08/13/2026")

    assert Decimal(current["net_asset_value"]) == Decimal("11.17558800")
    assert Decimal(current["assets_under_management"]) == Decimal("958406746.9500")
    assert Decimal(current["outstanding_shares"]) == Decimal("85758954.871099")


def test_ustb_observation_dates_match_source_audit() -> None:
    yield_observation = load_json("ustb-yield.json")
    holdings_observation = load_json("ustb-holdings.json")
    assert isinstance(yield_observation, dict)
    assert isinstance(holdings_observation, dict)

    assert yield_observation["as_of_date"] == "2026-08-11"
    assert holdings_observation["as_of_date"] == "07/24/2026"


def test_uscc_source_audit_nav() -> None:
    records = load_json("uscc-nav.json")
    assert isinstance(records, list)
    current = nav_row(records, "08/13/2026")

    assert Decimal(current["net_asset_value"]) == Decimal("11.67389000")


@pytest.mark.parametrize("name", ["ustb-nav.json", "uscc-nav.json"])
def test_consecutive_nav_rows_repeat_values_but_not_dates(name: str) -> None:
    records = load_json(name)
    assert isinstance(records, list)
    august_13 = nav_row(records, "08/13/2026")
    august_12 = nav_row(records, "08/12/2026")

    assert august_13["net_asset_value_date"] != august_12["net_asset_value_date"]
    assert {field: august_13[field] for field in NAV_VALUE_FIELDS} == {
        field: august_12[field] for field in NAV_VALUE_FIELDS
    }


def test_fixture_observations_are_fresh_against_frozen_now() -> None:
    now = date(2026, 8, 13)
    ustb_nav = load_json("ustb-nav.json")
    ustb_yield = load_json("ustb-yield.json")
    ustb_holdings = load_json("ustb-holdings.json")
    assert isinstance(ustb_nav, list)
    assert isinstance(ustb_yield, dict)
    assert isinstance(ustb_holdings, dict)

    nav_observed_on = datetime.strptime(
        nav_row(ustb_nav, "08/13/2026")["net_asset_value_date"], "%m/%d/%Y"
    ).date()
    yield_observed_on = date.fromisoformat(ustb_yield["as_of_date"])
    holdings_observed_on = datetime.strptime(
        ustb_holdings["as_of_date"], "%m/%d/%Y"
    ).date()

    assert is_fresh(nav_observed_on, now=now, max_age=timedelta(days=0))
    assert is_fresh(yield_observed_on, now=now, max_age=timedelta(days=2))
    assert is_fresh(holdings_observed_on, now=now, max_age=timedelta(days=40))


def test_freshness_boundary_is_inclusive_and_next_older_day_is_stale() -> None:
    now = date(2026, 8, 13)
    max_age = timedelta(days=2)

    assert is_fresh(date(2026, 8, 11), now=now, max_age=max_age)
    assert not is_fresh(date(2026, 8, 10), now=now, max_age=max_age)
