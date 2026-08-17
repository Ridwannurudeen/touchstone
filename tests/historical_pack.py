"""The control set the committed fixtures were approved under, frozen in place.

`default_ustb_controls()` reads whatever the project ships today, and a newly compiled
control is effective from its compile date — so it can never evaluate a fixture captured
earlier. A suite pinned to the shipped set therefore breaks on every recompile, and one
did: eighty tests failed at once when a 2026-08-17 set was approved against 08-14 fixtures,
all of them reporting a transition-rule mismatch that had nothing to do with what they were
testing.

Tests about canonicalisation, signatures, roots, tampering, provenance and transition rules
are not tests about business policy. They take their controls from here, where the set is a
committed fact rather than a moving one. The narrow lane that must exercise what actually
ships keeps using `default_ustb_controls()` — see `test_approval.py` and the local-chain
end-to-end run.

This is a frozen copy, not a mock: the controls still resolve through the real approval
boundary and still bind to the real committed artifacts, so a test using them proves the
same things about the same code.
"""

from __future__ import annotations

import json
from pathlib import Path

from touchstone.controls import ControlRecord
from touchstone.evaluate import default_ustb_controls

# A byte-for-byte copy of the approval ledger the live USTB sequence-1 report was signed
# under, taken from that report's own bundle. It hashes to
# 14857c704b878bf3c5715673752d2a3d464a3340626c9da708ad696e905918c4, which is exactly the
# `approval_ledger_sha256` committed in the report published to X Layer testnet on
# 2026-08-17. That equality is the point: this is not a plausible reconstruction of the old
# set, it is the object a published report is bound to.
#
# Byte-for-byte matters and is not tidiness, and it took two attempts to get right. Raw ledger
# bytes are hashed into every report, so a copy differing only in key order or a note has a
# different digest and is not the ledger anything was signed under — the first version was a
# reconstruction carrying its own explanatory note and hashed to 7aaa7a17…. The second was a
# copy of the file on disk, hashing to 61837371…, and looked correct: same eight decisions,
# zero diff lines against the published ledger. It was still wrong. `core.autocrlf` is true
# here and this path had no `-text` rule, so the copy held LF where the ledger the daemon
# actually read held CRLF. Identical text, 60 bytes shorter, different digest. Every test
# passed throughout, because each compared the pack against itself.
#
# `.gitattributes` now marks this path `-text`. Explanation belongs here, in the module, where
# it changes no bytes.
PACK = Path(__file__).parent / "historical_pack.json"


def historical_ledger() -> dict:
    """The frozen ledger snapshot, read fresh so a caller cannot mutate the shared one."""
    return json.loads(PACK.read_bytes())


def historical_controls() -> tuple[ControlRecord, ...]:
    """The eight controls effective on the committed fixture dates."""
    return default_ustb_controls(historical_ledger())


def historical_ledger_bytes() -> bytes:
    """The frozen ledger as bytes, for the `approval_ledger=` boundary.

    `build_observation_report` and `create_bundle` each default to the shipped ledger and
    each accept an explicit one, so a report built from these controls must be given the
    ledger they came from — otherwise the report commits to one control set and the bundle
    carries another, which is what "epoch evaluations do not match the control set" was
    reporting.

    `verify_bundle` takes no ledger and defaults to nothing: it reads the one embedded in
    the bundle, which is what makes it an offline verifier. An earlier version of this
    docstring said it behaved like the other two. It does not, and passing one raises
    TypeError — which is how the claim was found to be wrong.
    """
    return PACK.read_bytes()
