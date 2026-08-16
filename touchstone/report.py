"""Versioned observation reports and independently recomputable ordered roots.

Roots use a domain-separated ordered hash chain. The initial value is
``SHA256(b"touchstone-root-v1:" + domain)``. Each item, sorted as documented by
the public helpers below, advances the chain with
``SHA256(b"\x01" + previous + canonical_json(item))``. The hexadecimal final
digest is the root published in reports and onchain.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time, timezone
import hashlib
import re

from touchstone.approval import provenance_digests
from touchstone.controls import (
    AssetState,
    ControlRecord,
    OperationalEvent,
    transition_state,
)
from touchstone.epoch import EpochControlReport, EpochSourceReport, USTBEpochReport
from touchstone.evidence import CaptureRecord
from touchstone.quantities import utc_instant
from touchstone.signing import canonical_json_bytes


REPORT_VERSION = "touchstone.observation-report.v3"
CAPTURE_ROLES = ("current", "confirmation")
_EVIDENCE_FIELDS = {"capture_role", "retrieved_at", "sha256", "source_id"}
USTB_LIMITATIONS = (
    "Issuer APIs prove only what Superstate published at the observed endpoints; "
    "Touchstone does not independently audit the fund, its assets, or issuer accuracy.",
    "Holdings publication lags daily NAV and its 40-day freshness window is provisional.",
    "The newest nav-daily rows are provisional and are revised in place, so value "
    "controls observe only a row confirmed unchanged across two retained captures; a "
    "control's observed_on names the row its observed_value belongs to. Confirmation "
    "shows a row was not revised between those captures, not that it is final.",
    "This bundle carries evidence digests, not the artifacts themselves, so it cannot "
    "replay normalization or prove that a reported row occurs inside an artifact.",
    "This local-only report does not verify an onchain NAV oracle or token supply.",
)
_DIGEST = re.compile(r"[0-9a-f]{64}")


def control_set_root(records: Iterable[ControlRecord]) -> str:
    """Hash controls ordered by ``control_id`` using their canonical content hashes."""
    controls = tuple(records)
    if any(not isinstance(record, ControlRecord) for record in controls):
        raise TypeError("each control record must be a ControlRecord")
    items = [
        {"content_hash": record.content_hash, "control_id": record.control_id}
        for record in sorted(controls, key=lambda record: record.control_id)
    ]
    _reject_duplicate_values(items, "control_id", "control_id")
    return ordered_hash_root("control-set", items)


def evidence_root(records: Iterable[Mapping[str, object]]) -> str:
    """Hash evidence references ordered by ``source_id`` then ``capture_role``.

    Every reference names the role its artifact played in the epoch, so a report that
    carries a cross-capture value claim binds both the current capture and the earlier
    one that confirmed it.
    """
    items: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _EVIDENCE_FIELDS:
            raise ValueError(
                "evidence reference must contain "
                "capture_role, retrieved_at, sha256 and source_id"
            )
        role = record["capture_role"]
        if role not in CAPTURE_ROLES:
            raise ValueError("capture_role must be current or confirmation")
        items.append(
            {
                "capture_role": role,
                "retrieved_at": _utc_timestamp_text(
                    record["retrieved_at"], "evidence retrieved_at"
                ),
                "sha256": _digest(record["sha256"], "sha256"),
                "source_id": _nonempty_text(record["source_id"], "source_id"),
            }
        )
    items.sort(key=lambda item: (item["source_id"], item["capture_role"]))
    _reject_duplicate_pairs(items)
    return ordered_hash_root("evidence", items)


def ordered_hash_root(domain: str, items: Sequence[Mapping[str, object]]) -> str:
    """Compute the documented domain-separated ordered hash chain."""
    domain_text = _nonempty_text(domain, "domain")
    state = hashlib.sha256(
        b"touchstone-root-v1:" + domain_text.encode("utf-8")
    ).digest()
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("root item must be a mapping")
        state = hashlib.sha256(
            b"\x01" + state + canonical_json_bytes(dict(item))
        ).digest()
    return state.hex()


def evidence_references(epoch: USTBEpochReport) -> list[dict[str, object]]:
    """Return the epoch's evidence references, one per capture and role."""
    if not epoch.sources:
        raise ValueError("epoch must contain source observations")
    references: list[dict[str, object]] = [
        {
            "capture_role": "current",
            "retrieved_at": _utc_timestamp(source.retrieved_at, "source retrieved_at"),
            "sha256": source.evidence_sha256,
            "source_id": source.source_id,
        }
        for source in epoch.sources
    ]
    if epoch.confirmation is not None:
        references.append(
            {
                "capture_role": "confirmation",
                "retrieved_at": _utc_timestamp(
                    epoch.confirmation.retrieved_at, "confirmation retrieved_at"
                ),
                "sha256": epoch.confirmation.sha256,
                "source_id": epoch.confirmation.source_id,
            }
        )
    return references


def _epoch_snapshot(epoch: USTBEpochReport) -> USTBEpochReport:
    """Rebuild one epoch, element by element, reading every attribute exactly once.

    Materialising only the two outer sequences left their *elements* caller-owned, and
    those are read again during validation and again during serialisation. A stateful
    evaluation could therefore report SATISFIED while the transition was checked and
    CONTRADICTED while the controls were written down, producing a CONFIRMED report whose
    own serialised controls contradict it.

    Instants are resolved here too, for the same reason and in the same pass. The evidence
    references normalised each `retrieved_at` while `observed_at` reused the caller's
    original object, so a zone that changed offset between those two reads committed an
    evidence root to one instant and declared another in the report.
    """
    if not isinstance(epoch, USTBEpochReport):
        raise TypeError("epoch must be a USTBEpochReport")
    return USTBEpochReport(
        asset_key=epoch.asset_key,
        now=epoch.now,
        state=epoch.state,
        evidence_deadline=epoch.evidence_deadline,
        sources=tuple(_source_snapshot(source) for source in epoch.sources),
        evaluations=tuple(
            _evaluation_snapshot(evaluation) for evaluation in epoch.evaluations
        ),
        confirmation=_capture_snapshot(epoch.confirmation),
    )


def _source_snapshot(source: EpochSourceReport) -> EpochSourceReport:
    return EpochSourceReport(
        source_id=source.source_id,
        source_url=source.source_url,
        content_type=source.content_type,
        byte_size=source.byte_size,
        evidence_sha256=source.evidence_sha256,
        retrieved_at=utc_instant(source.retrieved_at, "source retrieved_at"),
        observed_on=source.observed_on,
    )


def _evaluation_snapshot(evaluation: EpochControlReport) -> EpochControlReport:
    return EpochControlReport(
        control_id=evaluation.control_id,
        result=evaluation.result,
        observed_value=evaluation.observed_value,
        evidence_deadline=evaluation.evidence_deadline,
        observed_on=evaluation.observed_on,
    )


def _capture_snapshot(capture: CaptureRecord | None) -> CaptureRecord | None:
    if capture is None:
        return None
    return CaptureRecord(
        source_id=capture.source_id,
        sha256=capture.sha256,
        retrieved_at=utc_instant(capture.retrieved_at, "confirmation retrieved_at"),
        index_position=capture.index_position,
    )


def build_observation_report(
    epoch: USTBEpochReport,
    controls: Iterable[ControlRecord],
    *,
    epoch_id: str,
    sequence: int,
    publisher_kid: str,
    previous_state: AssetState = AssetState.UNVERIFIABLE,
    event: OperationalEvent = OperationalEvent.RECONFIRMED,
    limitations: Iterable[str] = USTB_LIMITATIONS,
    correction_of: int | None = None,
) -> dict[str, object]:
    """Build a strict report from a completed epoch without performing I/O."""
    # One reading of the epoch for the whole report. `sources` and `evaluations` were each
    # read three times — once to check they were non-empty, once for the derived values,
    # once for the state check — and `evidence_references` read `sources` again. An epoch
    # that answered those reads differently produced a report whose state, evidence root
    # and serialised controls each described a different set of observations, and none of
    # the checks could see it because each one was individually satisfied.
    if not isinstance(epoch, USTBEpochReport):
        raise TypeError("epoch must be a USTBEpochReport")
    epoch = _epoch_snapshot(epoch)
    records = tuple(controls)
    if type(sequence) is not int or sequence < 1:
        raise ValueError("sequence must be a positive integer")
    if correction_of is not None and (
        type(correction_of) is not int or not 1 <= correction_of < sequence
    ):
        raise ValueError("correction_of must reference an earlier positive sequence")
    if not isinstance(previous_state, AssetState):
        raise TypeError("previous_state must be an AssetState")
    if not isinstance(event, OperationalEvent):
        raise TypeError("event must be an OperationalEvent")

    controls_by_id = {record.control_id: record for record in records}
    if len(controls_by_id) != len(records):
        raise ValueError("control_id values must be unique")
    evaluations_by_id = {item.control_id: item for item in epoch.evaluations}
    if len(evaluations_by_id) != len(epoch.evaluations):
        raise ValueError("epoch control_id values must be unique")
    if set(controls_by_id) != set(evaluations_by_id):
        raise ValueError("epoch evaluations do not match the control set")
    if any(record.asset_key != epoch.asset_key for record in records):
        raise ValueError("each control must identify the report asset")

    expected_state = transition_state(
        previous_state,
        event,
        (item.result for item in epoch.evaluations),
        epoch.evidence_deadline,
        epoch.now,
    )
    if epoch.state is not expected_state:
        raise ValueError("epoch state does not match the transition rules")

    evidence_digests = evidence_references(epoch)
    observed_at = max(source.retrieved_at for source in epoch.sources)
    observed_text = _utc_timestamp(observed_at, "source retrieved_at")
    valid_until = datetime.combine(
        epoch.evidence_deadline, time(23, 59, 59), tzinfo=timezone.utc
    )
    valid_until = max(valid_until, observed_at.astimezone(timezone.utc))
    valid_text = _utc_timestamp(valid_until, "evidence deadline")
    # Derived from the controls being reported, never accepted from the caller. A supplied
    # list could name a compilation that produced none of the evaluated controls, and both
    # the report builder and the offline verifier checked only that it was well-formed hex —
    # so the provenance a report carried need not have had anything to do with its controls.
    provenance = tuple(
        _digest(value, "compiler provenance digest")
        for value in provenance_digests(records)
    )
    caveats = tuple(_nonempty_text(value, "limitation") for value in limitations)
    if not caveats:
        raise ValueError("limitations must not be empty")

    report_controls = []
    for control_id in sorted(controls_by_id):
        control = controls_by_id[control_id]
        evaluation = evaluations_by_id[control_id]
        report_controls.append(
            {
                "content_hash": control.content_hash,
                "control_id": control_id,
                "evaluation": {
                    "evidence_deadline": (
                        evaluation.evidence_deadline.isoformat()
                        if evaluation.evidence_deadline is not None
                        else None
                    ),
                    "observed_on": (
                        evaluation.observed_on.isoformat()
                        if evaluation.observed_on is not None
                        else None
                    ),
                    "observed_value": evaluation.observed_value,
                    "result": evaluation.result.value,
                },
            }
        )

    return {
        "asset_key": epoch.asset_key,
        "compiler_provenance_digests": list(provenance),
        "control_set_root": control_set_root(records),
        "controls": report_controls,
        "correction_of": correction_of,
        "epoch_id": _nonempty_text(epoch_id, "epoch_id"),
        "evidence_root": evidence_root(evidence_digests),
        "limitations": list(caveats),
        "observed_at": observed_text,
        "publisher_kid": _nonempty_text(publisher_kid, "publisher_kid"),
        "sequence": sequence,
        "state": epoch.state.value,
        "state_transition": {
            "as_of": epoch.now.isoformat(),
            "event": event.value,
            "evidence_deadline": epoch.evidence_deadline.isoformat(),
            "previous_state": previous_state.value,
        },
        "valid_until": valid_text,
        "version": REPORT_VERSION,
    }


def _reject_duplicate_values(
    items: Sequence[Mapping[str, str]], key: str, label: str
) -> None:
    values = [item[key] for item in items]
    if len(set(values)) != len(values):
        raise ValueError(f"{label} values must be unique")


def _reject_duplicate_pairs(items: Sequence[Mapping[str, str]]) -> None:
    pairs = [(item["source_id"], item["capture_role"]) for item in items]
    if len(set(pairs)) != len(pairs):
        raise ValueError("evidence references must be unique per source and role")


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    value.encode("utf-8", errors="strict")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _utc_timestamp_text(value: object, field: str) -> str:
    """Accept an already-normalized UTC timestamp string and re-validate it."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a normalized UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{field} must be a normalized UTC timestamp") from error
    return _utc_timestamp(parsed, field)


def _utc_timestamp(value: object, field: str) -> str:
    return utc_instant(value, field).isoformat().replace("+00:00", "Z")
