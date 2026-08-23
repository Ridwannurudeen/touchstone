"""The binding between an approved control and the compilation that produced it.

This module had no tests of its own while being the thing that decides whether a control
set means anything. Every case here is a way the binding could be false while looking true:
an artifact that is not the one named, a candidate the compiler never accepted, an approval
that edited more than approval may edit, and a control a human explicitly declined.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from touchstone.approval import (
    APPROVED_KEY,
    APPROVAL_SCOPE_POLICY,
    DECLINED_KEY,
    LEDGER_VERSION,
    ApprovalError,
    approval_typed_data,
    approved_control,
    assert_binding,
    assert_entry_proposal,
    assert_ledger_approves,
    assert_ledger_permits,
    compilation_from_bytes,
    from_mapping,
    ledger_from_bytes,
    load_approval_ledger,
    provenance_digests,
    sign_approval,
    verify_signed_approval,
)
from historical_pack import historical_controls, historical_ledger_bytes
from touchstone.controls import ControlRecord
from touchstone.evaluate import default_ustb_controls


COMPILATIONS = Path(__file__).parents[1] / "data" / "compilations"
DECLINED = "ustb-outstanding-shares-present"


def artifacts() -> dict[str, bytes]:
    return {
        path.stem: path.read_bytes()
        for path in COMPILATIONS.glob("*.json")
        # The ledger and its dated signed releases live beside the artifacts they attest,
        # and neither is content-addressed: a compilation is named by what it is, a decision
        # record by what it decides about.
        if not path.stem.startswith(("APPROVALS", "DECISIONS"))
    }


def edited(control: ControlRecord, **changes: object) -> ControlRecord:
    mapping = control.to_mapping()
    mapping.update(changes)
    return ControlRecord.from_mapping(mapping)


def test_the_committed_control_set_is_bound_to_its_compilations() -> None:
    """The whole claim, checked against what is actually on disk."""
    controls = default_ustb_controls()

    # Five, deliberately hardcoded: this is the production lane, and a count that must be
    # edited by hand is what forces someone to look at a control set before it ships.
    assert len(controls) == 5
    for control in controls:
        assert control.approval_state == "approved"
        assert control.compilation_sha256 is not None
        assert_binding(control)
    assert provenance_digests(controls) == sorted(
        {control.compilation_sha256 for control in controls}
    )


def test_every_artifact_hashes_to_the_name_it_is_filed_under() -> None:
    for digest, raw in artifacts().items():
        assert hashlib.sha256(raw).hexdigest() == digest


def test_bytes_that_are_not_the_artifact_named_are_refused() -> None:
    digest, raw = next(iter(artifacts().items()))

    with pytest.raises(ApprovalError, match="is not the artifact named"):
        compilation_from_bytes(digest, raw + b" ")


def test_an_artifact_that_is_not_json_is_refused() -> None:
    raw = b"not json at all"
    with pytest.raises(ApprovalError, match="not readable JSON"):
        compilation_from_bytes(hashlib.sha256(raw).hexdigest(), raw)


def test_an_approved_control_naming_no_compilation_is_refused() -> None:
    control = edited(default_ustb_controls()[0], compilation_sha256=None)

    with pytest.raises(ApprovalError, match="names no compilation"):
        assert_binding(control)


def test_a_proposal_is_refused_rather_than_waved_through() -> None:
    """It used to return early, and its null digest then failed later as a bare TypeError."""
    control = edited(
        default_ustb_controls()[0], approval_state="proposed", compilation_sha256=None
    )

    with pytest.raises(ApprovalError, match="is a proposal"):
        assert_binding(control)
    with pytest.raises(ApprovalError, match="is a proposal"):
        provenance_digests([control])


def test_an_edit_beyond_approval_is_named_field_by_field() -> None:
    """Reporting only that something differs is useless; the point is to say what."""
    control = edited(default_ustb_controls()[0], grace_period=99, subject="rewritten")

    with pytest.raises(ApprovalError, match="grace_period, subject"):
        assert_binding(control)


def test_a_control_pointed_at_a_compilation_that_never_proposed_it() -> None:
    """Generic binding, not shipped policy — so it takes the frozen set.

    Two named control ids and two artifacts that really did not propose each other are all
    this needs, and the shipped set supplies neither once it is recompiled.
    """
    controls = {control.control_id: control for control in historical_controls()}
    yield_control = controls["ustb-one-day-yield-present"]
    other = controls["ustb-aum-published"].compilation_sha256

    with pytest.raises(ApprovalError, match="accepted no candidate"):
        assert_binding(edited(yield_control, compilation_sha256=other))


def test_a_declined_candidate_cannot_be_relabelled_approved() -> None:
    """The decline was decorative until the ledger was consulted.

    Resolution read only the artifact, which of course still contains the candidate a human
    rejected — so a control someone had explicitly refused resolved cleanly to `approved`.
    """
    ledger = load_approval_ledger()
    entry = next(
        item for item in ledger[DECLINED_KEY] if item["control_id"] == DECLINED
    )

    with pytest.raises(ApprovalError, match="was declined"):
        approved_control(
            {
                "control_id": DECLINED,
                "compilation_sha256": entry["compilation_sha256"],
            }
        )


def test_a_candidate_in_neither_list_is_refused() -> None:
    digest = default_ustb_controls()[0].compilation_sha256

    with pytest.raises(ApprovalError, match="not in the approval ledger"):
        assert_ledger_approves("a-control-nobody-ruled-on", digest)


def test_a_control_approved_twice_is_ambiguous() -> None:
    ledger = load_approval_ledger()
    entry = ledger[APPROVED_KEY][0]
    doubled = {
        **ledger,
        APPROVED_KEY: [*ledger[APPROVED_KEY], dict(entry)],
    }

    with pytest.raises(ApprovalError, match="approved 2 times"):
        assert_ledger_approves(
            entry["control_id"], entry["compilation_sha256"], ledger=doubled
        )


def test_the_ledger_records_why_each_declined_candidate_was_refused() -> None:
    """A control set that silently omits a rejected candidate cannot be audited for why."""
    ledger = load_approval_ledger()

    assert len(ledger[DECLINED_KEY]) == 11
    for entry in ledger[DECLINED_KEY]:
        assert entry["reason"].strip()
        assert entry["compilation_sha256"] in artifacts()
    # Reviewer identity is deliberately absent: there is no approver identity anywhere in
    # this project, and a placeholder would assert an attribution that does not exist.
    assert not any("reviewer" in entry for entry in ledger[DECLINED_KEY])


def test_a_signed_approval_recovers_its_approver_and_binds_the_proposal() -> None:
    control = default_ustb_controls()[0]
    signed = sign_approval(
        "0x" + "11" * 32,
        control_digest=edited(
            control, approval_state="proposed", compilation_sha256=None
        ).content_hash,
        compilation_digest=control.compilation_sha256,
        decision=APPROVED_KEY,
        reason_code="operator-confirmed",
        timestamp=1,
    )
    entry = {
        "control_id": control.control_id,
        "compilation_sha256": control.compilation_sha256,
        "approval": signed,
    }
    ledger = {
        "version": "touchstone.approval-ledger.v1",
        APPROVED_KEY: [entry],
        DECLINED_KEY: [],
    }

    assert verify_signed_approval(signed) == signed["approver"]
    assert signed["scope"] == "global"
    assert signed["policy_id"] == ""
    assert_ledger_permits([control], ledger)


def test_a_policy_scoped_approval_signs_its_scope_and_policy_id() -> None:
    signed = sign_approval(
        "0x" + "11" * 32,
        control_digest="00" * 32,
        compilation_digest="22" * 32,
        decision=APPROVED_KEY,
        reason_code="policy-owner-confirmed",
        timestamp=1,
        scope=APPROVAL_SCOPE_POLICY,
        policy_id="freshness-only",
    )
    typed_data = approval_typed_data(
        control_digest=signed["control_digest"],
        compilation_digest=signed["compilation_digest"],
        decision=signed["decision"],
        reason_code=signed["reason_code"],
        timestamp=signed["timestamp"],
        scope=signed["scope"],
        policy_id=signed["policy_id"],
    )

    assert typed_data["domain"]["version"] == "2"
    assert {field["name"] for field in typed_data["types"]["Approval"]} >= {
        "scope",
        "policyId",
    }
    assert typed_data["message"]["policyId"] == "freshness-only"
    assert (
        verify_signed_approval(
            signed,
            expected_scope=APPROVAL_SCOPE_POLICY,
            expected_policy_id="freshness-only",
        )
        == signed["approver"]
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("scope", "global"), ("policy_id", "nav-settlement")],
)
def test_a_signed_approval_rejects_scope_tampering(
    field: str, replacement: str
) -> None:
    signed = sign_approval(
        "0x" + "11" * 32,
        control_digest="00" * 32,
        compilation_digest="22" * 32,
        decision=APPROVED_KEY,
        reason_code="policy-owner-confirmed",
        timestamp=1,
        scope=APPROVAL_SCOPE_POLICY,
        policy_id="freshness-only",
    )
    tampered = {**signed, field: replacement}
    if field == "scope":
        tampered["policy_id"] = ""

    with pytest.raises(
        ApprovalError, match="approver does not match its signature"
    ):
        verify_signed_approval(tampered)


def test_a_policy_scoped_signature_cannot_approve_a_global_control() -> None:
    control = default_ustb_controls()[0]
    signed = sign_approval(
        "0x" + "11" * 32,
        control_digest=edited(
            control, approval_state="proposed", compilation_sha256=None
        ).content_hash,
        compilation_digest=control.compilation_sha256,
        decision=APPROVED_KEY,
        reason_code="policy-owner-confirmed",
        timestamp=1,
        scope=APPROVAL_SCOPE_POLICY,
        policy_id="freshness-only",
    )
    ledger = {
        "version": "touchstone.approval-ledger.v1",
        APPROVED_KEY: [
            {
                "control_id": control.control_id,
                "compilation_sha256": control.compilation_sha256,
                "approval": signed,
            }
        ],
        DECLINED_KEY: [],
    }

    with pytest.raises(ApprovalError, match="scope does not match its use"):
        assert_ledger_permits([control], ledger)


def test_a_legacy_signed_approval_remains_verifiable_without_a_scope() -> None:
    signed = {
        "version": 1,
        "control_digest": "00" * 32,
        "compilation_digest": "22" * 32,
        "decision": APPROVED_KEY,
        "reason_code": "legacy-fixture",
        "timestamp": 1,
        "approver": "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A",
        "signature": (
            "c00797f57998ec38cc91bc3ce3213aec6a68ebb027f1cb53bcea4c17d06401d1"
            "69735118e6a2359b8bd571d1ff7d6ac8f8c4e75390bc6f827826c42be5f54a2b1c"
        ),
    }

    assert verify_signed_approval(signed) == signed["approver"]
    assert "scope" not in signed
    assert "policy_id" not in signed


def test_a_signed_approval_cannot_be_replayed_over_a_different_proposal() -> None:
    control = default_ustb_controls()[0]
    signed = sign_approval(
        "0x" + "11" * 32,
        control_digest="00" * 32,
        compilation_digest=control.compilation_sha256,
        decision=APPROVED_KEY,
        reason_code="operator-confirmed",
        timestamp=1,
    )
    ledger = {
        "version": "touchstone.approval-ledger.v1",
        APPROVED_KEY: [
            {
                "control_id": control.control_id,
                "compilation_sha256": control.compilation_sha256,
                "approval": signed,
            }
        ],
        DECLINED_KEY: [],
    }

    with pytest.raises(ApprovalError, match="does not match the compiler proposal"):
        assert_ledger_permits([control], ledger)


def test_unsigned_legacy_approval_remains_readable_and_unattributed() -> None:
    control = default_ustb_controls()[0]
    ledger = {
        "version": "touchstone.approval-ledger.v1",
        APPROVED_KEY: [
            {
                "control_id": control.control_id,
                "compilation_sha256": control.compilation_sha256,
            }
        ],
        DECLINED_KEY: [],
    }

    assert_ledger_permits([control], ledger)


def signed_entry(control, decision: str) -> dict:
    signed = sign_approval(
        "0x" + "11" * 32,
        control_digest=edited(
            control, approval_state="proposed", compilation_sha256=None
        ).content_hash,
        compilation_digest=control.compilation_sha256,
        decision=decision,
        reason_code="operator-confirmed",
        timestamp=1,
    )
    return {
        "control_id": control.control_id,
        "compilation_sha256": control.compilation_sha256,
        "approval": signed,
    }


def test_a_version2_ledger_verifies_from_bytes_and_binds_from_disk(
    tmp_path: Path,
) -> None:
    controls = default_ustb_controls()
    ledger = {
        "version": LEDGER_VERSION,
        APPROVED_KEY: [signed_entry(controls[0], APPROVED_KEY)],
        DECLINED_KEY: [signed_entry(controls[1], DECLINED_KEY)],
    }
    raw = json.dumps(ledger).encode("utf-8")

    ledger_from_bytes(raw)
    location = tmp_path / "APPROVALS.json"
    location.write_bytes(raw)
    load_approval_ledger(location)


def test_a_version2_ledger_refuses_an_unsigned_entry() -> None:
    control = default_ustb_controls()[0]
    ledger = {
        "version": LEDGER_VERSION,
        APPROVED_KEY: [signed_entry(control, APPROVED_KEY)],
        DECLINED_KEY: [
            {
                "control_id": DECLINED,
                "compilation_sha256": control.compilation_sha256,
            }
        ],
    }

    with pytest.raises(ApprovalError, match="every entry"):
        ledger_from_bytes(json.dumps(ledger).encode("utf-8"))


def test_a_signed_decline_cannot_be_repurposed_for_another_control(
    tmp_path: Path,
) -> None:
    """The gap Codex named: a decline refuses by its outer control_id, and before the
    version-2 binding nothing tied that field to the digest the human actually signed."""
    controls = default_ustb_controls()
    # Two real candidates from one compilation, so the signature's compilation binding
    # holds while the control identity is swapped underneath it.
    donor, victim = controls[1], controls[2]
    assert donor.compilation_sha256 == victim.compilation_sha256
    entry = signed_entry(donor, DECLINED_KEY)
    entry["control_id"] = victim.control_id
    ledger = {
        "version": LEDGER_VERSION,
        APPROVED_KEY: [signed_entry(controls[0], APPROVED_KEY)],
        DECLINED_KEY: [entry],
    }
    raw = json.dumps(ledger).encode("utf-8")

    # From bytes alone the artifact is not there to check against; the binding is the
    # loader's job, and it refuses.
    ledger_from_bytes(raw)
    with pytest.raises(ApprovalError, match="does not match the compiler proposal"):
        assert_entry_proposal(entry)
    location = tmp_path / "APPROVALS.json"
    location.write_bytes(raw)
    with pytest.raises(ApprovalError, match="does not match the compiler proposal"):
        load_approval_ledger(location)


def test_an_in_memory_resolver_needs_no_filesystem() -> None:
    """What lets an offline verifier repeat the binding from a bundle alone."""
    resolve = from_mapping(artifacts())

    for control in default_ustb_controls():
        assert_binding(control, resolve=resolve)


def test_an_in_memory_resolver_refuses_an_artifact_it_does_not_hold() -> None:
    resolve = from_mapping({})

    with pytest.raises(ApprovalError, match="carries no compilation"):
        assert_binding(default_ustb_controls()[0], resolve=resolve)


def test_an_unsupported_ledger_version_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "APPROVALS.json"
    ledger.write_text(json.dumps({"version": "something.else"}), encoding="utf-8")

    with pytest.raises(ApprovalError, match="not a supported version"):
        load_approval_ledger(ledger)


def test_a_missing_ledger_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ApprovalError, match="no approval ledger"):
        load_approval_ledger(tmp_path / "absent.json")


def test_the_frozen_pack_is_the_ledger_the_published_report_was_signed_under() -> None:
    """The digest, pinned as a literal, because every other check compares it to itself.

    `tests/historical_pack.json` is a copy of the approval ledger that the live USTB
    sequence-1 report committed to on X Layer testnet on 2026-08-17. Its digest is that
    report's `approval_ledger_sha256`, so this is not a fixture asserting its own shape — it
    is a fixture asserting it is still the object a published report is bound to.

    It needs a literal because the failure mode is invisible to any relative comparison. This
    file was checked out with LF while the ledger the daemon read held CRLF: identical text,
    zero diff lines, 60 bytes shorter, different digest. The whole suite stayed green, because
    each test threaded the pack into a boundary and compared it against the pack. Only the
    published report disagreed, and nothing was reading that.

    If this fails after a fresh clone, the `-text` rule for this path in `.gitattributes` was
    lost, not the ledger.
    """
    digest = hashlib.sha256(historical_ledger_bytes()).hexdigest()

    assert digest == (
        "14857c704b878bf3c5715673752d2a3d464a3340626c9da708ad696e905918c4"
    ), (
        "the frozen pack is no longer the ledger USTB sequence 1 was signed under; "
        "line-ending translation is the usual cause"
    )
