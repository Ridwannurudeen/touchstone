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
    supports,
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


def synthetic(**changes: object) -> ControlRecord:
    """A control built here, not resolved from a compilation artifact.

    Evaluation is deliberately provenance-free — it is a pure function of controls and
    observations, and the binding to a compilation is enforced at the report boundary
    instead. That is what lets these tests exercise state transitions, cross-wiring and
    fail-closed behaviour without a filesystem of artifacts behind every case.
    """
    mapping: dict[str, object] = {
        "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
        "control_id": "synthetic-aum",
        "control_version": 1,
        "predicate_type": "observation",
        "subject": "USTB assets under management",
        "source_id": USTB_NAV_SOURCE_ID,
        "source_authority_class": "issuer-api",
        "evidence_span": '"assets_under_management":"958406746.9500"',
        "cadence": "business-daily",
        "grace_period": 0,
        "observation_adapter": "ustb-nav-daily",
        "comparison_operator": "exists",
        "expected_value": {"field": "assets_under_management"},
        "effective_from": "2026-08-13",
        "effective_until": None,
        "compiler_confidence": 1.0,
        "approval_state": "approved",
        "compilation_sha256": None,
    }
    mapping.update(changes)
    return ControlRecord.from_mapping(mapping)


def aum_control() -> ControlRecord:
    return next(
        control
        for control in default_ustb_controls()
        if control.control_id == "ustb-aum-published"
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
        [control or synthetic()],
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
    assert report.evidence_deadline == date(2026, 8, 17)
    assert sorted(item.control_id for item in report.evaluations) == [
        "holdings-as-of-date-present",
        "ustb-aum-published",
        "ustb-nav-date-freshness",
        "ustb-nav-per-share-published",
        "ustb-one-day-yield-present",
        "ustb-outstanding-shares-published",
        "ustb-seven-day-yield-present",
        "ustb-thirty-day-yield-present",
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
        if item.control_id in {"ustb-aum-published", "ustb-nav-per-share-published", "ustb-outstanding-shares-published"}
    }

    assert {item.observed_on for item in values.values()} == {CONFIRMED_ON}
    assert values["ustb-aum-published"].observed_value == Decimal("958406746.9500")
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

    assert results["ustb-aum-published"].result is EvaluationResult.UNEVALUABLE
    assert results["ustb-aum-published"].observed_value is None
    assert results["ustb-aum-published"].observed_on is None
    assert results["ustb-nav-per-share-published"].result is (
        EvaluationResult.UNEVALUABLE
    )
    assert results["ustb-nav-date-freshness"].result is EvaluationResult.SATISFIED
    # Presence on the other two sources needs no predecessor at all.
    assert results["ustb-one-day-yield-present"].result is EvaluationResult.SATISFIED
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
    """The age floor is declared per control, and none of the approved set declares one.

    The retired hand-written controls carried `minimum_row_age_business_days: 2`, holding
    a row back until the issuer had had two business days to revise it. The compiler did
    not propose that, and approval may not add it — so the approved set observes the newest
    row confirmed across both captures, with confirmation alone as the safeguard. The floor
    still works when a control declares it, which is what this pins.
    """
    current = (row(date(2026, 8, 11)), row(date(2026, 8, 12)), row(date(2026, 8, 13)))

    def with_floor(days: int) -> ControlRecord:
        return synthetic(
            expected_value={
                "field": "assets_under_management",
                "minimum_row_age_business_days": days,
            }
        )

    assert evaluate_rows(current, current, now=date(2026, 8, 14)).observed_on == date(
        2026, 8, 13
    ), "no declared floor means the newest confirmed row"
    assert evaluate_rows(
        current, current, now=date(2026, 8, 14), control=with_floor(2)
    ).observed_on == date(2026, 8, 12)
    assert evaluate_rows(
        current, current, now=date(2026, 8, 14), control=with_floor(3)
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
        if evaluation.control_id == "ustb-aum-published"
    )


@pytest.mark.parametrize(
    ("now", "expected_state", "stale_controls"),
    [
        # Inclusive on the deadline itself, stale the day after. The approved set carries
        # exactly one freshness control — on nav-daily, with a one-business-day grace — so
        # the whole asset ages on that single deadline.
        (date(2026, 8, 16), AssetState.CONFIRMED, set()),
        (date(2026, 8, 17), AssetState.CONFIRMED, set()),
        (date(2026, 8, 18), AssetState.STALE, {"ustb-nav-date-freshness"}),
        (date(2026, 9, 3), AssetState.STALE, {"ustb-nav-date-freshness"}),
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
    """The numeric operators, on synthetic controls.

    The approved set contains no comparison control — the compiler proposed one and it was
    declined, because its expected value was a literal identifier the decimal comparison
    cannot resolve. These operators are still part of the language and still evaluated, so
    they are exercised here on controls built for the purpose.
    """
    tolerance = synthetic(
        control_id="synthetic-within-tolerance",
        comparison_operator=ComparisonOperator.WITHIN_TOLERANCE.value,
        expected_value={
            "field": "net_asset_value",
            "value": "11.17",
            "tolerance": "0.01",
            "minimum_row_age_business_days": 2,
        },
    )
    non_decreasing = synthetic(
        control_id="synthetic-non-decreasing",
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
    value_control = synthetic(
        comparison_operator=ComparisonOperator.EQ.value,
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
    # A NAV-sourced control wearing the holdings adapter. Taking the first approved
    # control and setting that adapter no longer cross-wires anything, because the first
    # approved control *is* the holdings one.
    cross_wired = synthetic(observation_adapter="ustb-holdings")

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


def test_one_report_describes_one_set_of_prior_observations() -> None:
    """The qualifying earlier capture is a caller mapping on the same terms.

    It decides which rows count as confirmed, so reading it once per control lets three
    controls confirm against three different predecessors while the report says nothing
    about which capture it compared against.
    """
    shifting = _ShiftingObservations(prior(), prior("ustb-nav-20260814.json"))

    report = evaluate_ustb(
        default_ustb_controls(),
        observations(),
        prior_observations=shifting,
        now=date(2026, 8, 14),
    )

    assert shifting.nav_reads == 1, "the prior capture was read exactly once"
    assert report == evaluate_ustb(
        default_ustb_controls(),
        observations(),
        prior_observations=prior(),
        now=date(2026, 8, 14),
    )


@pytest.mark.parametrize(
    ("expected", "decidable", "why"),
    [
        ({"field": "net_asset_value", "minimum_row_age_business_days": 2}, True, "the window the retired hand-written controls used"),
        ({"field": "net_asset_value", "minimum_row_age_business_days": 0}, True, "zero is a real window: it admits any row not future-dated"),
        ({"field": "net_asset_value"}, True, "absent is allowed and means zero"),
        ({"field": "net_asset_value", "minimum_row_age_business_days": -1}, False, "negative"),
        ({"field": "net_asset_value", "minimum_row_age_business_days": "2"}, False, "a string, not an integer"),
        ({"field": "net_asset_value", "minimum_row_age_business_days": 2.0}, False, "a float, not an integer"),
        ({"field": "net_asset_value", "minimum_row_age_business_days": True}, False, "a bool is not an integer window"),
    ],
)
def test_a_minimum_row_age_must_be_usable_to_be_accepted(
    expected: dict, decidable: bool, why: str
) -> None:
    """A malformed window abstains from every row for ever, saying nothing about why.

    `_minimum_row_age_business_days` returns None for anything that is not a non-negative
    integer, and the row selector then returns None, so the control reports UNEVALUABLE
    permanently. That is exactly the outcome `supports` exists to keep out of the approved
    set, and it did not check this key at all.
    """
    assert (
        supports(USTB_NAV_SOURCE_ID, ComparisonOperator.EXISTS, expected) is decidable
    ), why


@pytest.mark.parametrize(
    ("source", "operator"),
    [
        ("superstate-ustb-yield", ComparisonOperator.EXISTS),
        ("superstate-ustb-holdings", ComparisonOperator.EXISTS),
        (USTB_NAV_SOURCE_ID, ComparisonOperator.FRESH_WITHIN),
    ],
)
def test_a_minimum_row_age_where_nothing_reads_a_row_is_refused(
    source: str, operator: ComparisonOperator
) -> None:
    """Only the NAV source selects a row, and freshness does not select one either.

    Accepting the key elsewhere would put a setting in the control that a reader could
    reasonably believe was in force while nothing ever consulted it — a control claiming
    more than it does, which is the same defect as a vacuous test.
    """
    expected = {"field": "as_of_date", "minimum_row_age_business_days": 2}
    if operator is ComparisonOperator.FRESH_WITHIN:
        expected = {"business_days": 1, "minimum_row_age_business_days": 2}

    assert supports(source, operator, expected) is False
