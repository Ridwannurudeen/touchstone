"""Deterministic USTB control evaluation and asset-state derivation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from touchstone.approval import APPROVED_KEY, approved_control, load_approval_ledger
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

# The NAV row fields the adapter can actually read. `_observed_value` maps exactly these;
# a control naming anything else observes nothing and would be UNEVALUABLE for ever.
NAV_FIELDS = frozenset(
    {
        "fund_id",
        "net_asset_value",
        "subscription_nav_per_share",
        "assets_under_management",
        "outstanding_shares",
        "net_income_expenses",
    }
)

# Which scalars a presence control may observe, per source.
#
# Confirmation across two captures is a *source* policy, not a property of `exists`. It
# exists for nav-daily because the issuer publishes a provisional row and rewrites it; the
# yield and holdings endpoints publish scalars that the strict normalizer has already
# established as present and typed in the capture being evaluated, and there is nothing
# there for a second capture to confirm. Requiring one anyway made every presence control
# on those two sources permanently UNEVALUABLE — the compiler proposed five of them against
# real evidence and not one could ever be decided.
#
# `holdings` is deliberately absent. It is a collection, and whether "exists" means
# "present" or "non-empty" is undefined; inventing that meaning here would be a semantics
# decision smuggled in as a lookup table.
PRESENCE_FIELDS: Mapping[str, frozenset[str]] = {
    USTB_YIELD_SOURCE_ID: frozenset(
        {"as_of_date", "thirty_day", "seven_day", "one_day"}
    ),
    USTB_HOLDINGS_SOURCE_ID: frozenset({"as_of_date"}),
}


def supports(
    source_id: str, operator: ComparisonOperator, expected_value: FrozenJSONValue
) -> bool:
    """Whether the deterministic evaluator can reach a verdict on this combination.

    Shared by the compiler's policy gate, because a control the evaluator can never decide
    must not be accepted in the first place however well it cites its evidence. Two copies
    of this rule would be two answers to what this system can actually prove.

    It judges the whole `expected_value`, not just the source and operator. An earlier
    version checked only those two and so waved through a NAV control naming a field the
    adapter cannot read, a `within_tolerance` with no tolerance, and a `fresh_within` whose
    window was a string — every one of which the compiler would then accept and the
    evaluator would then answer UNEVALUABLE for ever.
    """
    # `minimum_row_age_business_days` is optional, and where it appears it has to be usable.
    # A malformed window made `_minimum_row_age_business_days` return None, which abstains
    # from every row for ever — the control reports UNEVALUABLE and nothing says why. That
    # is precisely the outcome this gate exists to keep out of the set. A window declared
    # where the evaluator never reads it is refused too: only the NAV source selects a row,
    # and only then for operators other than freshness, so anywhere else the key would be a
    # setting a reader could believe was in force while nothing consulted it.
    if isinstance(expected_value, Mapping) and "minimum_row_age_business_days" in (
        expected_value
    ):
        declared = expected_value["minimum_row_age_business_days"]
        if type(declared) is not int or declared < 0:
            return False
        if source_id != USTB_NAV_SOURCE_ID:
            return False
        if operator is ComparisonOperator.FRESH_WITHIN:
            return False

    if operator is ComparisonOperator.FRESH_WITHIN:
        if not isinstance(expected_value, Mapping):
            return False
        windows = [
            expected_value[unit]
            for unit in ("business_days", "calendar_days")
            if unit in expected_value
        ]
        return len(windows) == 1 and type(windows[0]) is int and windows[0] >= 0

    field = _expected_field(expected_value)
    if field is None:
        return False
    if source_id != USTB_NAV_SOURCE_ID:
        return operator is ComparisonOperator.EXISTS and field in PRESENCE_FIELDS.get(
            source_id, frozenset()
        )
    if field not in NAV_FIELDS:
        return False
    if operator is ComparisonOperator.EXISTS:
        return True
    if _expected_decimal(expected_value, "value") is None:
        return False
    if operator is ComparisonOperator.WITHIN_TOLERANCE:
        tolerance = _expected_decimal(expected_value, "tolerance")
        return tolerance is not None and tolerance >= 0
    return operator in {ComparisonOperator.EQ, ComparisonOperator.NON_DECREASING}


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


def default_ustb_controls(
    ledger: Mapping[str, list] | None = None,
) -> tuple[ControlRecord, ...]:
    """The approved USTB control set, resolved from the compilations that produced it.

    These were not written here. Each one is a candidate a model proposed from the issuer's
    own bytes, which passed the compiler's deterministic gates, and which a human then
    approved — an approval that may change exactly two things, the ``approval_state`` and
    the digest of the artifact the candidate came out of.

    The five controls that stood here before were hand-written and marked approved
    directly. They cited real spans and evaluated correctly, but nothing had compiled them,
    so a report claiming they came from a compiler was claiming something untrue. They are
    retired rather than retrofitted: feeding an already-approved control back through a
    canned provider to manufacture an artifact is self-attestation, not provenance.

    ``ledger`` derives the set from an already-read snapshot instead of reading the file
    again. The ledger was read four times on the way to one report, and a control declined
    between two of those reads produced a signed, publishable report that the offline
    verifier refused. Callers that will also commit a ledger digest must pass the same
    snapshot here.
    """
    snapshot = load_approval_ledger() if ledger is None else ledger
    return tuple(
        approved_control(entry, ledger=snapshot) for entry in snapshot[APPROVED_KEY]
    )


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

    # One reading of each mapping for the whole report. Asking per control meant the NAV
    # source was read three times, so a mapping that changed underneath produced a single
    # evaluation describing neither observation — a freshness date from one and a value
    # from another. The values are frozen observation dataclasses, so an owned top-level
    # copy is enough to make the whole report describe one set of observations.
    observed = dict(observations)
    prior = dict(prior_observations)
    evaluations = tuple(
        _evaluate_control(
            control,
            observed.get(control.source_id),
            prior.get(control.source_id),
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
    if control.source_id != USTB_NAV_SOURCE_ID:
        # Before the NAV route below, which returns None for any observation that is not a
        # NAV one and so silently made every non-freshness control on the other two sources
        # UNEVALUABLE forever.
        return _evaluate_presence(control, observation)
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


def _evaluate_presence(
    control: ControlRecord, observation: USTBObservation
) -> ControlEvaluation:
    """A scalar the issuer published, in the bytes this epoch captured. Nothing more.

    What this proves is narrow and worth stating: the issuer returned this normalized field
    in these hash-bound bytes. It is not evidence the value is correct, final, stable, or
    that it will be published again tomorrow.

    Never CONTRADICTED. A required field that is absent fails normalization outright, so
    the observation would not exist to evaluate; reporting a contradiction here would claim
    an observation about the asset that was never made.
    """
    if not supports(
        control.source_id, control.comparison_operator, control.expected_value
    ):
        return ControlEvaluation(
            control.control_id, EvaluationResult.UNEVALUABLE, None, None
        )
    value = getattr(observation, _expected_field(control.expected_value), None)
    if value is None:
        return ControlEvaluation(
            control.control_id, EvaluationResult.UNEVALUABLE, None, None
        )
    observed = value if isinstance(value, (date, Decimal)) else None
    # The capture's own as-of date, not None. A conclusive evaluation carrying no evidence
    # date is refused outright by the offline verifier, so returning None here produced
    # controls that evaluated cleanly and then made every bundle unverifiable.
    return ControlEvaluation(
        control.control_id,
        EvaluationResult.SATISFIED,
        observed,
        None,
        getattr(observation, "as_of_date", None),
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
