import json
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from touchstone.normalize.ustb import (
    DEFAULT_MAX_BYTES,
    USTB_HOLDINGS_SOURCE_ID,
    USTB_NAV_SOURCE_ID,
    USTB_YIELD_SOURCE_ID,
    NormalizationError,
    normalize_ustb_payload,
    normalize_ustb_payload_isolated,
    parse_holdings,
    parse_nav_daily,
    parse_yield,
)
from touchstone.sources import USTB_SOURCE_BY_ID


FIXTURES = Path(__file__).parents[1] / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def encode(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def test_nav_fixture_normalizes_exact_values_without_float_round_trip() -> None:
    observation = parse_nav_daily(fixture_bytes("ustb-nav.json"))

    assert len(observation.rows) == 954
    assert observation.rows[0].observed_on == date(2026, 8, 13)
    assert observation.rows[0].net_asset_value == Decimal("11.17558800")
    assert observation.rows[0].subscription_nav_per_share is None
    assert observation.rows[0].assets_under_management == Decimal(
        "958406746.9500"
    )
    assert observation.rows[0].outstanding_shares == Decimal("85758954.871099")
    assert observation.rows[0].net_income_expenses == Decimal("92362.32361600")
    assert observation.rows[-1].observed_on == date(2024, 1, 3)
    assert observation.rows[-1].outstanding_shares is None


def test_yield_fixture_normalizes_json_numbers_directly_to_decimal() -> None:
    observation = parse_yield(fixture_bytes("ustb-yield.json"))

    assert observation.as_of_date == date(2026, 8, 11)
    assert observation.thirty_day == Decimal("0.03506350017212496")
    assert observation.seven_day == Decimal("0.03492134410540035")
    assert observation.one_day == Decimal("0.03553014845996195")


def test_holdings_fixture_normalizes_currency_percent_and_variable_day_dates() -> None:
    observation = parse_holdings(fixture_bytes("ustb-holdings.json"))

    assert observation.as_of_date == date(2026, 7, 24)
    assert len(observation.holdings) == 36
    assert observation.holdings[0].security_name == (
        "U.S. Treasury Bill 07/30/2026"
    )
    assert observation.holdings[0].base_value_cost == Decimal("19990189")
    assert observation.holdings[0].maturity == date(2026, 7, 30)
    assert observation.holdings[0].current_yield == Decimal("2.99")
    assert observation.holdings[0].percent_of_fund == Decimal("2.44")
    assert observation.holdings[1].maturity == date(2026, 8, 4)


def test_observations_are_immutable() -> None:
    observation = parse_yield(fixture_bytes("ustb-yield.json"))

    with pytest.raises(FrozenInstanceError):
        observation.as_of_date = date(2026, 8, 12)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (parse_nav_daily, b'{"not":"an array"}'),
        (parse_yield, b"[]"),
        (parse_holdings, b"[]"),
        (parse_nav_daily, b"[]"),
        (parse_holdings, b'{"as_of_date":"07/24/2026","holdings":[]}'),
    ],
)
def test_root_magic_shape_is_strict(parser: object, raw: bytes) -> None:
    with pytest.raises(NormalizationError):
        parser(raw)  # type: ignore[operator]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"as_of_date":"2026-08-11","thirty_day":0.1,"seven_day":0.2,"one_day":0.3,"extra":1}',
        b'{"as_of_date":"2026-08-11","thirty_day":0.1,"seven_day":0.2}',
        b'{"as_of_date":"2026-08-11","thirty_day":0.1,"thirty_day":0.2,"seven_day":0.2,"one_day":0.3}',
        b'{"as_of_date":"2026-08-11","thirty_day":NaN,"seven_day":0.2,"one_day":0.3}',
        b'{"as_of_date":"2026-08-11","thirty_day":Infinity,"seven_day":0.2,"one_day":0.3}',
        b'{"as_of_date":"2026-08-11","thirty_day":"0.1","seven_day":0.2,"one_day":0.3}',
        b'{"as_of_date":"08/11/2026","thirty_day":0.1,"seven_day":0.2,"one_day":0.3}',
        b'{"as_of_date":"2026-02-30","thirty_day":0.1,"seven_day":0.2,"one_day":0.3}',
        b"not-json",
        b'\xff{"as_of_date":"2026-08-11"}',
    ],
)
def test_yield_rejects_adversarial_or_non_exact_payloads(raw: bytes) -> None:
    with pytest.raises(NormalizationError):
        parse_yield(raw)


def test_nav_rejects_unknown_missing_wrong_fund_invalid_date_and_numeric() -> None:
    valid = {
        "fund_id": 1,
        "net_asset_value_date": "08/13/2026",
        "net_asset_value": "11.17558800",
        "subscription_nav_per_share": None,
        "assets_under_management": "958406746.9500",
        "outstanding_shares": "85758954.871099",
        "net_income_expenses": "92362.32361600",
    }
    cases = []
    cases.append([{**valid, "extra": "blocked"}])
    cases.append([{key: value for key, value in valid.items() if key != "fund_id"}])
    cases.append([{**valid, "fund_id": 2}])
    cases.append([{**valid, "net_asset_value_date": "8/13/2026"}])
    cases.append([{**valid, "net_asset_value": 11.175588}])
    cases.append([{**valid, "net_asset_value": "NaN"}])
    cases.append([{**valid, "subscription_nav_per_share": "Infinity"}])
    cases.append([{**valid, "outstanding_shares": ""}])

    for case in cases:
        with pytest.raises(NormalizationError):
            parse_nav_daily(encode(case))


def test_nav_allows_blank_outstanding_shares_only_for_exact_oldest_sentinel() -> None:
    sentinel = {
        "fund_id": 1,
        "net_asset_value_date": "01/03/2024",
        "net_asset_value": "10",
        "subscription_nav_per_share": None,
        "assets_under_management": "0",
        "outstanding_shares": "",
        "net_income_expenses": "0",
    }

    observation = parse_nav_daily(encode([sentinel]))

    assert observation.rows[0].outstanding_shares is None


@pytest.mark.parametrize(
    "change",
    [
        {"extra": "blocked"},
        {"Security Name": None},
        {"Base Value/Cost": "$1,00"},
        {"Maturity": "04-XYZ-2026"},
        {"Current Yld": "NaN%"},
        {"% of Fund": "2.44"},
    ],
)
def test_holdings_rejects_invalid_nested_records(change: dict[str, object]) -> None:
    holding = {
        "Security Name": "U.S. Treasury Bill 08/04/2026",
        "Base Value/Cost": "$19,980,056",
        "Maturity": "4-Aug-2026",
        "Current Yld": "3.31%",
        "% of Fund": "2.44%",
    }
    holding.update(change)
    raw = encode({"as_of_date": "07/24/2026", "holdings": [holding]})

    with pytest.raises(NormalizationError):
        parse_holdings(raw)


def test_size_limit_is_checked_before_json_parsing() -> None:
    raw = fixture_bytes("ustb-yield.json")

    with pytest.raises(NormalizationError, match="size limit"):
        parse_yield(raw, max_bytes=len(raw) - 1)


def test_depth_limit_is_checked_before_json_parsing() -> None:
    raw = b"[" * 33 + b"0" + b"]" * 33

    with pytest.raises(NormalizationError, match="depth limit"):
        parse_nav_daily(raw, max_depth=32)


@pytest.mark.parametrize(
    ("source_id", "fixture_name", "expected_type"),
    [
        (USTB_NAV_SOURCE_ID, "ustb-nav.json", "USTBNavObservation"),
        (USTB_YIELD_SOURCE_ID, "ustb-yield.json", "USTBYieldObservation"),
        (USTB_HOLDINGS_SOURCE_ID, "ustb-holdings.json", "USTBHoldingsObservation"),
    ],
)
def test_dispatches_exact_source_ids(
    source_id: str, fixture_name: str, expected_type: str
) -> None:
    result = normalize_ustb_payload(source_id, fixture_bytes(fixture_name))

    assert type(result).__name__ == expected_type


def test_dispatch_source_ids_match_the_allowlisted_manifests() -> None:
    assert {
        USTB_NAV_SOURCE_ID,
        USTB_YIELD_SOURCE_ID,
        USTB_HOLDINGS_SOURCE_ID,
    } == set(USTB_SOURCE_BY_ID)


def test_dispatch_rejects_unknown_source_id() -> None:
    with pytest.raises(NormalizationError, match="unknown USTB source_id"):
        normalize_ustb_payload("https://evil.example/source", b"{}")


@pytest.mark.parametrize(
    ("source_id", "fixture_name", "expected_type"),
    [
        (USTB_NAV_SOURCE_ID, "ustb-nav.json", "USTBNavObservation"),
        (USTB_YIELD_SOURCE_ID, "ustb-yield.json", "USTBYieldObservation"),
        (USTB_HOLDINGS_SOURCE_ID, "ustb-holdings.json", "USTBHoldingsObservation"),
    ],
)
def test_isolated_parser_returns_typed_result(
    source_id: str, fixture_name: str, expected_type: str
) -> None:
    result = normalize_ustb_payload_isolated(
        source_id,
        fixture_bytes(fixture_name),
        max_bytes=DEFAULT_MAX_BYTES,
        timeout=5.0,
    )

    assert type(result).__name__ == expected_type


def test_isolated_parser_propagates_typed_rejection() -> None:
    with pytest.raises(NormalizationError, match="invalid JSON"):
        normalize_ustb_payload_isolated(
            USTB_YIELD_SOURCE_ID,
            b"not-json",
            timeout=5.0,
        )


def test_extreme_json_exponent_is_a_typed_rejection() -> None:
    raw = (
        b'{"as_of_date":"2026-08-11","thirty_day":'
        b"1e999999999999999999999999,"
        b'"seven_day":0.2,"one_day":0.3}'
    )

    with pytest.raises(NormalizationError, match="invalid JSON"):
        parse_yield(raw)
