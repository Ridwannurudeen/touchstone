"""Portable network-free verification for Touchstone observation bundles."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from cryptography.exceptions import InvalidSignature

from touchstone.controls import (
    AssetState,
    ComparisonOperator,
    ControlRecord,
    EvaluationResult,
    OperationalEvent,
    transition_state,
)
from touchstone.evidence import CONFIRMATION_INTERVAL_SECONDS
from touchstone.report import (
    CAPTURE_ROLES,
    REPORT_VERSION,
    control_set_root,
    evidence_root,
)
from touchstone.signing import (
    canonical_json_bytes,
    strict_json_loads,
    verify_signed_report,
)


BUNDLE_VERSION = "touchstone.verification-bundle.v2"
_BUNDLE_FIELDS = {
    "control_records",
    "evidence_digests",
    "published_key",
    "report_canonical",
    "signed_report",
    "version",
}
_REPORT_FIELDS = {
    "asset_key",
    "compiler_provenance_digests",
    "control_set_root",
    "controls",
    "correction_of",
    "epoch_id",
    "evidence_root",
    "limitations",
    "observed_at",
    "publisher_kid",
    "sequence",
    "state",
    "state_transition",
    "valid_until",
    "version",
}
_CONTROL_RESULT_FIELDS = {"content_hash", "control_id", "evaluation"}
_EVALUATION_FIELDS = {"evidence_deadline", "observed_on", "observed_value", "result"}
_TRANSITION_FIELDS = {"as_of", "event", "evidence_deadline", "previous_state"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ASSET_KEY = re.compile(r"eip155:[1-9][0-9]*:0x[0-9a-f]{40}")
_CONCLUSIVE_RESULTS = frozenset(
    {EvaluationResult.SATISFIED, EvaluationResult.CONTRADICTED}
)


class VerificationError(RuntimeError):
    """A precise offline-bundle verification failure."""


def create_bundle(
    signed_report: Mapping[str, object],
    published_key: Mapping[str, object],
    control_records: Sequence[ControlRecord],
    evidence_digests: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Create the exact self-contained bundle mapping at ``BUNDLE_VERSION``."""
    if not isinstance(signed_report, Mapping) or not isinstance(
        signed_report.get("report"), Mapping
    ):
        raise ValueError("signed_report.report must be a mapping")
    if not isinstance(published_key, Mapping):
        raise TypeError("published_key must be a mapping")
    if any(not isinstance(record, ControlRecord) for record in control_records):
        raise TypeError("each control record must be a ControlRecord")
    return {
        "control_records": [record.to_mapping() for record in control_records],
        "evidence_digests": [dict(record) for record in evidence_digests],
        "published_key": dict(published_key),
        "report_canonical": canonical_json_bytes(dict(signed_report["report"])).decode(
            "utf-8"
        ),
        "signed_report": dict(signed_report),
        "version": BUNDLE_VERSION,
    }


def write_bundle(path: str | Path, bundle: Mapping[str, object]) -> None:
    """Write a canonical portable bundle with one terminating newline."""
    Path(path).write_bytes(canonical_json_bytes(dict(bundle)) + b"\n")


def verify_bundle(value: bytes | str | Mapping[str, object]) -> Mapping[str, object]:
    """Verify a bundle without network access and return its report mapping."""
    try:
        parsed = strict_json_loads(value) if isinstance(value, (bytes, str)) else value
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise VerificationError(f"invalid bundle JSON: {error}") from error
    bundle = _exact_mapping(parsed, _BUNDLE_FIELDS, "bundle")
    if bundle["version"] != BUNDLE_VERSION:
        raise VerificationError("unsupported bundle version")

    signed_report = _mapping(bundle["signed_report"], "signed_report")
    report = _mapping(signed_report.get("report"), "signed_report.report")
    report_canonical = bundle["report_canonical"]
    if not isinstance(report_canonical, str):
        raise VerificationError("report_canonical must be text")
    try:
        canonical = canonical_json_bytes(dict(report)).decode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise VerificationError(
            f"report cannot be canonically serialized: {error}"
        ) from error
    if report_canonical != canonical:
        raise VerificationError("canonical report re-serialization mismatch")

    published_key = _mapping(bundle["published_key"], "published_key")
    kid = signed_report.get("kid")
    if not isinstance(kid, str):
        raise VerificationError("signed report kid is invalid")
    try:
        verified = verify_signed_report(signed_report, {kid: published_key})
    except InvalidSignature as error:
        raise VerificationError("signature verification failed") from error
    except (TypeError, ValueError, RecursionError) as error:
        raise VerificationError(f"key resolution failed: {error}") from error
    if verified != report:
        raise VerificationError("verified report does not match bundled report")

    _verify_report_schema(report, kid)
    controls = _load_controls(bundle["control_records"])
    if any(record.asset_key != report["asset_key"] for record in controls):
        raise VerificationError("control records do not identify the report asset")
    evidence = _evidence_records(bundle["evidence_digests"])
    if report["control_set_root"] != control_set_root(controls):
        raise VerificationError("control-set root mismatch")
    if report["evidence_root"] != evidence_root(evidence):
        raise VerificationError("evidence root mismatch")
    transition = _mapping(report["state_transition"], "state_transition")
    _verify_control_results(
        report["controls"],
        controls,
        _calendar_date(transition.get("as_of"), "as_of"),
    )
    _verify_state(report)
    _verify_capture_roles(evidence, report, controls)
    return report


def _verify_report_schema(report: Mapping[str, object], envelope_kid: str) -> None:
    if set(report) != _REPORT_FIELDS:
        raise VerificationError("report fields do not match the supported schema")
    if report["version"] != REPORT_VERSION:
        raise VerificationError("unsupported report version")
    if report["publisher_kid"] != envelope_kid:
        raise VerificationError("report publisher kid does not match signature kid")
    if (
        not isinstance(report["asset_key"], str)
        or _ASSET_KEY.fullmatch(report["asset_key"]) is None
    ):
        raise VerificationError("asset_key must be a canonical eip155 identifier")
    if not isinstance(report["epoch_id"], str) or not report["epoch_id"]:
        raise VerificationError("epoch_id must be nonempty text")
    if type(report["sequence"]) is not int or report["sequence"] < 1:
        raise VerificationError("sequence must be a positive integer")
    correction = report["correction_of"]
    if correction is not None and (
        type(correction) is not int or not 1 <= correction < report["sequence"]
    ):
        raise VerificationError("correction_of must reference an earlier sequence")
    for field in ("control_set_root", "evidence_root"):
        if (
            not isinstance(report[field], str)
            or _DIGEST.fullmatch(report[field]) is None
        ):
            raise VerificationError(f"{field} must be a lowercase SHA-256 digest")
    provenance = report["compiler_provenance_digests"]
    if (
        not isinstance(provenance, list)
        or not provenance
        or any(
            not isinstance(value, str) or _DIGEST.fullmatch(value) is None
            for value in provenance
        )
    ):
        raise VerificationError("compiler provenance digests are invalid")
    limitations = report["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(value, str) or not value.strip() for value in limitations)
    ):
        raise VerificationError("limitations must contain nonempty text")
    observed = _timestamp(report["observed_at"], "observed_at")
    valid = _timestamp(report["valid_until"], "valid_until")
    if valid < observed:
        raise VerificationError("valid_until precedes observed_at")


def _load_controls(value: object) -> tuple[ControlRecord, ...]:
    if not isinstance(value, list):
        raise VerificationError("control_records must be an array")
    try:
        records = tuple(ControlRecord.from_mapping(item) for item in value)
    except (TypeError, ValueError) as error:
        raise VerificationError(f"invalid control record: {error}") from error
    ids = [record.control_id for record in records]
    if len(set(ids)) != len(ids):
        raise VerificationError("control record ids must be unique")
    unapproved = sorted(
        record.control_id for record in records if record.approval_state != "approved"
    )
    if unapproved:
        raise VerificationError(
            "bundled control is not approved: " + ", ".join(unapproved)
        )
    return records


def _evidence_records(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not value:
        raise VerificationError("evidence_digests must be a nonempty array")
    if any(not isinstance(item, Mapping) for item in value):
        raise VerificationError("each evidence digest must be an object")
    return list(value)


def _verify_capture_roles(
    evidence: Sequence[Mapping[str, object]],
    report: Mapping[str, object],
    records: tuple[ControlRecord, ...],
) -> None:
    """Bind every claim to a current capture, and every conclusive value result to a
    confirmation.

    The trigger is the result, not the value: an ``exists`` control that reports
    ``CONTRADICTED`` carries no value but still asserts an absence, and asserting absence
    from a single capture is exactly the unconfirmed claim this rule exists to reject.
    """
    by_role: dict[tuple[str, str], Mapping[str, object]] = {}
    for reference in evidence:
        role = reference.get("capture_role")
        source_id = reference.get("source_id")
        if role not in CAPTURE_ROLES or not isinstance(source_id, str):
            raise VerificationError("evidence reference role or source is invalid")
        by_role[(source_id, role)] = reference

    sources = {record.source_id for record in records}
    for source_id in sources:
        if (source_id, "current") not in by_role:
            raise VerificationError(
                f"no current evidence reference for source {source_id}"
            )

    controls_by_id = {record.control_id: record for record in records}
    for item in report["controls"]:
        evaluation = item["evaluation"]
        control = controls_by_id[item["control_id"]]
        if control.comparison_operator is ComparisonOperator.FRESH_WITHIN:
            continue
        if EvaluationResult(evaluation["result"]) not in _CONCLUSIVE_RESULTS:
            continue
        confirmation = by_role.get((control.source_id, "confirmation"))
        if confirmation is None:
            raise VerificationError(
                f"value claim for {item['control_id']} has no confirmation capture"
            )
        current = by_role[(control.source_id, "current")]
        separation = _timestamp(
            current["retrieved_at"], "current retrieved_at"
        ) - _timestamp(confirmation["retrieved_at"], "confirmation retrieved_at")
        if separation < timedelta(seconds=CONFIRMATION_INTERVAL_SECONDS):
            raise VerificationError(
                "confirmation capture does not precede the current capture by a full day"
            )


def _verify_control_results(
    value: object, records: tuple[ControlRecord, ...], as_of: date
) -> None:
    if not isinstance(value, list) or not value:
        raise VerificationError("report controls must be a nonempty array")
    expected = {record.control_id: record.content_hash for record in records}
    observed: dict[str, str] = {}
    for item in value:
        result = _exact_mapping(item, _CONTROL_RESULT_FIELDS, "report control")
        control_id = result["control_id"]
        content_hash = result["content_hash"]
        if not isinstance(control_id, str) or not isinstance(content_hash, str):
            raise VerificationError("report control identity is invalid")
        if control_id in observed:
            raise VerificationError("report control ids must be unique")
        evaluation = _exact_mapping(
            result["evaluation"], _EVALUATION_FIELDS, "control evaluation"
        )
        try:
            EvaluationResult(evaluation["result"])
        except (TypeError, ValueError) as error:
            raise VerificationError("control evaluation result is invalid") from error
        deadline = evaluation["evidence_deadline"]
        observed_value = evaluation["observed_value"]
        observed_on = evaluation["observed_on"]
        if observed_value is not None and (
            not isinstance(observed_value, str) or not observed_value.strip()
        ):
            raise VerificationError("observed value must be nonempty text or null")
        if observed_on is None:
            if observed_value is not None:
                raise VerificationError(
                    "observed value is not attributed to an evidence date"
                )
            if EvaluationResult(evaluation["result"]) in _CONCLUSIVE_RESULTS:
                raise VerificationError(
                    "a conclusive evaluation requires an evidence date"
                )
        else:
            observed_date = _calendar_date(observed_on, "control observed_on")
            if observed_date > as_of:
                raise VerificationError(
                    "control observed_on is later than the evaluated epoch"
                )
        if deadline is not None:
            deadline_date = _calendar_date(deadline, "control evidence_deadline")
            if observed_on is None:
                raise VerificationError(
                    "an evidence deadline requires an evidence date"
                )
            if observed_date > deadline_date:
                raise VerificationError(
                    "control observed_on is later than its evidence deadline"
                )
        observed[control_id] = content_hash
    if observed != expected:
        raise VerificationError("report controls do not match bundled control records")


def _verify_state(report: Mapping[str, object]) -> None:
    transition = _exact_mapping(
        report["state_transition"], _TRANSITION_FIELDS, "state_transition"
    )
    try:
        previous = AssetState(transition["previous_state"])
        event = OperationalEvent(transition["event"])
        claimed = AssetState(report["state"])
    except (TypeError, ValueError) as error:
        raise VerificationError("state transition enum is invalid") from error
    deadline = _calendar_date(transition["evidence_deadline"], "evidence_deadline")
    as_of = _calendar_date(transition["as_of"], "as_of")
    results = []
    control_deadlines = []
    for item in report["controls"]:
        try:
            results.append(EvaluationResult(item["evaluation"]["result"]))
            control_deadline = item["evaluation"]["evidence_deadline"]
            if control_deadline is not None:
                control_deadlines.append(
                    _calendar_date(control_deadline, "control evidence_deadline")
                )
        except (KeyError, TypeError, ValueError) as error:
            raise VerificationError("control evaluation result is invalid") from error
    if control_deadlines and deadline != min(control_deadlines):
        raise VerificationError("state transition deadline does not match controls")
    observed = _timestamp(report["observed_at"], "observed_at")
    valid = _timestamp(report["valid_until"], "valid_until")
    if observed.date() != as_of:
        raise VerificationError("observed_at does not match the evaluated epoch")
    expected_valid = datetime.combine(deadline, time(23, 59, 59), tzinfo=timezone.utc)
    expected_valid = max(expected_valid, observed)
    if valid != expected_valid:
        raise VerificationError("valid_until does not match the evidence deadline")
    actual = transition_state(previous, event, results, deadline, as_of)
    if claimed is not actual:
        raise VerificationError(
            f"state mismatch: report says {claimed.value}, transition yields {actual.value}"
        )


def _exact_mapping(
    value: object, expected: set[str], context: str
) -> Mapping[str, object]:
    mapping = _mapping(value, context)
    if set(mapping) != expected:
        raise VerificationError(f"{context} fields do not match the supported schema")
    return mapping


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise VerificationError(f"{context} must be an object")
    return value


def _calendar_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise VerificationError(f"{field} must be an ISO 8601 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise VerificationError(f"{field} must be an ISO 8601 date") from error
    if parsed.isoformat() != value:
        raise VerificationError(f"{field} must be an ISO 8601 date")
    return parsed


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise VerificationError(f"{field} must be a normalized UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise VerificationError(
            f"{field} must be a normalized UTC timestamp"
        ) from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise VerificationError(f"{field} must be a normalized UTC timestamp")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Touchstone bundle offline")
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        raw = args.bundle.read_bytes()
        report = verify_bundle(raw)
    except (OSError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        f"PASS: {report['asset_key']} epoch={report['epoch_id']} "
        f"sequence={report['sequence']} state={report['state']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
