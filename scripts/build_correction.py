"""Build a superseding report that restates an epoch without re-deciding it.

Sequence 1 went onto two public chains carrying two defects in its signed bytes: an operational
event of `RECONFIRMED` on a first publication, which asserts a history that did not exist, and
a limitation reading "This local-only report" on a report published to a chain. Signed bytes
cannot be edited. The registry's answer to that is `publishCorrection`, and this builds what it
publishes.

**The correction restates; it does not re-evaluate.** It reconstructs the original epoch from
the bundle and the retained evidence, and then refuses to continue unless the reconstruction
reproduces the published `control_set_root` and `evidence_root` exactly. That check is the
whole safety property: a "wording correction" that quietly carried a different verdict would
look like a typo fix and be a restatement of fact, which is the worst artifact this project
could publish.

Nothing is fetched. Re-fetching would ask the issuer what is true now, which is a different
question, and would turn a restatement into a new observation.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from touchstone.approval import ledger_from_bytes  # noqa: E402
from touchstone.controls import (  # noqa: E402
    AssetState,
    EvaluationResult,
    OperationalEvent,
)
from touchstone.epoch import (  # noqa: E402
    EpochControlReport,
    EpochSourceReport,
    USTBEpochReport,
)
from touchstone.evaluate import default_ustb_controls  # noqa: E402
from touchstone.report import (  # noqa: E402
    build_observation_report,
    evidence_references,
)
from touchstone.signing import Ed25519Signer  # noqa: E402
from touchstone.verify import create_bundle, verify_bundle  # noqa: E402


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sources(entries: list[dict], retrieved_at: str, observed_on: date):
    """Rebuild the epoch's source reports from the evidence store's own records.

    The store is the authority on what was retrieved: the bundle carries digests, not the
    byte size, MIME or URL. Only `source_id`, `retrieved_at` and `sha256` reach the evidence
    root, so a faithful rebuild reproduces it and the rest is recorded truthfully rather than
    guessed at.
    """
    return tuple(
        EpochSourceReport(
            source_id=entry["source_id"],
            source_url=entry["source_url"],
            content_type=entry["declared_mime"],
            byte_size=entry["byte_size"],
            evidence_sha256=entry["sha256"],
            retrieved_at=_instant(entry["retrieved_at"]),
            observed_on=observed_on,
        )
        for entry in entries
        if entry["retrieved_at"] == retrieved_at
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle", required=True, help="bundle of the report being corrected"
    )
    parser.add_argument("--evidence-index", required=True, help="evidence index.jsonl")
    parser.add_argument(
        "--sequence", type=int, required=True, help="sequence for the correction"
    )
    parser.add_argument(
        "--out", required=True, help="where to write the corrected report"
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="sign the correction and write a verified bundle beside it",
    )
    args = parser.parse_args()

    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    original = bundle["signed_report"]["report"]
    corrected_sequence = original["sequence"]

    if args.sequence <= corrected_sequence:
        raise SystemExit(
            f"correction sequence {args.sequence} must follow {corrected_sequence}"
        )

    ledger = bundle["approval_ledger"].encode("utf-8")
    controls = default_ustb_controls(ledger_from_bytes(ledger))

    transition = original["state_transition"]
    entries = [
        json.loads(line)
        for line in Path(args.evidence_index).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    evaluations = tuple(
        EpochControlReport(
            control_id=item["control_id"],
            result=EvaluationResult(item["evaluation"]["result"]),
            observed_value=item["evaluation"]["observed_value"],
            evidence_deadline=_date(item["evaluation"]["evidence_deadline"]),
            observed_on=_date(item["evaluation"]["observed_on"]),
        )
        for item in original["controls"]
    )

    current = next(
        digest
        for digest in bundle["evidence_digests"]
        if digest["capture_role"] == "current"
    )
    observed_on = date.fromisoformat(transition["as_of"])

    epoch = USTBEpochReport(
        asset_key=original["asset_key"],
        now=observed_on,
        state=AssetState(original["state"]),
        evidence_deadline=date.fromisoformat(transition["evidence_deadline"]),
        sources=_sources(entries, current["retrieved_at"], observed_on),
        evaluations=evaluations,
        confirmation=None,
    )

    report = build_observation_report(
        epoch,
        controls,
        epoch_id=original["epoch_id"],
        sequence=args.sequence,
        correction_of=corrected_sequence,
        publisher_kid=original["publisher_kid"],
        previous_state=AssetState(transition["previous_state"]),
        event=OperationalEvent.CORRECTION_PUBLISHED,
        approval_ledger=ledger,
    )

    # The safety property. A restatement that cannot reproduce what it restates is not a
    # restatement, and every later check would be verifying the wrong thing consistently.
    problems = []
    for field in (
        "control_set_root",
        "evidence_root",
        "approval_ledger_sha256",
        "asset_key",
        "epoch_id",
        "state",
    ):
        if report[field] != original[field]:
            problems.append(f"{field}: {original[field]} -> {report[field]}")
    if problems:
        print(
            "REFUSING — the reconstruction does not reproduce the report it corrects:",
            file=sys.stderr,
        )
        for line in problems:
            print("  ", line, file=sys.stderr)
        return 1

    removed = [t for t in original["limitations"] if t not in report["limitations"]]
    added = [t for t in report["limitations"] if t not in original["limitations"]]

    out = Path(args.out)
    signed = None
    if args.sign:
        # Only after the reconstruction has been proven faithful. Signing first would create a
        # signature over bytes nobody had checked, and a signature is the one thing here that
        # cannot be taken back.
        signer = Ed25519Signer.from_env()
        if signer.kid != original["publisher_kid"]:
            print(
                f"REFUSING: signing key is {signer.kid}, but the report being corrected was "
                f"signed by {original['publisher_kid']}. A correction signed by a different "
                "key is a different claim, not a correction.",
                file=sys.stderr,
            )
            return 1
        signed = signer.sign_report(report)
        made = create_bundle(
            signed,
            signer.public_key_record(),
            controls,
            evidence_references(epoch),
            approval_ledger=ledger,
        )
        # Built is not the same claim as verifiable, and only one of them is publishable.
        verify_bundle(made)
        out.with_name(out.stem + "-bundle.json").write_text(
            json.dumps(made, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    out.write_text(
        json.dumps(signed if signed else report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        f"correction built: sequence {args.sequence}, correction_of {corrected_sequence}"
    )
    print(f"  epoch            {report['epoch_id']}  (restated, not re-decided)")
    print(f"  state            {report['state']}  (unchanged)")
    print(
        f"  event            {original['state_transition']['event']}"
        f" -> {report['state_transition']['event']}"
    )
    print(f"  control_set_root {report['control_set_root']}  (identical)")
    print(f"  evidence_root    {report['evidence_root']}  (identical)")
    for text in removed:
        print(f"  limitation removed: {text}")
    for text in added:
        print(f"  limitation added  : {text}")
    if signed is not None:
        print(f"\nsigned by {original['publisher_kid']}")
        print(f"  report {out}")
        print(
            f"  bundle {out.with_name(out.stem + '-bundle.json')}  (verified offline)"
        )
        print("Nothing is published. That is a separate, owner-approved transaction.")
    else:
        print(f"\nwritten to {args.out}. Nothing is signed and nothing is published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
