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


def test_project_state_is_assembled_from_verified_repository_facts(tmp_path: Path) -> None:
    state = build.build_state(ROOT)
    assert state["version"] == build.STATE_VERSION
    assert state["approval"]["approved_count"] == len(
        state["approval"]["approved_control_ids"]
    )
    assert state["reports"]["artifact_count"] == len(state["bundles"])
    # Two, since 2026-08-19: the first confirmed policy reports were published and their
    # bundles retained under site2/data. These pinned zero right up until the product did
    # the thing it was built to do.
    assert state["reports"]["retained_verified_policy_bundle_count"] == 2
    assert state["reports"]["confirmed_policy_bundle_count"] == 2
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
    assert "<style" not in page
    assert " style=" not in page
    assert "<script" not in page
    assert "onclick=" not in page
    assert 'role="tab"' not in page
    # CONFIRMED since 2026-08-19; the truth gate separately requires the retained
    # bundles that entitle the page to say so.
    assert page.count('data-policy-state="CONFIRMED"') == 2
