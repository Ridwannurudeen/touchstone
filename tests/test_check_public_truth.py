from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "check_public_truth",
    Path(__file__).parents[1] / "scripts" / "check_public_truth.py",
)
assert SPEC is not None and SPEC.loader is not None
check_public_truth = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_public_truth
SPEC.loader.exec_module(check_public_truth)


def test_visible_status_counts_are_parsed_across_markup() -> None:
    document = """
    <p><strong>17 reports</strong>, of which
    <strong>12 reached <code>CONFIRMED</code></strong>.</p>
    <p><strong>9</strong> permit/refuse enforcement transactions.</p>
    <script>const reports = 999;</script>
    """

    assert check_public_truth.parse_live_counts(document) == {
        "reports_published": 17,
        "confirmed_reports": 12,
        "enforcement_txs": 9,
    }


def test_unpublished_enforcement_count_is_an_explicit_difference() -> None:
    local = {
        "reports_published": 20,
        "confirmed_reports": 15,
        "enforcement_txs": 9,
    }
    live = {
        "reports_published": 17,
        "confirmed_reports": 12,
        "enforcement_txs": None,
    }

    assert check_public_truth.differences(local, live) == [
        ("reports_published", 20, 17),
        ("confirmed_reports", 15, 12),
        ("enforcement_txs", 9, None),
    ]


def test_offline_skips_files_and_network(capsys) -> None:
    assert check_public_truth.main(["--offline", "--facts", "does-not-exist.json"]) == 0
    assert "network check skipped" in capsys.readouterr().out
