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

PACK = Path(__file__).parent / "historical_pack.json"


def historical_ledger() -> dict:
    """The frozen ledger snapshot, read fresh so a caller cannot mutate the shared one."""
    return json.loads(PACK.read_bytes())


def historical_controls() -> tuple[ControlRecord, ...]:
    """The eight controls effective on the committed fixture dates."""
    return default_ustb_controls(historical_ledger())


def historical_ledger_bytes() -> bytes:
    """The frozen ledger as bytes, for the `approval_ledger=` boundary.

    `build_observation_report` and `verify_bundle` both default to the shipped ledger and
    both accept an explicit one. A report built from these controls has to be verified
    against the ledger they came from — otherwise the report commits to one control set and
    the verifier re-derives another, which is what "epoch evaluations do not match the
    control set" was reporting.
    """
    return PACK.read_bytes()
