import importlib.util
from pathlib import Path

import pytest

from scripts.build_reports import ReportRow


ROOT = Path(__file__).parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_site", ROOT / "scripts" / "build_site.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_site = _module()


def test_homepage_facts_come_from_stats_and_latest_mainnet_bundle() -> None:
    facts = build_site.site_facts()

    assert facts["homepage.live_assets"] == "2"
    assert facts["homepage.evidence_sources"] == "7"
    assert facts["homepage.networks"] == "2"
    assert facts["homepage.confirmed_reports"] == "17"
    assert facts["homepage.ustb.state"] == "CONFIRMED"
    assert facts["homepage.ustb.nav"] == "11.18316100"
    assert facts["homepage.ustb.nav_date"] == "2026-08-18"
    assert facts["homepage.ustb.valid_until"] == "2026-08-23T23:59:59Z"
    assert facts["homepage.ustb.source_count"] == "3"
    assert facts["homepage.fobxx.state"] == "CONFIRMED"
    assert facts["homepage.fobxx.evidence_as_of"] == "2026-08-22T04:51:45.401050Z"
    assert facts["homepage.fobxx.valid_until"] == "2026-08-26T23:59:59Z"
    assert facts["homepage.fobxx.source_count"] == "4"
    assert facts["homepage.fobxx.history_summary"] == (
        "1 publication on mainnet and 1 publication on testnet"
    )
    assert facts["homepage.ustb.gate_sentence"] == (
        "A configured admission contract may refuse USTB right now because the gate "
        "result is not available."
    )


def test_asset_status_facts_come_from_source_manifests() -> None:
    facts = build_site.asset_status_facts()

    assert facts["asset_status.usdy.state"] == "suspended"
    assert facts["asset_status.usdy.label"] == "RESEARCH · SUSPENDED"
    assert "260 MB" in facts["asset_status.usdy.reason"]
    assert facts["asset_status.ousg.state"] == "research"
    assert facts["asset_status.ousg.label"] == "RESEARCH"


def test_asset_status_gate_rejects_rendered_drift() -> None:
    rendered = build_site.rendered_homepage()
    build_site.assert_asset_status_truth([rendered])

    altered = rendered.replace(
        'data-asset-status="USDY">RESEARCH · SUSPENDED<',
        'data-asset-status="USDY">BUILT · NOT LIVE<',
        1,
    )
    with pytest.raises(SystemExit, match="USDY.*BUILT · NOT LIVE"):
        build_site.assert_asset_status_truth([altered])


def test_generated_site_asset_status_surfaces_match_manifests() -> None:
    pages = [
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "site2").rglob("*.html"))
        if "_pages" not in path.parts and "_partials" not in path.parts
    ]

    build_site.assert_asset_status_truth(pages)


def test_live_manifest_status_requires_verified_onchain_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        ReportRow(
            asset="USTB",
            published_at=None,
            published_text="",
            state="CONFIRMED",
            report_hash="0" * 64,
            bundle_url="/data/report.json",
            transaction_hash="0x" + "1" * 64,
            transaction_url="https://example.test/tx/1",
            network="X LAYER MAINNET",
            chain_id=196,
            sequence=1,
            correction_of=None,
            block=1,
        )
    ]
    monkeypatch.setattr(build_site, "load_rows", lambda: rows)

    with pytest.raises(SystemExit, match="FOBXX.*mainnet and testnet"):
        build_site.assert_live_asset_evidence()


def test_gate_sentence_requires_a_data_result() -> None:
    assert build_site._gate_sentence({}) == (
        "A configured admission contract may refuse USTB right now because the gate "
        "result is not available."
    )
    assert build_site._gate_sentence({"gate_result": {"allowed": True}}) == (
        "A configured admission contract may permit USTB right now."
    )
    assert build_site._gate_sentence(
        {"gate_result": {"allowed": False, "reason": "status not allowed"}}
    ) == (
        "A configured admission contract may refuse USTB right now because status not "
        "allowed."
    )


def test_homepage_numeric_truth_rejects_rendered_drift() -> None:
    rendered = build_site.rendered_homepage()
    build_site.assert_homepage_truth(rendered)

    altered = rendered.replace(
        'data-homepage-fact="confirmed_reports">17<',
        'data-homepage-fact="confirmed_reports">16<',
    )
    with pytest.raises(SystemExit, match="homepage fact confirmed_reports"):
        build_site.assert_homepage_truth(altered)


def test_live_status_gate_rejects_report_count_divergence() -> None:
    with pytest.raises(SystemExit, match=r"local 22/17.*live /status 17/12"):
        build_site.assert_live_status_counts(
            "<strong>17 reports</strong>, of which "
            "<strong>12 reached\n<code>CONFIRMED</code></strong>."
        )


def test_homepage_and_reports_links_resolve_in_generated_site() -> None:
    build_site.assert_page_links(ROOT / "site2" / "index.html")
    build_site.assert_page_links(ROOT / "site2" / "reports.html")
