"""The FOBXX dossier is rendered from retained proof, not copied page facts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_fobxx_dossier", ROOT / "scripts" / "build_fobxx_dossier.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dossier_contains_bundle_controls_publications_and_declines() -> None:
    module = _module()
    retained, retained_counts = module._retained_reports(module.BUNDLES)
    page = module.render()

    for value in (
        "fobxx-nav-std-stable-one",
        "&quot;navdate&quot;:&quot;2026-08-21&quot;,&quot;navstd&quot;:&quot;1.00000000&quot;",
        "fobxx-nmfp3-stable-price-one",
        "&lt;stablePricePerShare&gt;1.0000&lt;/stablePricePerShare&gt;",
        "fobxx-nmfp3-daily-liquid-floor",
        "&lt;percentageDailyLiquidAssets&gt;0.6463&lt;/percentageDailyLiquidAssets&gt;",
        "fobxx-nmfp3-weekly-liquid-floor",
        "&lt;percentageWeeklyLiquidAssets&gt;0.7305&lt;/percentageWeeklyLiquidAssets&gt;",
        "fobxx-price-history-freshness",
        "fobxx-nmfp3-filing-freshness",
    ):
        assert value in page
    main_controls = retained[196][2]["controls"]
    satisfied_count = sum(
        control["evaluation"]["result"] == "SATISFIED"
        for control in main_controls
    )
    assert (
        page.count(
            '<span class="chip chip-live"><i class="dot" aria-hidden="true"></i>SATISFIED</span>'
        )
        == satisfied_count
    )
    assert (
        f"{retained_counts[196]} retained <strong>CONFIRMED</strong>\n      "
        f"{module._plural(retained_counts[196], 'publication')} on mainnet" in page
    )
    for chain_id in (196, 1952):
        assert f'/data/{retained[chain_id][0].name}' in page
    assert "this page does not imply a daily history" in page


def test_dossier_distinguishes_published_controls_from_signed_approvals() -> None:
    module = _module()
    retained, _ = module._retained_reports(module.BUNDLES)
    page = module.render()
    published_controls = retained[196][2]["controls"]
    published_ids = {control["control_id"] for control in published_controls}
    approved_entries = module._ledger_entries(
        module._document(module.LEDGER, "approval ledger"), "approved"
    )
    approved_ids = {entry["control_id"] for entry in approved_entries}
    satisfied_count = sum(
        control["evaluation"]["result"] == "SATISFIED"
        for control in published_controls
    )
    unpublished_ids = approved_ids - published_ids

    assert "6 FOBXX controls are approved in the signed ledger" in page
    assert (
        f"{len(published_controls)} published controls, "
        f"{satisfied_count}/{len(published_controls)} SATISFIED" in page
    )
    if unpublished_ids:
        assert f"{len(unpublished_ids)} approved controls are not yet published" in page
        for control_id in unpublished_ids:
            assert f"<code>{control_id}</code>" in page
        assert "The next publication will carry all 6 approved controls." in page
    else:
        assert (
            "All 6 approved controls appear in the latest retained mainnet publication."
            in page
        )
    assert "6 controls were declined" in page


def test_dossier_states_different_network_control_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    retained, retained_counts = module._retained_reports(module.BUNDLES)
    retained[1952][2]["controls"].pop()
    monkeypatch.setattr(
        module, "_retained_reports", lambda bundles: (retained, retained_counts)
    )

    page = module.render()
    main_count = len(retained[196][2]["controls"])
    test_count = len(retained[1952][2]["controls"])

    assert (
        f"The latest retained mainnet publication carries {main_count} "
        f"{module._plural(main_count, 'control')}, while the latest retained "
        f"testnet publication carries {test_count} "
        f"{module._plural(test_count, 'control')}." in page
    )
    assert "Testnet is not on a publishing schedule." in page


def test_dossier_rejects_a_shared_control_result_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    retained, retained_counts = module._retained_reports(module.BUNDLES)
    test_control = retained[1952][2]["controls"][0]
    control_id = test_control["control_id"]
    main_result = next(
        control["evaluation"]["result"]
        for control in retained[196][2]["controls"]
        if control["control_id"] == control_id
    )
    test_control["evaluation"]["result"] = "CONTRADICTED"
    monkeypatch.setattr(
        module, "_retained_reports", lambda bundles: (retained, retained_counts)
    )

    with pytest.raises(ValueError) as raised:
        module.render()

    assert str(raised.value) == (
        f"FOBXX shared control {control_id} disagrees across networks: "
        f"mainnet {main_result}, testnet CONTRADICTED"
    )
