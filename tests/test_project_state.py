import importlib.util
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
