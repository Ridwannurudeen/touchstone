"""The correction builder restates an epoch; it must never re-decide one.

Sequence 1 reached two public chains carrying an operational event of `RECONFIRMED` on a first
publication and a limitation reading "This local-only report". Signed bytes cannot be edited,
so the public record is repaired by a superseding report rather than by rewriting anything.

The danger in that is specific: a correction advertised as a wording fix that quietly carries a
different verdict would look like a typo and be a restatement of fact. So the builder refuses
unless the reconstruction reproduces what it corrects, and these tests hold it to that.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_correction.py"
BUNDLE = ROOT / "site2" / "data" / "ustb-2026-08-17-1.json"


def _evidence_index(tmp_path: Path, bundle: dict) -> Path:
    """An evidence index carrying the records the bundle's digests refer to."""
    lines = []
    for digest in bundle["evidence_digests"]:
        lines.append(json.dumps({
            "byte_size": 1024,
            "declared_mime": "application/json",
            "retrieved_at": digest["retrieved_at"],
            "sha256": digest["sha256"],
            "source_id": digest["source_id"],
            "source_url": f"https://api.superstate.com/{digest['source_id']}",
        }))
    path = tmp_path / "index.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(tmp_path: Path, *, sequence: int, index: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--bundle", str(BUNDLE),
         "--evidence-index", str(index),
         "--sequence", str(sequence),
         "--out", str(tmp_path / "correction.json")],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_the_correction_reproduces_the_report_it_corrects(tmp_path: Path) -> None:
    """Same roots, same state, same epoch — only the wording moves."""
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    original = bundle["signed_report"]["report"]

    finished = _run(tmp_path, sequence=3, index=_evidence_index(tmp_path, bundle))
    assert finished.returncode == 0, finished.stderr

    corrected = json.loads((tmp_path / "correction.json").read_text(encoding="utf-8"))

    for field in ("control_set_root", "evidence_root", "approval_ledger_sha256",
                  "asset_key", "epoch_id", "state"):
        assert corrected[field] == original[field], f"{field} moved"

    assert corrected["sequence"] == 3
    assert corrected["correction_of"] == original["sequence"]
    assert corrected["state_transition"]["event"] == "CORRECTION_PUBLISHED"

    # The two defects, and nothing else.
    assert not any("local-only" in text for text in corrected["limitations"])
    assert any("onchain NAV oracle" in text for text in corrected["limitations"])
    assert len(corrected["limitations"]) == len(original["limitations"])
    assert [c["control_id"] for c in corrected["controls"]] == [
        c["control_id"] for c in original["controls"]
    ]


def test_a_correction_cannot_precede_what_it_corrects(tmp_path: Path) -> None:
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    finished = _run(tmp_path, sequence=1, index=_evidence_index(tmp_path, bundle))
    assert finished.returncode != 0
    assert "must follow" in finished.stderr or "must follow" in finished.stdout


def test_evidence_that_does_not_match_the_bundle_is_refused(tmp_path: Path) -> None:
    """The root check is the safety property, so prove it actually bites.

    A correction built over different evidence would carry a different evidence root while
    still calling itself a restatement. Here one digest is altered; the builder must refuse
    rather than emit a report whose root silently disagrees with the one it corrects.
    """
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    index = _evidence_index(tmp_path, bundle)
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line]
    rows[0]["sha256"] = "ff" * 32
    index.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    finished = _run(tmp_path, sequence=3, index=index)
    assert finished.returncode != 0, "a mismatched evidence root was accepted"
    assert "does not reproduce" in finished.stderr
    assert not (tmp_path / "correction.json").exists(), "a refused correction was still written"
