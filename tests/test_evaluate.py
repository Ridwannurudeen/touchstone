from collections.abc import Iterator, Mapping
from dataclasses import replace
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
    USTBNavObservation,
    USTBNavRow,
    parse_holdings,
    parse_nav_daily,
    parse_yield,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"
CONFIRMED_ON = date(2026, 8, 11)


def nav_capture(name: str) -> USTBNavObservation:
    return parse_nav_daily((FIXTURES / name).read_bytes(), max_bytes=262_144)


def observations(
    nav: str = "ustb-nav-20260814.json", yields: str = "ustb-yield-20260814.json"
):
    return {
        USTB_NAV_SOURCE_ID: nav_capture(nav),
        USTB_YIELD_SOURCE_ID: parse_yield(
            (FIXTURES / yields).read_bytes(), max_bytes=4_096
        ),
        USTB_HOLDINGS_SOURCE_ID: parse_holdings(
            (FIXTURES / "ustb-holdings.json").read_bytes(), max_bytes=16_384
        ),
    }


def prior(nav: str = "ustb-nav.json") -> dict[str, USTBNavObservation]:
    return {USTB_NAV_SOURCE_ID: nav_capture(nav)}


def replace_control(control: ControlRecord, **changes: object) -> ControlRecord:
    mapping = control.to_mapping()
    mapping.update(changes)
    return ControlRecord.from_mapping(mapping)


def aum_control() -> ControlRecord:
    return next(
        control
        for control in default_ustb_controls()
        if control.control_id == "aum-published"
    )


def row(observed_on: date, net_asset_value: str = "11.00000000") -> USTBNavRow:
    return USTBNavRow(
        fund_id=1,
        observed_on=observed_on,
        net_asset_value=Decimal(net_asset_value),
        subscription_nav_per_share=None,
        assets_under_management=Decimal("100.0000"),
        outstanding_shares=Decimal("10.000000"),
        net_income_expenses=Decimal("1.00000000"),
    )


def evaluate_rows(
    rows: tuple[USTBNavRow, ...],
    prior_rows: tuple[USTBNavRow, ...],
    *,
    now: date,
    control: ControlRecord | None = None,
):
    report = evaluate_ustb(
        [control or aum_control()],
        {USTB_NAV_SOURCE_ID: USTBNavObservation(rows=rows)},
        prior_observations={USTB_NAV_SOURCE_ID: USTBNavObservation(rows=prior_rows)},
        now=now,
    )
    return report.evaluations[0]


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
        default_ustb_controls(),
        observations(),
        prior_observations=prior(),
        now=date(2026, 8, 14),
    )

    assert report.state is AssetState.CONFIRMED
    assert report.evidence_deadline == date(2026, 8, 16)
    assert [item.control_id for item in report.evaluations] == [
        "nav-row-freshness",
        "yield-freshness",
        "holdings-freshness",
        "aum-published",
        "value-vs-expected",
    ]
    assert all(item.result is EvaluationResult.SATISFIED for item in report.evaluations)


def test_value_controls_observe_the_newest_row_confirmed_across_both_captures() -> None:
    """08/12 and 08/13 were revised between captures, so 08/11 is the newest confirmed."""
    report = evaluate_ustb(
        default_ustb_controls(),
        observations(),
        prior_observations=prior(),
        now=date(2026, 8, 14),
    )
    values = {
        item.control_id: item
        for item in report.evaluations
        if item.control_id in {"aum-published", "value-vs-expected"}
    }

    assert {item.observed_on for item in values.values()} == {CONFIRMED_ON}
    assert values["aum-published"].observed_value == Decimal("958406746.9500")
    assert CONFIRMED_ON < max(
        row.observed_on for row in observations()[USTB_NAV_SOURCE_ID].rows
    )


def test_missing_prior_capture_makes_every_value_control_unevaluable() -> None:
    report = evaluate_ustb(
        default_ustb_controls(),
        observations(),
        prior_observations={},
        now=date(2026, 8, 14),
    )
    results = {item.control_id: item for item in report.evaluations}

    assert results["aum-published"].result is EvaluationResult.UNEVALUABLE
    assert results["aum-published"].observed_value is None
    assert results["aum-published"].observed_on is None
    assert results["value-vs-expected"].result is EvaluationResult.UNEVALUABLE
    assert results["nav-row-freshness"].result is EvaluationResult.SATISFIED
    assert report.state is AssetState.UNVERIFIABLE


def test_a_row_changed_in_one_field_is_not_confirmed() -> None:
    current = (row(date(2026, 8, 10)), row(date(2026, 8, 11), "11.50000000"))
    revised = (
        current[0],
        replace(current[1], net_income_expenses=Decimal("2.00000000")),
    )

    evaluation = evaluate_rows(current, revised, now=date(2026, 8, 14))

    assert evaluation.observed_on == date(2026, 8, 10)


def test_future_dated_rows_cannot_qualify() -> None:
    current = (row(date(2026, 8, 10)), row(date(2026, 8, 20)))

    evaluation = evaluate_rows(current, current, now=date(2026, 8, 14))

    assert evaluation.observed_on == date(2026, 8, 10)


def test_minimum_row_age_boundary_is_enforced() -> None:
    current = (row(date(2026, 8, 11)), row(date(2026, 8, 12)), row(date(2026, 8, 13)))

    assert evaluate_rows(current, current, now=date(2026, 8, 14)).observed_on == date(
        2026, 8, 12
    )
    assert evaluate_rows(
        current,
        current,
        now=date(2026, 8, 14),
        control=replace_control(
            aum_control(),
            expected_value={
                "field": "assets_under_management",
                "minimum_row_age_business_days": 3,
            },
        ),
    ).observed_on == date(2026, 8, 11)


def test_absent_minimum_row_age_means_zero() -> None:
    current = (row(date(2026, 8, 13)), row(date(2026, 8, 14)))
    control = replace_control(
        aum_control(), expected_value={"field": "assets_under_management"}
    )

    evaluation = evaluate_rows(current, current, now=date(2026, 8, 14), control=control)

    assert evaluation.observed_on == date(2026, 8, 14)


@pytest.mark.parametrize("declared", [-1, True, "2", 2.0, None])
def test_malformed_minimum_row_age_fails_closed(declared: object) -> None:
    current = (row(date(2026, 8, 11)),)
    control = replace_control(
        aum_control(),
        expected_value={
            "field": "assets_under_management",
            "minimum_row_age_business_days": declared,
        },
    )

    evaluation = evaluate_rows(current, current, now=date(2026, 8, 14), control=control)

    assert evaluation.result is EvaluationResult.UNEVALUABLE
    assert evaluation.observed_on is None


def test_row_selection_is_independent_of_payload_order() -> None:
    current = observations()
    reversed_current = dict(current)
    nav = current[USTB_NAV_SOURCE_ID]
    reversed_current[USTB_NAV_SOURCE_ID] = USTBNavObservation(
        rows=tuple(reversed(nav.rows))
    )
    reversed_prior = {
        USTB_NAV_SOURCE_ID: USTBNavObservation(
            rows=tuple(reversed(prior()[USTB_NAV_SOURCE_ID].rows))
        )
    }

    report = evaluate_ustb(
        default_ustb_controls(),
        reversed_current,
        prior_observations=reversed_prior,
        now=date(2026, 8, 14),
    )

    assert report.state is AssetState.CONFIRMED
    assert all(
        evaluation.observed_on == CONFIRMED_ON
        for evaluation in report.evaluations
        if evaluation.control_id == "aum-published"
    )


@pytest.mark.parametrize(
    ("now", "expected_state", "stale_controls"),
    [
        (date(2026, 8, 14), AssetState.CONFIRMED, set()),
        (date(2026, 8, 17), AssetState.STALE, {"nav-row-freshness"}),
        (date(2026, 9, 2), AssetState.STALE, {"nav-row-freshness", "yield-freshness"}),
        (
            date(2026, 9, 3),
            AssetState.STALE,
            {"nav-row-freshness", "yield-freshness", "holdings-freshness"},
        ),
    ],
)
def test_staleness_boundaries_are_inclusive(
    now: date, expected_state: AssetState, stale_controls: set[str]
) -> None:
    report = evaluate_ustb(
        default_ustb_controls(), observations(), prior_observations=prior(), now=now
    )

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
    value_control = default_ustb_controls()[-1]
    tolerance = replace_control(
        value_control,
        comparison_operator=ComparisonOperator.WITHIN_TOLERANCE.value,
        expected_value={
            "field": "net_asset_value",
            "value": "11.17",
            "tolerance": "0.01",
            "minimum_row_age_business_days": 2,
        },
    )
    non_decreasing = replace_control(
        value_control,
        comparison_operator=ComparisonOperator.NON_DECREASING.value,
        expected_value={
            "field": "net_asset_value",
            "value": "11.17",
            "minimum_row_age_business_days": 2,
        },
    )

    for control in (tolerance, non_decreasing):
        report = evaluate_ustb(
            [control],
            observations(),
            prior_observations=prior(),
            now=date(2026, 8, 14),
        )
        assert report.evaluations[0].result is EvaluationResult.SATISFIED
        assert report.evaluations[0].observed_on == CONFIRMED_ON


def test_value_mismatch_is_contradicted_and_drives_inconsistent_state() -> None:
    value_control = replace_control(
        default_ustb_controls()[-1],
        expected_value={
            "field": "net_asset_value",
            "value": "9.99",
            "minimum_row_age_business_days": 2,
        },
    )

    report = evaluate_ustb(
        [value_control],
        observations(),
        prior_observations=prior(),
        now=date(2026, 8, 14),
    )

    assert report.evaluations[0].observed_value == Decimal("11.17558800")
    assert report.evaluations[0].result is EvaluationResult.CONTRADICTED
    assert report.state is AssetState.INCONSISTENT


def test_only_approved_controls_are_evaluated() -> None:
    proposed = replace_control(default_ustb_controls()[0], approval_state="proposed")

    with pytest.raises(ValueError, match="approved"):
        evaluate_ustb(
            [proposed],
            observations(),
            prior_observations=prior(),
            now=date(2026, 8, 14),
        )


def test_non_freshness_only_control_set_fails_closed() -> None:
    report = evaluate_ustb(
        [aum_control()],
        observations(),
        prior_observations=prior(),
        now=date(2036, 8, 13),
    )

    assert report.evaluations[0].result is EvaluationResult.SATISFIED
    assert report.state is AssetState.UNVERIFIABLE


def test_cross_wired_approved_control_is_rejected() -> None:
    cross_wired = replace_control(
        default_ustb_controls()[0], observation_adapter="ustb-holdings"
    )

    with pytest.raises(ValueError, match="source and adapter"):
        evaluate_ustb(
            [cross_wired],
            observations(),
            prior_observations=prior(),
            now=date(2026, 8, 14),
        )


def test_prior_observations_must_be_a_mapping() -> None:
    with pytest.raises(TypeError, match="prior_observations"):
        evaluate_ustb(
            default_ustb_controls(),
            observations(),
            prior_observations=[],
            now=date(2026, 8, 14),
        )


class _ShiftingObservations(Mapping):
    """Answers each read of the NAV source with a different observation.

    Live, not a fresh copy per read. Three of the default controls name that source, so a
    caller mapping that changes underneath is read three times unless the whole report is
    evaluated from one snapshot.
    """

    def __init__(
        self,
        first: dict[str, object],
        later: dict[str, object],
    ) -> None:
        self._first = first
        self._later = later
        self.nav_reads = 0

    def __getitem__(self, key: str) -> object:
        if key != USTB_NAV_SOURCE_ID:
            return self._first[key]
        self.nav_reads += 1
        return self._first[key] if self.nav_reads == 1 else self._later[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._first)

    def __len__(self) -> int:
        return len(self._first)


def test_one_report_describes_one_set_of_observations() -> None:
    """Each mapping was read once per control, not once per report.

    The NAV source backs three controls, so a mapping that changed underneath produced a
    single evaluation describing neither observation: a freshness date drawn from one
    capture and a value drawn from another.
    """
    shifting = _ShiftingObservations(observations(), observations(nav="ustb-nav.json"))

    report = evaluate_ustb(
        default_ustb_controls(),
        shifting,
        prior_observations=prior(),
        now=date(2026, 8, 14),
    )

    assert shifting.nav_reads == 1, "the NAV source was read exactly once"
    assert report == evaluate_ustb(
        default_ustb_controls(),
        observations(),
        prior_observations=prior(),
        now=date(2026, 8, 14),
    )
