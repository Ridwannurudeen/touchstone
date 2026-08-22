"""The FOBXX dossier is rendered from retained proof, not copied page facts."""

from __future__ import annotations

import importlib.util
from pathlib import Path


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
    page = _module().render()

    for value in (
        "fobxx-nav-std-stable-one",
        "&quot;navdate&quot;:&quot;2026-08-21&quot;,&quot;navstd&quot;:&quot;1.00000000&quot;",
        "fobxx-nmfp3-stable-price-one",
        "&lt;stablePricePerShare&gt;1.0000&lt;/stablePricePerShare&gt;",
        "fobxx-nmfp3-daily-liquid-floor",
        "&lt;percentageDailyLiquidAssets&gt;0.6463&lt;/percentageDailyLiquidAssets&gt;",
        "fobxx-nmfp3-weekly-liquid-floor",
        "&lt;percentageWeeklyLiquidAssets&gt;0.7305&lt;/percentageWeeklyLiquidAssets&gt;",
        "0x93ad57ed49bfbbd8b244fb32a6e60d5280e8c6745d871ac1270d9556e165660e",
        "0x4e84d9523a528d91a58afe49a9632e8456b92f3d26587be6c11998b55625b42f",
        "fobxx-price-history-freshness",
        "fobxx-nmfp3-filing-freshness",
    ):
        assert value in page
    assert page.count("SATISFIED") == 4
    assert "1 retained <strong>CONFIRMED</strong>\n      publication on mainnet" in page
    assert "this page does not imply a daily history" in page
