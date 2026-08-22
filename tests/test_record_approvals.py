from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from scripts import record_approvals
from touchstone.approval import (
    APPROVED_KEY,
    DECLINED_KEY,
    LEDGER,
    accepted_candidates,
    load_approval_ledger,
    load_compilation,
)


KEY = "0x" + "11" * 32


def _workspace(tmp_path: Path) -> tuple[Path, Path, str, str]:
    ledger = tmp_path / "APPROVALS.json"
    shutil.copyfile(LEDGER, ledger)
    compilations = tmp_path / "compilations"
    compilations.mkdir()
    existing = json.loads(ledger.read_text(encoding="utf-8"))
    for key in ("approved", "declined"):
        for entry in existing[key]:
            digest = entry["compilation_sha256"]
            source = LEDGER.parent / f"{digest}.json"
            shutil.copyfile(source, compilations / source.name)

    source_digest = existing["approved"][0]["compilation_sha256"]
    source_compilation = load_compilation(source_digest)
    candidate = dict(accepted_candidates(source_compilation)[0])
    candidate["control_id"] = "new-control"
    compilation = {"outcomes": [{"status": "accepted", "control": candidate}]}
    raw = (json.dumps(compilation, indent=2) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    (compilations / f"{digest}.json").write_bytes(raw)
    return ledger, compilations, digest, candidate["control_id"]


def _decisions(
    tmp_path: Path,
    digest: str,
    control_id: str,
    *,
    decision: str = APPROVED_KEY,
    reason: str | None = None,
) -> Path:
    row = {
        "decision": decision,
        "control_id": control_id,
        "compilation_sha256": digest,
    }
    if reason is not None:
        row["reason"] = reason
    path = tmp_path / "decisions.json"
    path.write_text(
        json.dumps(
            {
                "version": "touchstone.approval-decisions.v1",
                "decisions": [row],
            }
        ),
        encoding="utf-8",
    )
    return path


def _arguments(
    ledger: Path, compilations: Path, decisions: Path, action: str
) -> list[str]:
    return [
        action,
        "--ledger",
        str(ledger),
        "--compilations",
        str(compilations),
        "--decisions",
        str(decisions),
    ]


def test_review_resolves_the_proposal_and_never_signs(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    ledger, compilations, digest, control_id = _workspace(tmp_path)
    decisions = _decisions(tmp_path, digest, control_id)
    before = ledger.read_bytes()

    def signing_is_forbidden(*args, **kwargs):
        raise AssertionError("review attempted to sign")

    monkeypatch.setattr(record_approvals, "sign_approval", signing_is_forbidden)

    assert (
        record_approvals.main(_arguments(ledger, compilations, decisions, "--review"))
        == 0
    )

    output = capsys.readouterr().out
    assert "1 new decision will be recorded" in output
    assert f"APPROVE   {control_id}" in output
    assert digest in output
    assert "Nothing signed or written." in output
    assert ledger.read_bytes() == before


def test_review_refuses_a_control_the_artifact_did_not_accept(
    tmp_path: Path, capsys
) -> None:
    ledger, compilations, digest, _ = _workspace(tmp_path)
    decisions = _decisions(tmp_path, digest, "not-an-accepted-candidate")

    assert (
        record_approvals.main(_arguments(ledger, compilations, decisions, "--review"))
        == 1
    )
    assert "holds no accepted candidate" in capsys.readouterr().err


def test_an_existing_control_id_cannot_be_redecided(tmp_path: Path, capsys) -> None:
    ledger, compilations, digest, _ = _workspace(tmp_path)
    existing = json.loads(ledger.read_text(encoding="utf-8"))["approved"][0]
    decisions = _decisions(tmp_path, digest, existing["control_id"])

    assert (
        record_approvals.main(_arguments(ledger, compilations, decisions, "--review"))
        == 1
    )
    assert "already has a recorded decision" in capsys.readouterr().err


def test_signing_appends_a_valid_entry_at_the_signing_instant_without_rewriting_history(
    tmp_path: Path, monkeypatch
) -> None:
    ledger, compilations, digest, control_id = _workspace(tmp_path)
    reason = "Declined exactly as reviewed."
    decisions = _decisions(
        tmp_path,
        digest,
        control_id,
        decision=DECLINED_KEY,
        reason=reason,
    )
    before = json.loads(ledger.read_text(encoding="utf-8"))
    before_signatures = [
        entry["approval"]["signature"]
        for key in ("approved", "declined")
        for entry in before[key]
    ]
    monkeypatch.setenv(record_approvals.APPROVER_KEY_ENV, KEY)
    monkeypatch.setattr(record_approvals.time, "time", lambda: 1_787_428_800)

    assert (
        record_approvals.main(_arguments(ledger, compilations, decisions, "--sign"))
        == 0
    )

    after = load_approval_ledger(ledger, directory=compilations)
    after_signatures = [
        entry["approval"]["signature"]
        for entry in [*after["approved"], *after["declined"][:-1]]
    ]
    entry = after["declined"][-1]
    assert after["approved"] == before["approved"]
    assert after["declined"][:-1] == before["declined"]
    assert after_signatures == before_signatures
    assert entry["control_id"] == control_id
    assert entry["reason"] == reason
    assert entry["declined_on"] == "2026-08-22"
    assert entry["approval"]["timestamp"] == 1_787_428_800
    assert entry["approval"]["compilation_digest"] == digest
    assert entry["approval"]["decision"] == DECLINED_KEY


def test_a_signing_failure_leaves_the_ledger_byte_identical(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    ledger, compilations, digest, control_id = _workspace(tmp_path)
    decisions = _decisions(tmp_path, digest, control_id)
    before = ledger.read_bytes()
    monkeypatch.setenv(record_approvals.APPROVER_KEY_ENV, KEY)

    def fail_signing(*args, **kwargs):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(record_approvals, "sign_approval", fail_signing)

    assert (
        record_approvals.main(_arguments(ledger, compilations, decisions, "--sign"))
        == 1
    )
    assert "synthetic failure" in capsys.readouterr().err
    assert ledger.read_bytes() == before


def test_a_clock_earlier_than_existing_history_cannot_backdate_a_decision(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    ledger, compilations, digest, control_id = _workspace(tmp_path)
    decisions = _decisions(tmp_path, digest, control_id)
    before = ledger.read_bytes()
    monkeypatch.setenv(record_approvals.APPROVER_KEY_ENV, KEY)
    monkeypatch.setattr(record_approvals.time, "time", lambda: 1)

    assert (
        record_approvals.main(_arguments(ledger, compilations, decisions, "--sign"))
        == 1
    )
    assert "backdating is forbidden" in capsys.readouterr().err
    assert ledger.read_bytes() == before
