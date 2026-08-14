"""Deterministic USTB control evaluation and asset-state derivation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from touchstone.controls import (
    AssetState,
    ComparisonOperator,
    ControlRecord,
    EvaluationResult,
    FrozenJSONValue,
    OperationalEvent,
    transition_state,
)
from touchstone.normalize.ustb import (
    USTB_HOLDINGS_SOURCE_ID,
    USTB_NAV_SOURCE_ID,
    USTB_YIELD_SOURCE_ID,
    USTBHoldingsObservation,
    USTBNavObservation,
    USTBNavRow,
    USTBObservation,
    USTBYieldObservation,
)


USTB_ASSET_KEY = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"

# The newest nav-daily rows are provisional: the issuer publishes a row for the current
# date carrying the previous values forward and rewrites it later. Value controls
# therefore observe only rows confirmed unchanged across two retained captures. This age
# floor is a cheap pre-filter in front of that comparison, not proof of settlement: in
# the 2026-08-13 and 2026-08-14 captures every revised row was under two business days
# old, but two captures cannot establish that no older row is ever revised. The count is
# weekday-only — exchange and bank holidays are not modelled.
MINIMUM_ROW_AGE_BUSINESS_DAYS = 2


@dataclass(frozen=True, slots=True)
class ControlEvaluation:
    control_id: str
    result: EvaluationResult
    observed_value: date | Decimal | None
    evidence_deadline: date | None
    observed_on: date | None = None


@dataclass(frozen=True, slots=True)
class USTBEvaluationReport:
    evaluations: tuple[ControlEvaluation, ...]
    state: AssetState
    evidence_deadline: date


def business_days_elapsed(observed_on: date, now: date) -> int:
    """Count weekdays after ``observed_on`` through ``now``; holidays come later."""
    _date("observed_on", observed_on)
    _date("now", now)
    if now < observed_on:
        return -1
    current = observed_on + timedelta(days=1)
    elapsed = 0
    while current <= now:
        if current.weekday() < 5:
            elapsed += 1
        current += timedelta(days=1)
    return elapsed


def business_day_deadline(observed_on: date, grace_business_days: int) -> date:
    """Return the inclusive weekend-aware deadline; holidays require the ops calendar."""
    _date("observed_on", observed_on)
    if type(grace_business_days) is not int:
        raise TypeError("grace_business_days must be an integer")
    if grace_business_days < 0:
        raise ValueError("grace_business_days must not be negative")
    deadline = observed_on
    remaining = grace_business_days
    while remaining:
        deadline += timedelta(days=1)
        if deadline.weekday() < 5:
            remaining -= 1
    while (deadline + timedelta(days=1)).weekday() >= 5:
        deadline += timedelta(days=1)
    return deadline


def default_ustb_controls() -> tuple[ControlRecord, ...]:
    """Return the approved fixture-backed USTB control set for this vertical."""
    common: dict[str, object] = {
        "asset_key": USTB_ASSET_KEY,
        "control_version": 1,
        "predicate_type": "observation",
        "source_authority_class": "issuer-api",
        "effective_from": "2026-08-13",
        "effective_until": None,
        "compiler_confidence": 1.0,
        "approval_state": "approved",
    }
    mappings = [
        {
            **common,
            "control_id": "nav-row-freshness",
            "subject": "USTB daily NAV row",
            "source_id": USTB_NAV_SOURCE_ID,
            "evidence_span": '"net_asset_value_date":"08/13/2026"',
            "cadence": "business-daily",
            "grace_period": 0,
            "observation_adapter": "ustb-nav-daily",
            "comparison_operator": "fresh_within",
            "expected_value": {"business_days": 0},
        },
        {
            **common,
            "control_id": "yield-freshness",
            "subject": "USTB published yield date",
            "source_id": USTB_YIELD_SOURCE_ID,
            "evidence_span": '"as_of_date":"2026-08-11"',
            "cadence": "business-daily",
            "grace_period": 2,
            "observation_adapter": "ustb-yield",
            "comparison_operator": "fresh_within",
            "expected_value": {"business_days": 2},
        },
        {
            **common,
            "control_id": "holdings-freshness",
            "subject": "USTB published holdings date",
            "source_id": USTB_HOLDINGS_SOURCE_ID,
            "evidence_span": '"as_of_date":"07/24/2026"',
            "cadence": "periodic",
            "grace_period": 40,
            "observation_adapter": "ustb-holdings",
            "comparison_operator": "fresh_within",
            "expected_value": {"calendar_days": 40, "provisional": True},
        },
        {
            **common,
            "control_id": "aum-published",
            "control_version": 3,
            "subject": "USTB assets under management",
            "source_id": USTB_NAV_SOURCE_ID,
            "evidence_span": '"assets_under_management":"958406746.9500"',
            "cadence": "business-daily",
            "grace_period": 0,
            "observation_adapter": "ustb-nav-daily",
            "comparison_operator": "exists",
            "expected_value": {
                "field": "assets_under_management",
                "minimum_row_age_business_days": MINIMUM_ROW_AGE_BUSINESS_DAYS,
            },
        },
        {
            **common,
            "control_id": "value-vs-expected",
            "control_version": 3,
            "subject": "USTB issuer fund identifier",
            "source_id": USTB_NAV_SOURCE_ID,
            "evidence_span": '"fund_id":1',
            "cadence": "business-daily",
            "grace_period": 0,
            "observation_adapter": "ustb-nav-daily",
            "comparison_operator": "eq",
            "expected_value": {
                "field": "fund_id",
                "value": "1",
                "minimum_row_age_business_days": MINIMUM_ROW_AGE_BUSINESS_DAYS,
            },
        },
    ]
    return tuple(ControlRecord.from_mapping(mapping) for mapping in mappings)


def evaluate_ustb(
    controls: Iterable[ControlRecord],
    observations: Mapping[str, USTBObservation],
    *,
    prior_observations: Mapping[str, USTBObservation],
    now: date,
    previous: AssetState = AssetState.UNVERIFIABLE,
    event: OperationalEvent = OperationalEvent.RECONFIRMED,
) -> USTBEvaluationReport:
    """Evaluate approved USTB controls and apply frozen state-transition semantics.

    ``prior_observations`` carries the qualifying earlier capture per source and is
    required, never defaulted: a caller with no qualifying predecessor passes ``{}`` and
    every value control abstains rather than silently claiming an unconfirmed row.
    """
    _date("now", now)
    if not isinstance(observations, Mapping):
        raise TypeError("observations must be a mapping")
    if not isinstance(prior_observations, Mapping):
        raise TypeError("prior_observations must be a mapping")
    records = tuple(controls)
    if any(not isinstance(control, ControlRecord) for control in records):
        raise TypeError("each control must be a ControlRecord")
    if any(control.approval_state != "approved" for control in records):
        raise ValueError("only approved controls may be evaluated")
    for control in records:
        _validate_control_binding(control)

    evaluations = tuple(
        _evaluate_control(
            control,
            observations.get(control.source_id),
            prior_observations.get(control.source_id),
            now,
        )
        for control in records
    )
    deadlines = tuple(
        evaluation.evidence_deadline
        for evaluation in evaluations
        if evaluation.evidence_deadline is not None
    )
    if not deadlines:
        if any(
            evaluation.result is EvaluationResult.CONTRADICTED
            for evaluation in evaluations
        ):
            state = AssetState.INCONSISTENT
        else:
            state = AssetState.UNVERIFIABLE
        return USTBEvaluationReport(
            evaluations=evaluations,
            state=state,
            evidence_deadline=now - timedelta(days=1),
        )
    evidence_deadline = min(deadlines)
    state = transition_state(
        previous,
        event,
        (evaluation.result for evaluation in evaluations),
        evidence_deadline,
        now,
    )
    return USTBEvaluationReport(
        evaluations=evaluations,
        state=state,
        evidence_deadline=evidence_deadline,
    )


def _validate_control_binding(control: ControlRecord) -> None:
    adapters = {
        USTB_NAV_SOURCE_ID: "ustb-nav-daily",
        USTB_YIELD_SOURCE_ID: "ustb-yield",
        USTB_HOLDINGS_SOURCE_ID: "ustb-holdings",
    }
    if control.asset_key != USTB_ASSET_KEY:
        raise ValueError("control asset_key does not identify USTB")
    if control.observation_adapter != adapters.get(control.source_id):
        raise ValueError("control source and adapter do not match")


def _evaluate_control(
    control: ControlRecord,
    observation: USTBObservation | None,
    prior_observation: USTBObservation | None,
    now: date,
) -> ControlEvaluation:
    if now < control.effective_from or (
        control.effective_until is not None and now > control.effective_until
    ):
        return ControlEvaluation(
            control.control_id, EvaluationResult.UNEVALUABLE, None, None
        )
    if observation is None:
        return ControlEvaluation(
            control.control_id, EvaluationResult.UNEVALUABLE, None, None
        )
    if control.comparison_operator is ComparisonOperator.FRESH_WITHIN:
        return _evaluate_freshness(control, observation, now)
    row = _confirmed_nav_row(control, observation, prior_observation, now)
    if row is None:
        return ControlEvaluation(
            control.control_id, EvaluationResult.UNEVALUABLE, None, None
        )
    observed = _observed_value(control, row)
    if control.comparison_operator is ComparisonOperator.EXISTS:
        result = (
            EvaluationResult.SATISFIED
            if observed is not None
            else EvaluationResult.CONTRADICTED
        )
    else:
        expected = _expected_decimal(control.expected_value, "value")
        if not isinstance(observed, Decimal) or expected is None:
            result = EvaluationResult.UNEVALUABLE
        elif control.comparison_operator is ComparisonOperator.EQ:
            result = _comparison_result(observed == expected)
        elif control.comparison_operator is ComparisonOperator.WITHIN_TOLERANCE:
            tolerance = _expected_decimal(control.expected_value, "tolerance")
            result = (
                EvaluationResult.UNEVALUABLE
                if tolerance is None or tolerance < 0
                else _comparison_result(abs(observed - expected) <= tolerance)
            )
        elif control.comparison_operator is ComparisonOperator.NON_DECREASING:
            result = _comparison_result(observed >= expected)
        else:
            result = EvaluationResult.UNEVALUABLE
    return ControlEvaluation(
        control.control_id, result, observed, None, row.observed_on
    )


def _evaluate_freshness(
    control: ControlRecord, observation: USTBObservation, now: date
) -> ControlEvaluation:
    if isinstance(observation, USTBNavObservation):
        row = _latest_nav_row(observation)
        observed_on = row.observed_on if row is not None else None
        deadline = (
            business_day_deadline(observed_on, control.grace_period)
            if observed_on is not None
            else None
        )
    elif isinstance(observation, USTBYieldObservation):
        observed_on = observation.as_of_date
        deadline = business_day_deadline(observed_on, control.grace_period)
    elif isinstance(observation, USTBHoldingsObservation):
        observed_on = observation.as_of_date
        deadline = observed_on + timedelta(days=control.grace_period)
    else:
        observed_on = None
        deadline = None
    result = (
        EvaluationResult.SATISFIED
        if observed_on is not None and observed_on <= now <= deadline
        else EvaluationResult.UNEVALUABLE
    )
    return ControlEvaluation(
        control.control_id, result, observed_on, deadline, observed_on
    )


def _confirmed_nav_row(
    control: ControlRecord,
    observation: USTBObservation,
    prior_observation: USTBObservation | None,
    now: date,
) -> USTBNavRow | None:
    """Return the newest NAV row confirmed unchanged across two retained captures.

    A row qualifies only when the earlier capture carries the same date and the whole
    normalized row is identical, so a row revised between captures is skipped and an
    older unchanged row may be observed instead.
    """
    if not isinstance(observation, USTBNavObservation) or not isinstance(
        prior_observation, USTBNavObservation
    ):
        return None
    minimum_age = _minimum_row_age_business_days(control.expected_value)
    if minimum_age is None:
        return None
    prior_rows = {row.observed_on: row for row in prior_observation.rows}
    return max(
        (
            row
            for row in observation.rows
            if row.observed_on <= now
            and business_days_elapsed(row.observed_on, now) >= minimum_age
            and prior_rows.get(row.observed_on) == row
        ),
        key=lambda row: row.observed_on,
        default=None,
    )


def _observed_value(control: ControlRecord, row: USTBNavRow) -> Decimal | None:
    field = _expected_field(control.expected_value)
    allowed = {
        "fund_id": Decimal(row.fund_id),
        "net_asset_value": row.net_asset_value,
        "subscription_nav_per_share": row.subscription_nav_per_share,
        "assets_under_management": row.assets_under_management,
        "outstanding_shares": row.outstanding_shares,
        "net_income_expenses": row.net_income_expenses,
    }
    return allowed.get(field)


def _latest_nav_row(observation: USTBNavObservation):
    return max(observation.rows, key=lambda row: row.observed_on, default=None)


def _minimum_row_age_business_days(value: FrozenJSONValue) -> int | None:
    """Return the control's minimum row age; ``None`` rejects a malformed declaration."""
    if not isinstance(value, Mapping):
        return None
    declared = value.get("minimum_row_age_business_days", 0)
    if type(declared) is not int or declared < 0:
        return None
    return declared


def _expected_field(value: FrozenJSONValue) -> str | None:
    if not isinstance(value, Mapping):
        return None
    field = value.get("field")
    return field if isinstance(field, str) else None


def _expected_decimal(value: FrozenJSONValue, field: str) -> Decimal | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        result = Decimal(raw)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _comparison_result(matches: bool) -> EvaluationResult:
    return EvaluationResult.SATISFIED if matches else EvaluationResult.CONTRADICTED


def _date(name: str, value: object) -> None:
    if type(value) is not date:
        raise TypeError(f"{name} must be a date")
