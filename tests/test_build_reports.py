"""The reports feed is derived from retained bundles, never hand-maintained rows."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).with_name("reports_fixtures")


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_reports", ROOT / "scripts" / "build_reports.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows():
    return _module().load_rows(
        bundles=FIXTURES / "bundles",
        stats=FIXTURES / "stats.json",
        facts=FIXTURES / "facts.json",
    )


def test_rows_are_newest_first() -> None:
    rows = _rows()

    assert [row.block for row in rows] == [68307118, 68292878, 38617112, 38526525]


def test_explorer_url_is_selected_by_chain_id() -> None:
    rows = _rows()
    mainnet = next(row for row in rows if row.chain_id == 196)
    testnet = next(row for row in rows if row.chain_id == 1952)

    assert mainnet.transaction_url.startswith(
        "https://web3.okx.com/explorer/xlayer/tx/"
    )
    assert testnet.transaction_url.startswith(
        "https://web3.okx.com/explorer/xlayer-test/tx/"
    )


def test_correction_marker_keeps_the_original_visible() -> None:
    build_reports = _module()
    rows = _rows()
    page = build_reports.render(rows)

    assert page.count("corrects #1") == 2
    assert len(rows) == 4
    assert sum(row.sequence == 1 for row in rows) == 2


def test_empty_state_renders() -> None:
    page = _module().render([])

    assert "No reports yet" in page
