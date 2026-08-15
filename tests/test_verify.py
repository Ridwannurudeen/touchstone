from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from touchstone.epoch import FixtureTransport, run_ustb_epoch
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import default_ustb_controls
from touchstone.report import (
    build_observation_report,
    evidence_references,
    evidence_root,
)
from touchstone.signing import Ed25519Signer, canonical_json_bytes
from touchstone.verify import VerificationError, create_bundle, main, verify_bundle


FIXTURES = Path(__file__).parents[1] / "fixtures"
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)


def _epoch(tmp_path: Path, *, confirmed: bool = True):
    store = EvidenceStore(tmp_path / "evidence")
    if confirmed:
        run_ustb_epoch(
            transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
            store=store,
            now=date(2026, 8, 13),
            retrieved_at=CONFIRMED_AT,
        )
    return run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
    )


def _bundle(tmp_path: Path, *, confirmed: bool = True):
    epoch = _epoch(tmp_path, confirmed=confirmed)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid=signer.kid,
        compiler_provenance_digests=["33" * 32],
    )
    return create_bundle(
        signer.sign_report(report),
        signer.public_key_record(),
        default_ustb_controls(),
        evidence_references(epoch),
    )


def _resign(bundle: dict, report: dict) -> dict:
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    bundle["signed_report"] = signer.sign_report(report)
    bundle["report_canonical"] = canonical_json_bytes(report).decode()
    return bundle


def test_verifier_accepts_complete_offline_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    verified = verify_bundle(canonical_json_bytes(bundle))
    assert verified["state"] == "CONFIRMED"
    assert verified["sequence"] == 1


def test_verifier_rejects_report_byte_tamper_with_precise_reason(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    bundle["report_canonical"] = bundle["report_canonical"].replace(
        '"state":"CONFIRMED"', '"state":"STALE"'
    )
    with pytest.raises(VerificationError, match="re-serialization mismatch"):
        verify_bundle(bundle)


def test_verifier_rejects_single_byte_bundle_tamper(tmp_path: Path) -> None:
    raw = bytearray(canonical_json_bytes(_bundle(tmp_path)))
    offset = raw.find(b"CONFIRMED")
    assert offset >= 0
    raw[offset] = ord("X")

    with pytest.raises(VerificationError, match="re-serialization mismatch"):
        verify_bundle(bytes(raw))


def test_verifier_rejects_signature_tamper_with_precise_reason(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle["signed_report"]["signature"] = "00" * 64
    with pytest.raises(VerificationError, match="signature verification failed"):
        verify_bundle(bundle)


def test_verifier_rejects_evidence_digest_tamper_with_precise_reason(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    bundle["evidence_digests"][0]["sha256"] = "ff" * 32
    with pytest.raises(VerificationError, match="evidence root mismatch"):
        verify_bundle(bundle)


def test_verifier_rejects_control_record_tamper_with_precise_reason(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    bundle["control_records"][0]["subject"] += " changed"
    with pytest.raises(VerificationError, match="control-set root mismatch"):
        verify_bundle(bundle)


def test_verifier_rejects_validly_signed_inconsistent_state(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = deepcopy(bundle["signed_report"]["report"])
    report["state"] = "STALE"
    bundle["signed_report"] = signer.sign_report(report)
    bundle["report_canonical"] = canonical_json_bytes(report).decode()
    with pytest.raises(VerificationError, match="state mismatch"):
        verify_bundle(bundle)


def test_verifier_rejects_validly_signed_cross_asset_controls(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = deepcopy(bundle["signed_report"]["report"])
    report["asset_key"] = "eip155:1:0x" + "22" * 20
    bundle["signed_report"] = signer.sign_report(report)
    bundle["report_canonical"] = canonical_json_bytes(report).decode()
    with pytest.raises(VerificationError, match="report asset"):
        verify_bundle(bundle)


def test_verifier_rejects_validly_signed_freshness_extension(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = deepcopy(bundle["signed_report"]["report"])
    report["valid_until"] = "2026-08-14T23:59:59Z"
    bundle["signed_report"] = signer.sign_report(report)
    bundle["report_canonical"] = canonical_json_bytes(report).decode()
    with pytest.raises(VerificationError, match="evidence deadline"):
        verify_bundle(bundle)


def test_cli_returns_zero_for_good_bundle_and_nonzero_for_tamper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bundle.json"
    bundle = _bundle(tmp_path)
    path.write_bytes(canonical_json_bytes(bundle) + b"\n")
    assert main([str(path)]) == 0
    assert capsys.readouterr().out.startswith("PASS:")

    path.write_bytes(path.read_bytes().replace(b'"signature":"', b'"signature":"00', 1))
    assert main([str(path)]) == 1
    assert capsys.readouterr().err.startswith("FAIL:")


def test_verifier_rejects_a_value_attributed_to_a_future_date(tmp_path: Path) -> None:
    """A validly re-signed report may not date a value after the evaluated epoch."""
    bundle = _bundle(tmp_path)
    report = deepcopy(bundle["signed_report"]["report"])
    for control in report["controls"]:
        if control["control_id"] == "aum-published":
            control["evaluation"]["observed_on"] = "2099-01-01"

    with pytest.raises(VerificationError, match="later than the evaluated epoch"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_value_with_no_evidence_date(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    report = deepcopy(bundle["signed_report"]["report"])
    for control in report["controls"]:
        if control["control_id"] == "aum-published":
            control["evaluation"]["observed_on"] = None

    with pytest.raises(VerificationError, match="not attributed to an evidence date"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_conclusive_evaluation_with_no_evidence_date(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    report = deepcopy(bundle["signed_report"]["report"])
    for control in report["controls"]:
        if control["control_id"] == "aum-published":
            control["evaluation"]["observed_on"] = None
            control["evaluation"]["observed_value"] = None

    with pytest.raises(VerificationError, match="conclusive evaluation"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_value_claim_with_no_confirmation_capture(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    bundle["evidence_digests"] = [
        reference
        for reference in bundle["evidence_digests"]
        if reference["capture_role"] != "confirmation"
    ]
    report = deepcopy(bundle["signed_report"]["report"])
    report["evidence_root"] = evidence_root(bundle["evidence_digests"])

    with pytest.raises(VerificationError, match="no confirmation capture"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_same_day_confirmation_capture(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    for reference in bundle["evidence_digests"]:
        if reference["capture_role"] == "confirmation":
            reference["retrieved_at"] = "2026-08-14T09:00:00Z"
    report = deepcopy(bundle["signed_report"]["report"])
    report["evidence_root"] = evidence_root(bundle["evidence_digests"])

    with pytest.raises(VerificationError, match="does not precede"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_confirmation_reference_for_another_source(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    for reference in bundle["evidence_digests"]:
        if reference["capture_role"] == "confirmation":
            reference["source_id"] = "superstate-ustb-yield"
    report = deepcopy(bundle["signed_report"]["report"])
    report["evidence_root"] = evidence_root(bundle["evidence_digests"])

    with pytest.raises(VerificationError, match="no confirmation capture"):
        verify_bundle(_resign(bundle, report))


def test_verifier_accepts_a_first_epoch_with_no_confirmation(tmp_path: Path) -> None:
    """Without a predecessor the report abstains on values and stays verifiable."""
    verified = verify_bundle(canonical_json_bytes(_bundle(tmp_path, confirmed=False)))
    values = {
        control["control_id"]: control["evaluation"]
        for control in verified["controls"]
        if control["control_id"] in {"aum-published", "value-vs-expected"}
    }

    assert verified["state"] == "UNVERIFIABLE"
    assert all(item["observed_value"] is None for item in values.values())
    assert all(item["observed_on"] is None for item in values.values())


def test_verifier_rejects_an_unconfirmed_absence_claim(tmp_path: Path) -> None:
    """An `exists` control reporting CONTRADICTED carries no value but asserts absence."""
    bundle = _bundle(tmp_path, confirmed=False)
    report = deepcopy(bundle["signed_report"]["report"])
    for control in report["controls"]:
        if control["control_id"] == "aum-published":
            control["evaluation"] = {
                "evidence_deadline": None,
                "observed_on": "2026-08-11",
                "observed_value": None,
                "result": "CONTRADICTED",
            }
    report["state"] = "INCONSISTENT"

    with pytest.raises(VerificationError, match="no confirmation capture"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_bundle_carrying_unapproved_controls(tmp_path: Path) -> None:
    """Approval was enforced only inside the evaluator, on the publisher's own machine.

    An independent verifier could therefore accept a bundle whose controls were still
    proposals. R-11 in the threat model records the remaining gap: this closes the
    verifier half, not the binding between a control and the compilation that produced it.
    """
    bundle = _bundle(tmp_path)
    for record in bundle["control_records"]:
        if record["control_id"] == "aum-published":
            record["approval_state"] = "proposed"

    with pytest.raises(VerificationError, match="not approved: aum-published"):
        verify_bundle(bundle)


class _ShiftingReport(Mapping):
    """A report whose sequence changes on every read of it.

    Live, not a fresh copy per read: a shallow `dict(envelope)` captures *this* object, so
    the mutation reaches anything that only copied the outer mapping. That is the whole
    difference between a shallow copy and a snapshot, and a hostile input that hands out
    fresh dicts cannot tell them apart.
    """

    def __init__(self, report: Mapping[str, object]) -> None:
        self._report = dict(report)
        self.reads = 0

    def __getitem__(self, key: str) -> object:
        if key != "sequence":
            return self._report[key]
        self.reads += 1
        return self.reads

    def __iter__(self):
        return iter(self._report)

    def __len__(self) -> int:
        return len(self._report)


def test_a_bundle_describes_one_report_even_if_the_caller_changes_it(
    tmp_path: Path,
) -> None:
    """`create_bundle` read the caller's report three times — to validate it, to
    canonicalize it, and to store it.

    A caller still holding it could therefore produce a bundle whose `report_canonical`
    describes one report and whose `signed_report` contains another: a bundle this
    function returns successfully and its own paired verifier immediately rejects.
    """
    epoch = _epoch(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid=signer.kid,
        compiler_provenance_digests=["33" * 32],
    )
    envelope = dict(signer.sign_report(report))
    shifting = _ShiftingReport(envelope["report"])
    envelope["report"] = shifting

    bundle = create_bundle(
        envelope,
        signer.public_key_record(),
        default_ustb_controls(),
        evidence_references(epoch),
    )

    assert shifting.reads == 1, "the caller's report was read exactly once"
    canonical = canonical_json_bytes(dict(bundle["signed_report"]["report"])).decode()
    assert bundle["report_canonical"] == canonical, (
        "the canonicalized report and the stored report are the same report"
    )


def test_a_bundle_keeps_the_key_and_digests_it_was_given(tmp_path: Path) -> None:
    """The published key and the evidence digests are caller-owned too.

    Both were copied one level deep, so a nested value stayed shared with the caller and
    could change after the bundle claimed to describe it.
    """
    epoch = _epoch(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid=signer.kid,
        compiler_provenance_digests=["33" * 32],
    )
    key = dict(signer.public_key_record())
    key["provenance"] = {"issued_by": "the operator"}
    digests = [dict(record) for record in evidence_references(epoch)]
    digests[0]["provenance"] = {"fetched_by": "the daemon"}

    bundle = create_bundle(
        signer.sign_report(report), key, default_ustb_controls(), digests
    )
    key["provenance"]["issued_by"] = "someone else"
    digests[0]["provenance"]["fetched_by"] = "someone else"

    assert bundle["published_key"]["provenance"] == {"issued_by": "the operator"}
    assert bundle["evidence_digests"][0]["provenance"] == {"fetched_by": "the daemon"}
