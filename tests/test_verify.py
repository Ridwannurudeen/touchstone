from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path

import pytest

from touchstone.controls import ControlRecord
from touchstone.epoch import FixtureTransport, run_ustb_epoch
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import default_ustb_controls
from touchstone.report import (
    build_observation_report,
    control_set_root,
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
        if control["control_id"] == "ustb-aum-published":
            control["evaluation"]["observed_on"] = "2099-01-01"

    with pytest.raises(VerificationError, match="later than the evaluated epoch"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_value_with_no_evidence_date(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    report = deepcopy(bundle["signed_report"]["report"])
    for control in report["controls"]:
        if control["control_id"] == "ustb-aum-published":
            control["evaluation"]["observed_on"] = None

    with pytest.raises(VerificationError, match="not attributed to an evidence date"):
        verify_bundle(_resign(bundle, report))


def test_verifier_rejects_a_conclusive_evaluation_with_no_evidence_date(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    report = deepcopy(bundle["signed_report"]["report"])
    for control in report["controls"]:
        if control["control_id"] == "ustb-aum-published":
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
        if control["control_id"] == "ustb-aum-published":
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
    proposals. The binding between a control and the compilation that produced it is
    enforced separately, at report construction.
    """
    bundle = _bundle(tmp_path)
    for record in bundle["control_records"]:
        if record["control_id"] == "ustb-aum-published":
            record["approval_state"] = "proposed"

    with pytest.raises(VerificationError, match="not approved: ustb-aum-published"):
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


def test_a_bundle_holds_every_control_of_a_single_pass_sequence(
    tmp_path: Path,
) -> None:
    """The controls were validated by one pass over the sequence and used by another.

    A sequence is not obliged to be re-readable. Validating a generator consumed it, so
    the bundle was built from what remained — nothing — and `create_bundle` returned,
    reporting no error, a bundle holding none of the controls it had just approved.
    """
    epoch = _epoch(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid=signer.kid,
    )
    controls = default_ustb_controls()
    digests = evidence_references(epoch)

    bundle = create_bundle(
        signer.sign_report(report),
        signer.public_key_record(),
        (record for record in controls),
        (record for record in digests),
    )

    assert len(bundle["control_records"]) == len(controls)
    assert len(bundle["evidence_digests"]) == len(digests)
    verified = verify_bundle(canonical_json_bytes(bundle))
    assert verified["state"] == "CONFIRMED"


def test_a_verified_report_does_not_change_when_the_caller_mutates_the_bundle(
    tmp_path: Path,
) -> None:
    """A mapping passed to `verify_bundle` stays owned by its caller.

    Bytes and text are safe already, because parsing produces owned data. A mapping was
    not: the caller held the very object returned as the verified report, and could change
    it after verification had vouched for it.
    """
    bundle = _bundle(tmp_path)

    verified = verify_bundle(bundle)
    expected = deepcopy(dict(verified))
    assert verified["state"] == "CONFIRMED"

    bundle["signed_report"]["report"]["state"] = "STALE"
    bundle["signed_report"]["report"]["sequence"] = 99

    assert verified == expected


def test_a_bundle_carries_the_compilations_its_report_cites(tmp_path: Path) -> None:
    """The claim "a compiler proposed these controls" is now checkable, not merely made.

    Before this the report named compilation digests and an offline verifier confirmed only
    that they were lowercase hexadecimal. A bundle could assert compiler provenance while
    carrying nothing anyone could resolve, so a reader had to take the claim from the party
    making it.
    """
    bundle = _bundle(tmp_path)

    assert set(bundle["compilations"]) == set(
        bundle["signed_report"]["report"]["compiler_provenance_digests"]
    )
    for digest, text in bundle["compilations"].items():
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest


def test_a_bundle_missing_a_compilation_it_cites_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle["compilations"].popitem()

    with pytest.raises(VerificationError, match="do not match the report's provenance"):
        verify_bundle(bundle)


def test_a_bundle_carrying_a_compilation_it_does_not_cite_is_refused(
    tmp_path: Path,
) -> None:
    """A surplus artifact is one the report does not stand behind."""
    bundle = _bundle(tmp_path)
    bundle["compilations"]["ab" * 32] = "{}"

    with pytest.raises(VerificationError, match="unexpected"):
        verify_bundle(bundle)


def test_an_altered_compilation_no_longer_hashes_to_its_digest(tmp_path: Path) -> None:
    """Editing an artifact to accept something it did not is caught by the hash."""
    bundle = _bundle(tmp_path)
    bundle["compilations"] = {
        digest: text.replace("accepted", "rejected", 1)
        for digest, text in bundle["compilations"].items()
    }

    with pytest.raises(VerificationError, match="is not the artifact named"):
        verify_bundle(bundle)


def test_compilations_swapped_under_each_others_digests_are_refused(
    tmp_path: Path,
) -> None:
    """Filing a real artifact under another real artifact's digest proves nothing."""
    bundle = _bundle(tmp_path)
    digests = sorted(bundle["compilations"])
    values = [bundle["compilations"][digest] for digest in digests]
    bundle["compilations"] = dict(zip(digests, reversed(values)))

    with pytest.raises(VerificationError, match="is not the artifact named"):
        verify_bundle(bundle)


def test_a_control_edited_after_approval_is_refused_by_the_verifier(
    tmp_path: Path,
) -> None:
    """The verifier repeats the binding rather than trusting the publisher's word on it."""
    bundle = _bundle(tmp_path)
    for record in bundle["control_records"]:
        if record["control_id"] == "ustb-aum-published":
            record["grace_period"] = record["grace_period"] + 5
    report = deepcopy(bundle["signed_report"]["report"])
    report["control_set_root"] = control_set_root(
        [ControlRecord.from_mapping(record) for record in bundle["control_records"]]
    )
    for item in report["controls"]:
        if item["control_id"] == "ustb-aum-published":
            item["content_hash"] = next(
                ControlRecord.from_mapping(record).content_hash
                for record in bundle["control_records"]
                if record["control_id"] == "ustb-aum-published"
            )

    with pytest.raises(VerificationError, match="differs from the candidate"):
        verify_bundle(_resign(bundle, report))
