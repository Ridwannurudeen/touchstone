from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from eth_account import Account

from historical_pack import historical_controls, historical_ledger_bytes
from touchstone.epoch import FixtureTransport, run_ustb_epoch_reports
from touchstone.evidence import EvidenceStore
from touchstone.policy import MANIFEST_VERSION, Policy, select

from touchstone.registry_v2 import (
    RegistryV2Error,
    attestation_from_report,
    attestation_eip712_digest,
    publish_calldata,
    policy_id_digest,
    registry_asset_key,
    report_digest,
    sign_attestation,
    verify_attestation,
)
from touchstone.report import (
    REPORT_VERSION_V4,
    build_observation_report,
    evidence_references,
)
from touchstone.signing import Ed25519Signer, canonical_json_bytes
from touchstone.verify import (
    BUNDLE_VERSION,
    BUNDLE_VERSION_REGISTRY_V2,
    BUNDLE_VERSION_V4,
    VerificationError,
    create_bundle,
    create_registry_v2_bundle,
    verify_bundle,
    verify_v2_attestation,
)


KEY = "0x" + "11" * 32
PUBLISHER = Account.from_key(KEY).address
CONTRACT = "0x" + "22" * 20
ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parents[1] / "fixtures"
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)


def value() -> dict[str, object]:
    return {
        "asset_key": "99" * 32,
        "report_digest": "33" * 32,
        "policy_id": "44" * 32,
        "policy_root": "55" * 32,
        "control_set_root": "66" * 32,
        "evidence_root": "77" * 32,
        "epoch_key": "aa" * 32,
        "status": 0,
        "observed_at": 1_700_000_000,
        "publisher": PUBLISHER,
        "valid_until": 1_800_000_000,
        "sequence": 3,
        "parent_digest": "88" * 32,
        "correction_of": 1,
        "report_uri": "ipfs://touchstone-v2",
        "chain_id": 196,
        "verifying_contract": CONTRACT,
    }


def _policy_manifest(control_id: str) -> bytes:
    return json.dumps(
        {
            "version": MANIFEST_VERSION,
            "policy_id": "freshness-only",
            "policy_version": 1,
            "asset_key": historical_controls()[0].asset_key,
            "title": "Freshness only",
            "consumer_question": "Is the issuer publication current?",
            "controls": [control_id],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bundle_inputs(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    controls = historical_controls()
    control_id = "ustb-nav-date-freshness"
    manifest = _policy_manifest(control_id)
    policy = Policy(
        policy_id="freshness-only",
        version=1,
        asset_key=controls[0].asset_key,
        title="Freshness only",
        consumer_question="Is the issuer publication current?",
        control_ids=(control_id,),
        digest=hashlib.sha256(manifest).hexdigest(),
    )
    store = EvidenceStore(tmp_path / "evidence")
    run_ustb_epoch_reports(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
        controls=controls,
    )
    asset_epoch, policy_epoch = run_ustb_epoch_reports(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
        controls=controls,
        policies=(policy,),
    )
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    policy_controls = select(policy, controls)
    report = build_observation_report(
        policy_epoch,
        policy_controls,
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid=signer.kid,
        approval_ledger=historical_ledger_bytes(),
        policy=policy,
    )
    attestation = sign_attestation(
        KEY,
        **attestation_from_report(
            report,
            publisher=PUBLISHER,
            parent_digest="0" * 64,
            correction_of=0,
            report_uri="ipfs://touchstone-v2-policy-report",
            chain_id=196,
            verifying_contract=CONTRACT,
        ),
    )
    v2_bundle = create_registry_v2_bundle(
        signer.sign_report(report),
        signer.public_key_record(),
        policy_controls,
        evidence_references(policy_epoch),
        registry_v2_attestation=attestation,
        approval_ledger=historical_ledger_bytes(),
        policy_manifest=manifest,
    )

    asset_report = build_observation_report(
        asset_epoch,
        controls,
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid=signer.kid,
        approval_ledger=historical_ledger_bytes(),
    )
    v5_bundle = create_bundle(
        signer.sign_report(asset_report),
        signer.public_key_record(),
        controls,
        evidence_references(asset_epoch),
        approval_ledger=historical_ledger_bytes(),
    )
    return v2_bundle, v5_bundle


def _resign_attestation(bundle: dict[str, object]) -> None:
    unsigned = deepcopy(bundle["registry_v2_attestation"])
    unsigned.pop("signature")
    bundle["registry_v2_attestation"] = sign_attestation(KEY, **unsigned)


def test_report_digest_matches_the_signed_report_bytes() -> None:
    report = {"state": "UNVERIFIABLE", "sequence": 1}
    assert report_digest(report) == hashlib.sha256(
        canonical_json_bytes(report)
    ).hexdigest()


def test_policy_report_derives_every_attestation_field() -> None:
    report = {
        "asset_key": "eip155:1:0x" + "ab" * 20 + "#policy:nav-settlement:2",
        "control_set_root": "66" * 32,
        "epoch_id": "2026-08-19",
        "evidence_root": "77" * 32,
        "observed_at": "2026-08-19T10:00:00Z",
        "policy": {
            "control_ids": ["nav"],
            "policy_digest": "55" * 32,
            "policy_id": "nav-settlement",
            "policy_version": 2,
        },
        "sequence": 3,
        "state": "UNVERIFIABLE",
        "valid_until": "2026-08-20T10:00:00Z",
    }

    derived = attestation_from_report(
        report,
        publisher=PUBLISHER,
        parent_digest="88" * 32,
        correction_of=1,
        report_uri="ipfs://touchstone-v2",
        chain_id=196,
        verifying_contract=CONTRACT,
    )

    assert derived["report_digest"] == report_digest(report)
    assert derived["policy_id"] == policy_id_digest("nav-settlement", 2)
    assert derived["policy_root"] == report["policy"]["policy_digest"]
    assert derived["status"] == 3
    assert derived["observed_at"] == 1_787_133_600
    assert derived["valid_until"] == 1_787_220_000


def test_asset_wide_report_cannot_be_presented_as_policy_bound_v2() -> None:
    with pytest.raises(RegistryV2Error, match="policy-bound"):
        attestation_from_report(
            {"policy": None},
            publisher=PUBLISHER,
            parent_digest="00" * 32,
            correction_of=0,
            report_uri="ipfs://touchstone-v2",
            chain_id=196,
            verifying_contract=CONTRACT,
        )


def test_python_matches_the_sdk_policy_vector() -> None:
    vector = json.loads(
        (ROOT / "sdk" / "fixtures" / "registry-v2-policy-vector.json").read_text(
            encoding="utf-8"
        )
    )

    assert registry_asset_key(vector["reportAssetKey"]) == vector["assetKey"][2:]
    assert (
        policy_id_digest(vector["policyId"], vector["policyVersion"])
        == vector["policyIdHash"][2:]
    )
    assert "0x" + vector["policyDigest"] == vector["policyRoot"]


def test_v2_attestation_recovers_and_matches_the_contract_digest() -> None:
    signed = sign_attestation(KEY, **value())

    assert verify_attestation(signed) == PUBLISHER
    assert verify_v2_attestation(signed) == PUBLISHER
    assert len(attestation_eip712_digest(signed)) == 64


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("asset_key", "98" * 32),
        ("report_digest", "32" * 32),
        ("policy_id", "43" * 32),
        ("policy_root", "54" * 32),
        ("control_set_root", "65" * 32),
        ("evidence_root", "76" * 32),
        ("epoch_key", "a9" * 32),
        ("status", 1),
        ("observed_at", 1_700_000_001),
        ("valid_until", 1_800_000_001),
        ("publisher", "0x" + "12" * 20),
        ("sequence", 4),
        ("parent_digest", "87" * 32),
        ("correction_of", 2),
        ("report_uri", "ipfs://tampered"),
        ("chain_id", 1952),
        ("verifying_contract", "0x" + "23" * 20),
    ],
)
def test_v2_attestation_tampering_fails_recovery(
    field: str, changed: object
) -> None:
    signed = sign_attestation(KEY, **value())
    signed[field] = changed

    with pytest.raises(RegistryV2Error, match="signature"):
        verify_attestation(signed)
    with pytest.raises(VerificationError, match="does not verify"):
        verify_v2_attestation(signed)


def test_v2_calldata_is_binary_transaction_data() -> None:
    signed = sign_attestation(KEY, **value())
    report_input = (
        signed["asset_key"],
        signed["report_digest"],
        signed["policy_id"],
        signed["policy_root"],
        signed["control_set_root"],
        signed["evidence_root"],
        signed["epoch_key"],
        signed["status"],
        signed["observed_at"],
        signed["valid_until"],
        signed["publisher"],
        signed["sequence"],
        signed["parent_digest"],
        signed["report_uri"],
    )

    calldata = publish_calldata(
        registry_address=CONTRACT,
        report_input=report_input,
        signature=signed["signature"],
    )
    assert isinstance(calldata, bytes)
    assert len(calldata) > 4


def test_registry_v2_bundle_binds_both_signatures_and_policy(tmp_path: Path) -> None:
    bundle, _ = _bundle_inputs(tmp_path)

    verified = verify_bundle(canonical_json_bytes(bundle))

    assert bundle["version"] == BUNDLE_VERSION_REGISTRY_V2
    assert verified["policy"]["policy_id"] == "freshness-only"


def test_registry_v2_bundle_rejects_resigned_binding_tamper(tmp_path: Path) -> None:
    bundle, _ = _bundle_inputs(tmp_path)
    attestation = bundle["registry_v2_attestation"]
    changes = {
        "asset_key": "98" * 32,
        "report_digest": "32" * 32,
        "policy_id": "43" * 32,
        "policy_root": "54" * 32,
        "control_set_root": "65" * 32,
        "evidence_root": "76" * 32,
        "epoch_key": "a9" * 32,
        "status": 1,
        "observed_at": attestation["observed_at"] + 1,
        "valid_until": attestation["valid_until"] + 1,
        "sequence": 2,
    }

    for field, changed in changes.items():
        tampered = deepcopy(bundle)
        tampered["registry_v2_attestation"][field] = changed
        _resign_attestation(tampered)
        with pytest.raises(VerificationError, match=field):
            verify_bundle(tampered)


def test_registry_v2_bundle_rejects_signature_schema_and_parent_tamper(
    tmp_path: Path,
) -> None:
    bundle, _ = _bundle_inputs(tmp_path)
    bad_signature = deepcopy(bundle)
    bad_signature["registry_v2_attestation"]["signature"] = "00" * 65
    with pytest.raises(VerificationError, match="does not verify"):
        verify_bundle(bad_signature)

    extra_field = deepcopy(bundle)
    extra_field["registry_v2_attestation"]["unexpected"] = True
    with pytest.raises(VerificationError, match="fields do not match"):
        verify_bundle(extra_field)

    bad_parent = deepcopy(bundle)
    bad_parent["registry_v2_attestation"]["parent_digest"] = "89" * 32
    _resign_attestation(bad_parent)
    with pytest.raises(VerificationError, match="zero parent_digest"):
        verify_bundle(bad_parent)


def test_registry_v2_bundle_rejects_a_different_valid_ed25519_report(
    tmp_path: Path,
) -> None:
    bundle, _ = _bundle_inputs(tmp_path)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    report = deepcopy(bundle["signed_report"]["report"])
    report["limitations"][0] += " changed"
    bundle["signed_report"] = signer.sign_report(report)
    bundle["report_canonical"] = canonical_json_bytes(report).decode("utf-8")

    with pytest.raises(VerificationError, match="report_digest"):
        verify_bundle(bundle)


def test_registry_v2_path_retains_v5_and_v4_verification(tmp_path: Path) -> None:
    _, v5_bundle = _bundle_inputs(tmp_path)
    assert v5_bundle["version"] == BUNDLE_VERSION
    assert verify_bundle(v5_bundle)["version"] != REPORT_VERSION_V4

    signer = Ed25519Signer.from_seed(bytes(range(32)))
    v4_bundle = deepcopy(v5_bundle)
    report = deepcopy(v4_bundle["signed_report"]["report"])
    report.pop("policy")
    report["version"] = REPORT_VERSION_V4
    v4_bundle["signed_report"] = signer.sign_report(report)
    v4_bundle["report_canonical"] = canonical_json_bytes(report).decode("utf-8")
    v4_bundle.pop("policy_manifest")
    v4_bundle["version"] = BUNDLE_VERSION_V4

    assert verify_bundle(v4_bundle)["version"] == REPORT_VERSION_V4
