import importlib.util
from copy import deepcopy
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _module("build_project_state")
assert_truth = _module("assert_public_truth")


def test_project_state_is_assembled_from_verified_repository_facts(
    tmp_path: Path,
) -> None:
    state = build.build_state(ROOT)
    assert state["version"] == build.STATE_VERSION
    assert state["approval"]["approved_count"] == len(
        state["approval"]["approved_control_ids"]
    )
    assert state["reports"]["artifact_count"] == len(state["bundles"])
    # Ten, since 2026-08-22: five confirmed policy pairs are retained under
    # site2/data and verified — the fourth is the first TESTNET pair with retained
    # chain-aware bundle files, the very gap the dossier used to disclose. These pinned
    # zero right up until the product did the thing it was built to do, and the count
    # only moves when another confirmed policy bundle is retained — which is exactly
    # the event worth pinning.
    assert state["reports"]["retained_verified_policy_bundle_count"] == 18
    assert state["reports"]["confirmed_policy_bundle_count"] == 18
    assert state["deployments"]
    output = tmp_path / "project-state.json"
    output.write_bytes(build.encode_state(state))
    assert json.loads(output.read_text(encoding="utf-8")) == state


def test_public_truth_rejects_a_stale_phrase(tmp_path: Path) -> None:
    state = build.build_state(ROOT)
    public = tmp_path / "index.html"
    public.write_text("the gate never deployed", encoding="utf-8")
    try:
        assert_truth.assert_state(state, (public,))
    except assert_truth.PublicTruthError as error:
        assert "stale public phrase" in str(error)
    else:
        raise AssertionError("stale public copy was accepted")


def _matching_chain_fact_surfaces() -> tuple[dict, dict, str]:
    return (
        {
            "counts": {
                "reports_published": "14",
                "confirmed_reports": "9",
            }
        },
        {
            "reports_published": 14,
            "confirmed_reports": 9,
            "reports": ([{"state": "CONFIRMED"}] * 9)
            + ([{"state": "UNVERIFIABLE"}] * 5),
        },
        "Status: 14 published reports, 9 `CONFIRMED`.",
    )


def test_public_truth_rejects_each_report_count_disagreement() -> None:
    cases = []
    facts, stats, readme = _matching_chain_fact_surfaces()
    stats["reports_published"] = 11
    cases.append((facts, stats, readme))

    facts, stats, readme = _matching_chain_fact_surfaces()
    stats["confirmed_reports"] = 6
    cases.append((facts, stats, readme))

    facts, stats, readme = _matching_chain_fact_surfaces()
    stats["reports"].pop()
    cases.append((facts, stats, readme))

    facts, stats, readme = _matching_chain_fact_surfaces()
    stats["reports"][0]["state"] = "UNVERIFIABLE"
    cases.append((facts, stats, readme))

    facts, stats, _ = _matching_chain_fact_surfaces()
    cases.append((facts, stats, "Status: 11 published reports, 6 `CONFIRMED`."))

    for facts, stats, readme in cases:
        try:
            assert_truth.assert_chain_fact_surfaces(facts, stats, readme)
        except assert_truth.PublicTruthError:
            pass
        else:
            raise AssertionError("a public report-count disagreement was accepted")


def test_public_truth_rejects_non_integer_chain_fact_counts() -> None:
    facts = {
        "counts": {
            "reports_published": 14.5,
            "confirmed_reports": "9",
        }
    }
    stats = {
        "reports_published": 14,
        "confirmed_reports": 9,
        "reports": ([{"state": "CONFIRMED"}] * 9) + ([{"state": "UNVERIFIABLE"}] * 5),
    }
    readme = "Status: 14 published reports, 9 `CONFIRMED`."

    try:
        assert_truth.assert_chain_fact_surfaces(facts, stats, readme)
    except assert_truth.PublicTruthError as error:
        assert "integers" in str(error)
    else:
        raise AssertionError("a non-integer chain fact count was accepted")


def test_public_truth_accepts_report_counts_that_match_chain_facts() -> None:
    facts, stats, readme = _matching_chain_fact_surfaces()
    assert_truth.assert_chain_fact_surfaces(facts, stats, readme)


def test_public_truth_accepts_readme_that_defers_live_counts_to_generated_site() -> None:
    facts, stats, _ = _matching_chain_fact_surfaces()
    assert_truth.assert_chain_fact_surfaces(
        facts, stats, "Current counts are rendered from retained data on the site."
    )


def test_registry_latest_sequences_link_their_latest_publications() -> None:
    page = (ROOT / "site2/_pages/products/registry.html").read_text(encoding="utf-8")
    links = (
        ("mainnet.pubs.asset_5_tx", "mainnet.seq_asset_v1"),
        ("testnet.pubs.asset_4_tx", "testnet.seq_asset_v1"),
        ("mainnet.pubs.freshness_v2_3_tx", "mainnet.seq_freshness_v2"),
        ("testnet.pubs.freshness_v2_tx", "testnet.seq_freshness_v2"),
        ("mainnet.pubs.nav_v2_3_tx", "mainnet.seq_nav_v2"),
        ("testnet.pubs.nav_v2_tx", "testnet.seq_nav_v2"),
    )
    for transaction, sequence in links:
        assert f'0x{{{{fact:{transaction}}}}}">{{{{fact:{sequence}}}}}</a>' in page


def test_public_truth_rejects_a_stale_phrase_regardless_of_its_recorded_case(
    tmp_path: Path,
) -> None:
    # The scan lowercases the document before comparing, so a phrase recorded with any
    # uppercase letter could never match anything — five of the eight phrases were dead
    # the moment they were added, and the one test above passed because it used the one
    # phrase that happened to be all-lowercase. An external audit found it (2026-08-20).
    state = build.build_state(ROOT)
    for phrase in assert_truth.STALE_PHRASES:
        public = tmp_path / "index.html"
        public.write_text(f"prose around {phrase} the claim", encoding="utf-8")
        try:
            assert_truth.assert_state(state, (public,))
        except assert_truth.PublicTruthError as error:
            assert "stale public phrase" in str(error)
        else:
            raise AssertionError(f"stale phrase {phrase!r} was accepted")


def _confirmed_policy_panel() -> str:
    return """<div data-policy-panel="freshness"
data-policy-id="disclosure-freshness" data-policy-version="1"
data-policy-state="CONFIRMED"><span>CONFIRMED</span></div>"""


def test_public_truth_rejects_confirmed_policy_without_retained_bundle(
    tmp_path: Path,
) -> None:
    state = deepcopy(build.build_state(ROOT))
    # The guard under test is "no CONFIRMED claim without a retained bundle". The repo now
    # retains real policy bundles, so they are stripped here — otherwise this test would be
    # about the repository's current contents rather than about the guard.
    state["bundles"] = [
        bundle
        for bundle in state["bundles"]
        if not isinstance(bundle.get("policy"), dict)
    ]
    state["reports"]["artifact_count"] = len(state["bundles"])
    state["reports"]["retained_verified_policy_bundle_count"] = 0
    state["reports"]["confirmed_policy_bundle_count"] = 0
    public = tmp_path / "judge.html"
    public.write_text(_confirmed_policy_panel(), encoding="utf-8")
    try:
        assert_truth.assert_state(state, (public,))
    except assert_truth.PublicTruthError as error:
        assert "without a retained verified policy bundle" in str(error)
    else:
        raise AssertionError("unsupported policy confirmation was accepted")


def test_public_truth_accepts_confirmed_policy_with_retained_verified_bundle(
    tmp_path: Path,
) -> None:
    state = deepcopy(build.build_state(ROOT))
    policy = state["policies"][0]
    state["bundles"].append(
        {
            "asset_key": policy["asset_key"],
            "control_count": len(policy["control_ids"]),
            "evidence_root": "00" * 32,
            "path": "retained-policy-bundle.json",
            "policy": {
                "control_ids": policy["control_ids"],
                "policy_digest": policy["digest"],
                "policy_id": policy["policy_id"],
                "policy_version": policy["policy_version"],
            },
            "sequence": 1,
            "sha256": "11" * 32,
            "state": "CONFIRMED",
        }
    )
    state["reports"]["artifact_count"] += 1
    state["reports"]["retained_verified_policy_bundle_count"] += 1
    state["reports"]["confirmed_policy_bundle_count"] += 1
    public = tmp_path / "judge.html"
    public.write_text(_confirmed_policy_panel(), encoding="utf-8")
    assert_truth.assert_state(state, (public,))


def test_judge_page_obeys_the_static_site_csp() -> None:
    page = (ROOT / "site2" / "judge.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/assets/style.css">' in page
    # The load-bearing claim is no script: the page must render as static text under
    # `script-src 'none'`. Inline styles became legal on 2026-08-20 when the vhost widened
    # `style-src` for the product-site rebuild — there is no script for an injected style
    # to assist and no user content is rendered — so this test stopped pinning them.
    assert "<script" not in page
    assert "onclick=" not in page
    assert 'role="tab"' not in page
    # CONFIRMED since 2026-08-19; the truth gate separately requires the retained
    # bundles that entitle the page to say so.
    assert page.count('data-policy-state="CONFIRMED"') == 2
