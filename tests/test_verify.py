from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from touchstone.epoch import FixtureTransport, run_ustb_epoch
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import default_ustb_controls
from touchstone.report import build_observation_report
from touchstone.signing import Ed25519Signer, canonical_json_bytes
from touchstone.verify import VerificationError, create_bundle, main, verify_bundle


FIXTURES = Path(__file__).parents[1] / "fixtures"
RETRIEVED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)


def _bundle(tmp_path: Path):
    epoch = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES),
        store=EvidenceStore(tmp_path / "evidence"),
        now=date(2026, 8, 13),
        retrieved_at=RETRIEVED_AT,
    )
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-13",
        sequence=1,
        publisher_kid=signer.kid,
        observed_at=RETRIEVED_AT,
        valid_until=datetime(2026, 8, 13, 23, 59, 59, tzinfo=timezone.utc),
        compiler_provenance_digests=["33" * 32],
    )
    evidence = [
        {"source_id": source.source_id, "sha256": source.evidence_sha256}
        for source in epoch.sources
    ]
    return create_bundle(
        signer.sign_report(report),
        signer.public_key_record(),
        default_ustb_controls(),
        evidence,
    )


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
