import importlib.util
from pathlib import Path

import pytest


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

    assert facts["homepage.live_assets"] == "1"
    assert facts["homepage.evidence_sources"] == "3"
    assert facts["homepage.networks"] == "2"
    assert facts["homepage.confirmed_reports"] == "15"
    assert facts["homepage.ustb.state"] == "CONFIRMED"
    assert facts["homepage.ustb.nav"] == "11.18316100"
    assert facts["homepage.ustb.nav_date"] == "2026-08-18"
    assert facts["homepage.ustb.valid_until"] == "2026-08-23T23:59:59Z"
    assert facts["homepage.ustb.source_count"] == "3"
    assert facts["homepage.ustb.gate_sentence"] == (
        "A configured admission contract may refuse USTB right now because the gate "
        "result is not available."
    )


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
        'data-homepage-fact="confirmed_reports">15<',
        'data-homepage-fact="confirmed_reports">14<',
    )
    with pytest.raises(SystemExit, match="homepage fact confirmed_reports"):
        build_site.assert_homepage_truth(altered)


def test_live_status_gate_rejects_report_count_divergence() -> None:
    with pytest.raises(SystemExit, match=r"local 20/15.*live /status 17/12"):
        build_site.assert_live_status_counts(
            "<strong>17 reports</strong>, of which "
            "<strong>12 reached\n<code>CONFIRMED</code></strong>."
        )


def test_homepage_and_reports_links_resolve_in_generated_site() -> None:
    build_site.assert_page_links(ROOT / "site2" / "index.html")
    build_site.assert_page_links(ROOT / "site2" / "reports.html")
