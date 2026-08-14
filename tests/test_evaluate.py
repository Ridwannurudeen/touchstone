from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from touchstone.controls import (
    AssetState,
    ComparisonOperator,
    ControlRecord,
    EvaluationResult,
)
from touchstone.evaluate import (
    business_day_deadline,
    business_days_elapsed,
    default_ustb_controls,
    evaluate_ustb,
)
from touchstone.normalize.ustb import (
    USTB_HOLDINGS_SOURCE_ID,
    USTB_NAV_SOURCE_ID,
    USTB_YIELD_SOURCE_ID,
    parse_holdings,
    parse_nav_daily,
    parse_yield,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


def observations():
    return {
        USTB_NAV_SOURCE_ID: parse_nav_daily(
            (FIXTURES / "ustb-nav.json").read_bytes(), max_bytes=262_144
        ),
        USTB_YIELD_SOURCE_ID: parse_yield(
            (FIXTURES / "ustb-yield.json").read_bytes(), max_bytes=4_096
        ),
        USTB_HOLDINGS_SOURCE_ID: parse_holdings(
            (FIXTURES / "ustb-holdings.json").read_bytes(), max_bytes=16_384
        ),
    }


def replace_control(control: ControlRecord, **changes: object) -> ControlRecord:
    mapping = control.to_mapping()
    mapping.update(changes)
    return ControlRecord.from_mapping(mapping)


@pytest.mark.parametrize(
    ("observed_on", "now", "expected"),
    [
        (date(2026, 8, 13), date(2026, 8, 13), 0),
        (date(2026, 8, 13), date(2026, 8, 14), 1),
        (date(2026, 8, 14), date(2026, 8, 15), 0),
        (date(2026, 8, 14), date(2026, 8, 17), 1),
        (date(2026, 8, 17), date(2026, 8, 14), -1),
    ],
)
def test_business_days_elapsed_weekends_only(
    observed_on: date, now: date, expected: int
) -> None:
    assert business_days_elapsed(observed_on, now) == expected


@pytest.mark.parametrize(
    ("observed_on", "grace", "deadline"),
    [
        (date(2026, 8, 13), 0, date(2026, 8, 13)),
        (date(2026, 8, 13), 1, date(2026, 8, 16)),
        (date(2026, 8, 14), 0, date(2026, 8, 16)),
        (date(2026, 8, 14), 1, date(2026, 8, 17)),
    ],
)
def test_business_day_deadline_extends_across_weekends(
    observed_on: date, grace: int, deadline: date
) -> None:
    assert business_day_deadline(observed_on, grace) == deadline


def test_golden_ustb_evaluation_is_confirmed() -> None:
    report = evaluate_ustb(
        default_ustb_controls(), observations(), now=date(2026, 8, 13)
    )

    assert report.state is AssetState.CONFIRMED
    assert report.evidence_deadline == date(2026, 8, 13)
    assert [item.control_id for item in report.evaluations] == [
        "nav-row-freshness",
        "yield-freshness",
        "holdings-freshness",
        "aum-published",
        "value-vs-expected",
    ]
    assert all(item.result is EvaluationResult.SATISFIED for item in report.evaluations)


@pytest.mark.parametrize(
    ("now", "expected_state", "stale_controls"),
    [
        (date(2026, 8, 13), AssetState.CONFIRMED, set()),
        (
            date(2026, 8, 14),
            AssetState.STALE,
            {"nav-row-freshness", "yield-freshness"},
        ),
        (
            date(2026, 9, 2),
            AssetState.STALE,
            {"nav-row-freshness", "yield-freshness"},
        ),
        (
            date(2026, 9, 3),
            AssetState.STALE,
            {
                "nav-row-freshness",
                "yield-freshness",
                "holdings-freshness",
            },
        ),
    ],
)
def test_staleness_boundaries_are_inclusive(
    now: date, expected_state: AssetState, stale_controls: set[str]
) -> None:
    report = evaluate_ustb(default_ustb_controls(), observations(), now=now)

    assert report.state is expected_state
    actual = {
        item.control_id
        for item in report.evaluations
        if item.result is EvaluationResult.UNEVALUABLE
    }
    assert actual == stale_controls
    assert all(
        item.result is not EvaluationResult.CONTRADICTED
        for item in report.evaluations
        if item.control_id.endswith("freshness")
    )


def test_value_vs_expected_supports_closed_numeric_operators() -> None:
    controls = default_ustb_controls()
    value_control = controls[-1]

    tolerance = replace_control(
        value_control,
        comparison_operator=ComparisonOperator.WITHIN_TOLERANCE.value,
        expected_value={
            "field": "net_asset_value",
            "value": "11.17",
            "tolerance": "0.01",
        },
    )
    non_decreasing = replace_control(
        value_control,
        comparison_operator=ComparisonOperator.NON_DECREASING.value,
        expected_value={"field": "net_asset_value", "value": "11.17"},
    )

    for control in (tolerance, non_decreasing):
        report = evaluate_ustb([control], observations(), now=date(2026, 8, 13))
        assert report.evaluations[0].result is EvaluationResult.SATISFIED


def test_value_mismatch_is_contradicted_and_drives_inconsistent_state() -> None:
    value_control = replace_control(
        default_ustb_controls()[-1],
        expected_value={"field": "net_asset_value", "value": "9.99"},
    )

    report = evaluate_ustb([value_control], observations(), now=date(2026, 8, 13))

    assert report.evaluations[0].observed_value == Decimal("11.17558800")
    assert report.evaluations[0].result is EvaluationResult.CONTRADICTED
    assert report.state is AssetState.INCONSISTENT


def nav_capture(name: str):
    return parse_nav_daily((FIXTURES / name).read_bytes(), max_bytes=262_144)


def aum_control() -> ControlRecord:
    return next(
        control
        for control in default_ustb_controls()
        if control.control_id == "aum-published"
    )


@pytest.mark.parametrize(
    ("capture", "now", "settled_on"),
    [
        ("ustb-nav.json", date(2026, 8, 13), date(2026, 8, 11)),
        ("ustb-nav-20260814.json", date(2026, 8, 14), date(2026, 8, 12)),
    ],
)
def test_value_controls_observe_a_settled_row_not_the_provisional_tail(
    capture: str, now: date, settled_on: date
) -> None:
    observation = nav_capture(capture)

    report = evaluate_ustb([aum_control()], {USTB_NAV_SOURCE_ID: observation}, now=now)
    evaluation = report.evaluations[0]

    assert evaluation.result is EvaluationResult.SATISFIED
    assert evaluation.observed_on == settled_on
    assert settled_on < max(row.observed_on for row in observation.rows)


def test_settled_row_survives_the_issuer_revision_that_rewrote_the_tail() -> None:
    first = {row.observed_on: row for row in nav_capture("ustb-nav.json").rows}
    second = {
        row.observed_on: row for row in nav_capture("ustb-nav-20260814.json").rows
    }

    report = evaluate_ustb(
        [aum_control()],
        {USTB_NAV_SOURCE_ID: nav_capture("ustb-nav.json")},
        now=date(2026, 8, 13),
    )
    reported_on = report.evaluations[0].observed_on

    assert second[reported_on] == first[reported_on]
    assert second[date(2026, 8, 13)] != first[date(2026, 8, 13)]
    assert second[date(2026, 8, 12)] != first[date(2026, 8, 12)]


def test_malformed_settling_window_fails_closed() -> None:
    malformed = replace_control(
        aum_control(),
        expected_value={
            "field": "assets_under_management",
            "settled_after_business_days": -1,
        },
    )

    report = evaluate_ustb([malformed], observations(), now=date(2026, 8, 13))

    assert report.evaluations[0].result is EvaluationResult.UNEVALUABLE
    assert report.evaluations[0].observed_on is None


def test_only_approved_controls_are_evaluated() -> None:
    proposed = replace_control(default_ustb_controls()[0], approval_state="proposed")

    with pytest.raises(ValueError, match="approved"):
        evaluate_ustb([proposed], observations(), now=date(2026, 8, 13))


def test_nav_evaluation_selects_latest_row_independent_of_payload_order() -> None:
    reordered = observations()
    nav = reordered[USTB_NAV_SOURCE_ID]
    reordered[USTB_NAV_SOURCE_ID] = type(nav)(rows=tuple(reversed(nav.rows)))

    report = evaluate_ustb(default_ustb_controls(), reordered, now=date(2026, 8, 13))

    assert report.state is AssetState.CONFIRMED
    assert all(
        evaluation.result is EvaluationResult.SATISFIED
        for evaluation in report.evaluations
    )


def test_non_freshness_only_control_set_fails_closed() -> None:
    aum = default_ustb_controls()[3]

    report = evaluate_ustb([aum], observations(), now=date(2036, 8, 13))

    assert report.evaluations[0].result is EvaluationResult.SATISFIED
    assert report.state is AssetState.UNVERIFIABLE


def test_cross_wired_approved_control_is_rejected() -> None:
    cross_wired = replace_control(
        default_ustb_controls()[0], observation_adapter="ustb-holdings"
    )

    with pytest.raises(ValueError, match="source and adapter"):
        evaluate_ustb([cross_wired], observations(), now=date(2026, 8, 13))
