"""Produce a signed approval release for the decisions the ledger already records.

The approval ledger records what a human decided and when. What it never carried is *who*:
`approval_state` is a mutable field, and both external audits called that the weakest link in
the chain — the strongest defence against a plausible-but-wrong AI proposal is the human
boundary, and an unauthenticated boundary is a claim, not a control.

This signs each existing decision as an EIP-712 artifact carrying the approver's recoverable
address. Three rules shape it:

* **Nothing is backdated.** Every signature is timestamped at signing time. The ledger's own
  `approved_on`/`declined_on` dates remain the record of when the decision was *made*; the
  signature attests who stands behind it *now*. Signing artifacts dated 2026-08-17 in late
  August would manufacture history, which is the one thing this project must never do.
* **Nothing is re-decided.** The decisions, reasons and artifact bindings come from the
  ledger and the compilation artifacts, byte-for-byte. A release that quietly flipped a
  decision would be an approval forgery wearing a signature.
* **The digests are the bound ones.** `control_digest` is the *proposed* candidate's content
  hash — the control exactly as the compiler proposed it, before approval touched the two
  fields approval may touch — and `compilation_digest` names the artifact it came from. That
  matches `verify_signed_approval`, so every artifact is verified before it is written.

`--review` prints the decisions and signs nothing. The signing act is deliberate: the operator
reads the table, confirms these are still their decisions, and only then runs with the key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.approval import (  # noqa: E402
    APPROVED_KEY,
    DECLINED_KEY,
    LEDGER,
    accepted_candidates,
    ledger_from_bytes,
    load_compilation,
    sign_approval,
    verify_signed_approval,
)
from touchstone.controls import ControlRecord  # noqa: E402

APPROVER_KEY_ENV = "TOUCHSTONE_APPROVER_PRIVATE_KEY"
RELEASE_VERSION = "touchstone.signed-approval-release.v1"


def _proposed_digest(compilation_digest: str, control_id: str) -> str:
    """The content hash of the candidate exactly as the compiler proposed it."""
    compilation = load_compilation(compilation_digest)
    for candidate in accepted_candidates(compilation):
        if candidate["control_id"] == control_id:
            return ControlRecord.from_mapping(candidate).content_hash
    raise SystemExit(
        f"compilation {compilation_digest[:12]}… holds no accepted candidate named "
        f"{control_id}; the ledger names a decision this artifact cannot support"
    )


def decisions() -> list[dict[str, object]]:
    """Every decision in the ledger, resolved to the digests a signature binds."""
    ledger = ledger_from_bytes(LEDGER.read_bytes())
    rows: list[dict[str, object]] = []
    for decision, key, reason_default in (
        (APPROVED_KEY, "approved", "operator-approved"),
        (DECLINED_KEY, "declined", "operator-declined"),
    ):
        for entry in ledger[key]:
            rows.append(
                {
                    "decision": decision,
                    "control_id": entry["control_id"],
                    "compilation_digest": entry["compilation_sha256"],
                    "control_digest": _proposed_digest(
                        entry["compilation_sha256"], entry["control_id"]
                    ),
                    "decided_on": entry.get("approved_on") or entry.get("declined_on"),
                    "reason_code": reason_default,
                    "reason": entry.get("reason"),
                }
            )
    return rows


def review(rows: list[dict[str, object]]) -> None:
    print(
        f"{len(rows)} decisions in the ledger. Signing binds each exactly as shown:\n"
    )
    for row in rows:
        print(f"  {row['decision'].upper():<9} {row['control_id']}")
        print(
            f"            decided {row['decided_on']}  artifact {str(row['compilation_digest'])[:16]}…"
        )
        if row["reason"]:
            print(f"            reason: {str(row['reason'])[:96]}")
    print(
        "\nNothing signed. Run with --sign once these are confirmed as your decisions; the\n"
        "signatures will be timestamped at that moment, never backdated."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review", action="store_true", help="print the decisions, sign nothing"
    )
    parser.add_argument(
        "--sign", action="store_true", help="sign every decision with the approver key"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="release file; defaults to data/compilations/APPROVALS-SIGNED-<utc-date>.json",
    )
    arguments = parser.parse_args(argv)
    if arguments.review == arguments.sign:
        parser.error("exactly one of --review or --sign")

    rows = decisions()
    if arguments.review:
        review(rows)
        return 0

    key = os.environ.get(APPROVER_KEY_ENV)
    if not key:
        print(f"SIGN FAIL: {APPROVER_KEY_ENV} is not set", file=sys.stderr)
        return 1

    now = int(time.time())
    signed = []
    approver = None
    for row in rows:
        artifact = sign_approval(
            key,
            control_digest=row["control_digest"],
            compilation_digest=row["compilation_digest"],
            decision=row["decision"],
            reason_code=row["reason_code"],
            timestamp=now,
        )
        # Verified before it is written, and the recovered address must be one identity
        # across the whole release — a release signed by two keys is two releases.
        recovered = verify_signed_approval(artifact, expected_decision=row["decision"])
        if approver is None:
            approver = recovered
        elif recovered != approver:
            raise SystemExit("recovered approver changed mid-release")
        signed.append(
            {
                **artifact,
                "control_id": row["control_id"],
                "ledger_reason": row["reason"],
            }
        )

    out = Path(
        arguments.out
        or ROOT
        / "data"
        / "compilations"
        / f"APPROVALS-SIGNED-{time.strftime('%Y-%m-%d', time.gmtime(now))}.json"
    )
    if out.exists():
        print(
            f"SIGN FAIL: {out} already exists; a release is published once",
            file=sys.stderr,
        )
        return 1
    release = {
        "version": RELEASE_VERSION,
        "note": (
            "Signed approval release. Each artifact re-attests a decision the ledger already "
            "records, with the approver's recoverable address. Timestamps are the signing "
            "instant — the ledger's approved_on/declined_on dates record when each decision "
            "was made, and these signatures do not claim otherwise."
        ),
        "approver": approver,
        "signed_at": now,
        "approvals": signed,
    }
    out.write_text(
        json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"signed {len(signed)} decisions as {approver}")
    print(f"release written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
