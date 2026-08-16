"""Typed controls and deterministic asset-state transitions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, timedelta
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import TypeAlias

from touchstone.quantities import finite_number


_SHA256 = re.compile(r"[0-9a-f]{64}")


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
FrozenJSONValue: TypeAlias = (
    JSONScalar | tuple["FrozenJSONValue", ...] | Mapping[str, "FrozenJSONValue"]
)


class ComparisonOperator(str, Enum):
    EXISTS = "exists"
    FRESH_WITHIN = "fresh_within"
    EQ = "eq"
    WITHIN_TOLERANCE = "within_tolerance"
    NON_DECREASING = "non_decreasing"


class AssetState(str, Enum):
    CONFIRMED = "CONFIRMED"
    STALE = "STALE"
    INCONSISTENT = "INCONSISTENT"
    UNVERIFIABLE = "UNVERIFIABLE"


class OperationalEvent(str, Enum):
    RECONFIRMED = "RECONFIRMED"
    EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
    SOURCE_ERROR = "SOURCE_ERROR"
    CORRECTION_PUBLISHED = "CORRECTION_PUBLISHED"


class EvaluationResult(str, Enum):
    SATISFIED = "SATISFIED"
    CONTRADICTED = "CONTRADICTED"
    UNEVALUABLE = "UNEVALUABLE"


def _non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _integer(name: str, value: object, *, minimum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _calendar_date(name: str, value: object) -> date:
    if type(value) is not date:
        raise TypeError(f"{name} must be a date")
    return value


def _parse_date(name: str, value: object) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO 8601 date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO 8601 date string") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO 8601 date string")
    return parsed


def _freeze_json(value: object, path: str = "expected_value") -> FrozenJSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, list):
        return tuple(
            _freeze_json(item, f"{path}[{index}]") for index, item in enumerate(value)
        )
    if isinstance(value, dict):
        frozen: dict[str, FrozenJSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    raise TypeError(f"{path} contains an unsupported JSON value")


def _thaw_json(value: FrozenJSONValue) -> JSONValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ControlRecord:
    asset_key: str
    control_id: str
    control_version: int
    predicate_type: str
    subject: str
    source_id: str
    source_authority_class: str
    evidence_span: str
    cadence: str
    grace_period: int
    observation_adapter: str
    comparison_operator: ComparisonOperator
    expected_value: FrozenJSONValue
    effective_from: date
    effective_until: date | None
    compiler_confidence: float
    approval_state: str
    # The compilation artifact this control came out of, or None on a proposal.
    #
    # A proposal cannot carry it: the digest is over the artifact that contains the
    # proposal, so a proposal naming its own digest is a cycle. It is attached at approval,
    # which is the only other thing approval may change. Because it is part of
    # `canonical_bytes`, the control-set root commits to it — an approved control cannot
    # later be pointed at a different compilation without changing the root a consumer
    # contract is pinned to.
    compilation_sha256: str | None

    def __post_init__(self) -> None:
        for name in (
            "asset_key",
            "control_id",
            "predicate_type",
            "subject",
            "source_id",
            "source_authority_class",
            "evidence_span",
            "cadence",
            "observation_adapter",
            "approval_state",
        ):
            _non_empty_string(name, getattr(self, name))

        _integer("control_version", self.control_version, minimum=1)
        _integer("grace_period", self.grace_period, minimum=0)

        operator = self.comparison_operator
        if isinstance(operator, str):
            try:
                operator = ComparisonOperator(operator)
            except ValueError as error:
                raise ValueError("comparison_operator is not allowed") from error
            object.__setattr__(self, "comparison_operator", operator)
        elif not isinstance(operator, ComparisonOperator):
            raise TypeError("comparison_operator must be a ComparisonOperator")

        object.__setattr__(self, "expected_value", _freeze_json(self.expected_value))
        _calendar_date("effective_from", self.effective_from)
        if self.effective_until is not None:
            _calendar_date("effective_until", self.effective_until)
            if self.effective_until < self.effective_from:
                raise ValueError("effective_until must not precede effective_from")

        confidence = finite_number(self.compiler_confidence, "compiler_confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("compiler_confidence must be between 0 and 1")
        object.__setattr__(self, "compiler_confidence", confidence)

        if self.compilation_sha256 is not None and (
            not isinstance(self.compilation_sha256, str)
            or _SHA256.fullmatch(self.compilation_sha256) is None
        ):
            raise ValueError(
                "compilation_sha256 must be a lowercase SHA-256 digest or None"
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ControlRecord:
        if not isinstance(value, Mapping):
            raise TypeError("control record must be a mapping")
        # One reading before the schema is inspected. The fields were checked by iterating
        # the caller's mapping and the record was then built from a second reading of it,
        # so a mapping that presented a complete schema and then withdrew a field passed
        # inspection and failed during construction.
        value = dict(value)

        names = {field.name for field in fields(cls)}
        supplied = set(value)
        unknown = supplied - names
        missing = names - supplied
        if unknown:
            rendered = ", ".join(sorted(map(str, unknown)))
            raise ValueError(f"unknown field(s): {rendered}")
        if missing:
            rendered = ", ".join(sorted(missing))
            raise ValueError(f"missing field(s): {rendered}")

        converted = dict(value)
        converted["effective_from"] = _parse_date(
            "effective_from", converted["effective_from"]
        )
        if converted["effective_until"] is not None:
            converted["effective_until"] = _parse_date(
                "effective_until", converted["effective_until"]
            )
        return cls(**converted)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, JSONValue]:
        return {
            "asset_key": self.asset_key,
            "control_id": self.control_id,
            "control_version": self.control_version,
            "predicate_type": self.predicate_type,
            "subject": self.subject,
            "source_id": self.source_id,
            "source_authority_class": self.source_authority_class,
            "evidence_span": self.evidence_span,
            "cadence": self.cadence,
            "grace_period": self.grace_period,
            "observation_adapter": self.observation_adapter,
            "comparison_operator": self.comparison_operator.value,
            "expected_value": _thaw_json(self.expected_value),
            "effective_from": self.effective_from.isoformat(),
            "effective_until": (
                self.effective_until.isoformat()
                if self.effective_until is not None
                else None
            ),
            "compiler_confidence": self.compiler_confidence,
            "approval_state": self.approval_state,
            "compilation_sha256": self.compilation_sha256,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_mapping(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def is_evidence_fresh(evidence_deadline: date, now: date) -> bool:
    """Return whether ``now`` is on or before the inclusive evidence deadline."""
    _calendar_date("evidence_deadline", evidence_deadline)
    _calendar_date("now", now)
    return now <= evidence_deadline


def is_fresh(observed_on: date, *, now: date, max_age: timedelta) -> bool:
    """Return whether a date-keyed observation is within its inclusive maximum age."""
    _calendar_date("observed_on", observed_on)
    _calendar_date("now", now)
    if type(max_age) is not timedelta:
        raise TypeError("max_age must be a timedelta")
    if max_age < timedelta(0):
        raise ValueError("max_age must not be negative")
    if max_age.seconds or max_age.microseconds:
        raise ValueError("max_age must contain whole days for date-keyed freshness")
    return observed_on <= now and is_evidence_fresh(observed_on + max_age, now)


def transition_state(
    previous: AssetState,
    event: OperationalEvent,
    evaluation_results: Iterable[EvaluationResult],
    evidence_deadline: date,
    now: date,
) -> AssetState:
    """Derive asset state from accepted-control results and evidence freshness."""
    if not isinstance(previous, AssetState):
        raise TypeError("previous must be an AssetState")
    if not isinstance(event, OperationalEvent):
        raise TypeError("event must be an OperationalEvent")

    results = tuple(evaluation_results)
    if any(not isinstance(result, EvaluationResult) for result in results):
        raise TypeError("each evaluation result must be an EvaluationResult")

    fresh = is_evidence_fresh(evidence_deadline, now)
    if EvaluationResult.CONTRADICTED in results:
        return AssetState.INCONSISTENT
    if event is OperationalEvent.SOURCE_ERROR:
        return previous if fresh else AssetState.STALE
    if not fresh:
        return AssetState.STALE
    if not results or EvaluationResult.UNEVALUABLE in results:
        return AssetState.UNVERIFIABLE
    return AssetState.CONFIRMED
