"""The reports feed is derived from retained bundles, never hand-maintained rows."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


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


@pytest.fixture
def split_batch_rows() -> list[dict[str, object]]:
    return [
        {
            "chain_id": 196,
            "note": "xlayer-mainnet-v1",
            "observed_at": "2026-08-25T15:10:25Z",
        },
        {
            "chain_id": 196,
            "note": "policy:nav-settlement",
            "observed_at": "2026-08-25T15:17:12Z",
        },
    ]


def test_split_batch_policy_row_anchors_by_chain_id(
    split_batch_rows: list[dict[str, object]],
) -> None:
    networks = {196: "mainnet", 1952: "testnet"}

    assert _module()._publication_networks(split_batch_rows, networks) == [
        "mainnet",
        "mainnet",
    ]


def test_network_note_must_agree_with_chain_id() -> None:
    publications = [
        {
            "chain_id": 1952,
            "note": "xlayer-mainnet-v1",
            "observed_at": "2026-08-25T15:10:25Z",
        }
    ]

    with pytest.raises(ValueError, match="chain_id 1952 conflicts with note"):
        _module()._publication_networks(publications, {196: "mainnet", 1952: "testnet"})


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


def test_live_rows_include_fobxx_on_both_networks_newest_first() -> None:
    rows = _module().load_rows()

    keyed = [(row.asset, row.chain_id, row.sequence) for row in rows]
    # Newest first: the 2026-08-23 mainnet FOBXX publication leads, and the USTB
    # rows published earlier that day sit between it and the testnet FOBXX row.
    assert keyed[0] == ("FOBXX", 196, 2)
    assert ("FOBXX", 1952, 1) in keyed
    assert rows[0].transaction_url.startswith(
        "https://web3.okx.com/explorer/xlayer/tx/"
    )
    testnet = rows[keyed.index(("FOBXX", 1952, 1))]
    assert testnet.transaction_url.startswith(
        "https://web3.okx.com/explorer/xlayer-test/tx/"
    )
