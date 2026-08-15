from collections.abc import Iterator, Mapping
from dataclasses import fields
from datetime import date, datetime, timedelta
import hashlib
import json
import math

import pytest

from touchstone.controls import (
    AssetState,
    ComparisonOperator,
    ControlRecord,
    EvaluationResult,
    OperationalEvent,
    is_fresh,
    is_evidence_fresh,
    transition_state,
)


FIELD_NAMES = [
    "asset_key",
    "control_id",
    "control_version",
    "predicate_type",
    "subject",
    "source_id",
    "source_authority_class",
    "evidence_span",
    "cadence",
    "grace_period",
    "observation_adapter",
    "comparison_operator",
    "expected_value",
    "effective_from",
    "effective_until",
    "compiler_confidence",
    "approval_state",
]


def control_values() -> dict[str, object]:
    return {
        "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
        "control_id": "nav-row-freshness",
        "control_version": 1,
        "predicate_type": "observation",
        "subject": "USTB daily NAV row",
        "source_id": "superstate-ustb-nav-daily",
        "source_authority_class": "issuer-disclosure",
        "evidence_span": "/2026-08-13",
        "cadence": "business-daily",
        "grace_period": 3,
        "observation_adapter": "json-date-row",
        "comparison_operator": "fresh_within",
        "expected_value": {"days": 3, "labels": ["NÁV", None, True]},
        "effective_from": "2026-08-13",
        "effective_until": None,
        "compiler_confidence": 0.97,
        "approval_state": "accepted",
    }


def make_control(**changes: object) -> ControlRecord:
    values = control_values()
    values.update(changes)
    return ControlRecord.from_mapping(values)


def test_control_record_has_exact_roadmap_field_order() -> None:
    assert [field.name for field in fields(ControlRecord)] == FIELD_NAMES


def test_control_record_is_frozen() -> None:
    record = make_control()
    with pytest.raises(AttributeError):
        record.control_id = "changed"  # type: ignore[misc]


def test_from_mapping_rejects_unknown_and_missing_fields() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        make_control(extra="unsupported")

    values = control_values()
    del values["subject"]
    with pytest.raises(ValueError, match="missing field"):
        ControlRecord.from_mapping(values)


def test_direct_constructor_rejects_unknown_fields() -> None:
    values = control_values()
    values["effective_from"] = date(2026, 8, 13)
    values["extra"] = "unsupported"
    with pytest.raises(TypeError, match="extra"):
        ControlRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "operator", [operator.value for operator in ComparisonOperator]
)
def test_closed_operator_allowlist_accepts_every_operator(operator: str) -> None:
    assert (
        make_control(comparison_operator=operator).comparison_operator.value == operator
    )


def test_operator_allowlist_rejects_unknown_operator() -> None:
    with pytest.raises(ValueError, match="comparison_operator"):
        make_control(comparison_operator="gt")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("asset_key", ""),
        ("control_id", 7),
        ("control_version", True),
        ("control_version", 0),
        ("evidence_span", ""),
        ("grace_period", True),
        ("grace_period", -1),
        ("effective_from", datetime(2026, 8, 13)),
        ("effective_until", "13 August 2026"),
        ("compiler_confidence", True),
        ("compiler_confidence", math.nan),
        ("compiler_confidence", math.inf),
        ("compiler_confidence", 1.01),
        ("approval_state", ""),
    ],
)
def test_constructor_rejects_invalid_field_values(
    field_name: str, invalid_value: object
) -> None:
    values = control_values()
    values["effective_from"] = date(2026, 8, 13)
    values[field_name] = invalid_value
    with pytest.raises((TypeError, ValueError), match=field_name):
        ControlRecord(**values)  # type: ignore[arg-type]


def test_effective_date_order_is_validated() -> None:
    with pytest.raises(ValueError, match="effective_until"):
        make_control(effective_until="2026-08-12")


@pytest.mark.parametrize(
    "unsupported",
    [
        {1: "non-string key"},
        ("tuple",),
        {"nested": object()},
        {"nested": [math.nan]},
        {"nested": {"value": math.inf}},
        -math.inf,
    ],
)
def test_expected_value_rejects_non_json_and_non_finite_values(
    unsupported: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="expected_value"):
        make_control(expected_value=unsupported)


def test_canonical_json_and_hash_are_stable_across_mapping_order() -> None:
    first_values = control_values()
    second_values = dict(reversed(list(first_values.items())))
    first = ControlRecord.from_mapping(first_values)
    second = ControlRecord.from_mapping(second_values)

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.content_hash == second.content_hash
    assert first.content_hash == hashlib.sha256(first.canonical_bytes()).hexdigest()
    assert b'": ' not in first.canonical_bytes()
    assert b", " not in first.canonical_bytes()
    assert b"\n" not in first.canonical_bytes()


def test_nested_expected_value_cannot_mutate_record_identity() -> None:
    expected_value = {"days": [1, 2]}
    record = make_control(expected_value=expected_value)
    content_hash = record.content_hash

    expected_value["days"].append(3)

    assert record.content_hash == content_hash
    assert record.to_mapping()["expected_value"] == {"days": [1, 2]}


def test_canonical_json_is_sorted_utf8_and_round_trips_unicode() -> None:
    record = make_control(subject="USTB — NÁV ✓")
    canonical = record.canonical_bytes()
    decoded = canonical.decode("utf-8")

    assert "USTB — NÁV ✓" in decoded
    assert "\\u00c1" not in decoded
    assert list(json.loads(decoded)) == sorted(FIELD_NAMES)


@pytest.mark.parametrize(
    ("deadline", "now", "expected"),
    [
        (date(2026, 8, 13), date(2026, 8, 12), True),
        (date(2026, 8, 13), date(2026, 8, 13), True),
        (date(2026, 8, 13), date(2026, 8, 14), False),
    ],
)
def test_freshness_is_inclusive_and_uses_explicit_dates(
    deadline: date, now: date, expected: bool
) -> None:
    assert is_evidence_fresh(deadline, now) is expected


@pytest.mark.parametrize("value", [datetime(2026, 8, 13), "2026-08-13"])
def test_freshness_rejects_non_date_values(value: object) -> None:
    with pytest.raises(TypeError):
        is_evidence_fresh(value, date(2026, 8, 13))  # type: ignore[arg-type]


def test_observation_freshness_uses_inclusive_age_boundary() -> None:
    observed_on = date(2026, 8, 10)

    assert is_fresh(observed_on, now=date(2026, 8, 13), max_age=timedelta(days=3))
    assert not is_fresh(observed_on, now=date(2026, 8, 14), max_age=timedelta(days=3))


def test_observation_freshness_rejects_future_observation() -> None:
    assert not is_fresh(
        date(2026, 8, 14), now=date(2026, 8, 13), max_age=timedelta(days=3)
    )


def test_observation_freshness_rejects_negative_max_age() -> None:
    with pytest.raises(ValueError, match="max_age"):
        is_fresh(
            date(2026, 8, 13),
            now=date(2026, 8, 13),
            max_age=timedelta(days=-1),
        )


ALL_STATES = list(AssetState)
ALL_EVENTS = list(OperationalEvent)


@pytest.mark.parametrize("previous", ALL_STATES)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_transition_matrix_satisfied_fresh(
    previous: AssetState, event: OperationalEvent
) -> None:
    expected = (
        previous if event is OperationalEvent.SOURCE_ERROR else AssetState.CONFIRMED
    )
    assert (
        transition_state(
            previous,
            event,
            [EvaluationResult.SATISFIED],
            date(2026, 8, 13),
            date(2026, 8, 13),
        )
        is expected
    )


@pytest.mark.parametrize("previous", ALL_STATES)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_transition_matrix_expired(
    previous: AssetState, event: OperationalEvent
) -> None:
    assert (
        transition_state(
            previous,
            event,
            [EvaluationResult.SATISFIED],
            date(2026, 8, 12),
            date(2026, 8, 13),
        )
        is AssetState.STALE
    )


@pytest.mark.parametrize("previous", ALL_STATES)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_transition_matrix_contradiction_has_highest_precedence(
    previous: AssetState, event: OperationalEvent
) -> None:
    assert (
        transition_state(
            previous,
            event,
            [EvaluationResult.SATISFIED, EvaluationResult.CONTRADICTED],
            date(2026, 8, 12),
            date(2026, 8, 13),
        )
        is AssetState.INCONSISTENT
    )


@pytest.mark.parametrize("previous", ALL_STATES)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_transition_matrix_unevaluable_fresh(
    previous: AssetState, event: OperationalEvent
) -> None:
    expected = (
        previous if event is OperationalEvent.SOURCE_ERROR else AssetState.UNVERIFIABLE
    )
    assert (
        transition_state(
            previous,
            event,
            [EvaluationResult.UNEVALUABLE],
            date(2026, 8, 14),
            date(2026, 8, 13),
        )
        is expected
    )


@pytest.mark.parametrize("previous", ALL_STATES)
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_transition_matrix_empty_evaluations(
    previous: AssetState, event: OperationalEvent
) -> None:
    expected = (
        previous if event is OperationalEvent.SOURCE_ERROR else AssetState.UNVERIFIABLE
    )
    assert (
        transition_state(
            previous,
            event,
            [],
            date(2026, 8, 14),
            date(2026, 8, 13),
        )
        is expected
    )


def test_transition_rejects_invalid_inputs() -> None:
    with pytest.raises(TypeError, match="previous"):
        transition_state(  # type: ignore[arg-type]
            "CONFIRMED",
            OperationalEvent.RECONFIRMED,
            [EvaluationResult.SATISFIED],
            date(2026, 8, 13),
            date(2026, 8, 13),
        )
    with pytest.raises(TypeError, match="evaluation"):
        transition_state(
            AssetState.CONFIRMED,
            OperationalEvent.RECONFIRMED,
            ["SATISFIED"],  # type: ignore[list-item]
            date(2026, 8, 13),
            date(2026, 8, 13),
        )


class _WithdrawingMapping(Mapping):
    """Presents a complete schema and then withdraws a field.

    The fields were checked by iterating the caller's mapping and the record was built from
    a second reading of it, so this passed inspection and failed during construction.
    """

    def __init__(self, value: dict[str, object]) -> None:
        self._value = value
        self.iterations = 0

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        return iter(self._value)

    def __getitem__(self, key: str) -> object:
        if key == "expected_value" and self.iterations >= 2:
            raise KeyError(key)
        return self._value[key]

    def __len__(self) -> int:
        return len(self._value)


def test_a_control_is_inspected_and_built_from_one_reading() -> None:
    withdrawing = _WithdrawingMapping(control_values())

    assert ControlRecord.from_mapping(withdrawing) == make_control()
