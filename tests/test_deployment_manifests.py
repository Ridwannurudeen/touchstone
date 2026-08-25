"""Every committed manifest, checked against the schema that describes it.

Nothing validated these against `manifest.schema.json`, so the schema and the files drifted
apart in silence — and when `deployment_state` was added to the loader, neither the schema,
the deploy script nor the two templates knew about it. A schema nobody runs is documentation
that happens to be machine-readable.

The deploy script is checked too, because the manifest that matters most is the one nobody
writes by hand: a fresh deployment's. If it omits a field the loader defaults, the default
is what a real deployment ends up carrying.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
from jsonschema import Draft202012Validator

from touchstone.deployment import DEPLOYMENT_STATES, DeploymentManifest


ROOT = Path(__file__).parents[1]
DEPLOYMENTS = ROOT / "deployments"
SCHEMA = json.loads((DEPLOYMENTS / "manifest.schema.json").read_text(encoding="utf-8"))


def _manifest_paths(deployments: Path = DEPLOYMENTS) -> list[Path]:
    return sorted(
        path
        for path in deployments.glob("*.json")
        if path.name != "manifest.schema.json"
        and not path.name.endswith(".attempt.json")
    )


MANIFESTS = _manifest_paths()


def test_attempt_journal_is_not_collected_as_a_manifest(tmp_path: Path) -> None:
    deployments = tmp_path / "deployments"
    deployments.mkdir()
    manifest = deployments / "xlayer-testnet-2.json"
    manifest.write_text("{}", encoding="utf-8")
    (deployments / "xlayer-testnet-2.json.attempt.json").write_text(
        '{"stage":"prepared"}\n{"stage":"authorized"}\n', encoding="utf-8"
    )

    assert _manifest_paths(deployments) == [manifest]


def test_there_are_manifests_to_check() -> None:
    """Guards against this whole module silently passing on an empty glob."""
    assert MANIFESTS


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda path: path.name)
def test_every_committed_manifest_matches_the_schema(path: Path) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(SCHEMA).iter_errors(manifest), key=lambda e: e.path
    )
    assert not errors, "; ".join(
        f"{'/'.join(str(p) for p in error.path) or '<root>'}: {error.message}"
        for error in errors
    )


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda path: path.name)
def test_every_committed_manifest_declares_its_state(path: Path) -> None:
    """Declared, never defaulted.

    Both the schema and the loader refuse a manifest that omits it. They disagreed once —
    the schema required the field while the loader defaulted an absent one to "active", so a
    manifest the schema rejected still loaded, and loaded as publishable.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["deployment_state"] in DEPLOYMENT_STATES


def test_the_deploy_script_emits_a_state_rather_than_omitting_it() -> None:
    """A fresh deployment's manifest is the one nobody writes by hand."""
    source = (ROOT / "contracts" / "scripts" / "deploy.js").read_text(encoding="utf-8")

    assert re.search(r'deployment_state:\s*"active"', source), (
        "deploy.js must state deployment_state explicitly"
    )


def test_the_superseded_testnet_deployment_is_refused() -> None:
    """The deployed registry predates epochKey and cannot enforce one report per epoch."""
    manifest = DeploymentManifest.load(DEPLOYMENTS / "xlayer-testnet.json")

    assert manifest.deployment_state == "superseded"
    assert not manifest.is_active


@pytest.mark.parametrize(
    "path",
    [p for p in MANIFESTS if p.name.endswith(".template.json")],
    ids=lambda path: path.name,
)
def test_a_template_is_refused_as_a_deployment(path: Path) -> None:
    """Templates carry the marker and must never load as a real deployment."""
    from touchstone.deployment import DeploymentError

    with pytest.raises(DeploymentError, match="template"):
        DeploymentManifest.load(path)


def test_a_manifest_state_outside_the_closed_set_is_refused(tmp_path: Path) -> None:
    """A typo must not read as permission to publish."""
    from touchstone.deployment import DeploymentError

    manifest = json.loads(
        (DEPLOYMENTS / "xlayer-testnet.json").read_text(encoding="utf-8")
    )
    manifest["deployment_state"] = "activ"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DeploymentError, match="deployment_state must be one of"):
        DeploymentManifest.load(path)


def _without_state() -> dict:
    manifest = json.loads(
        (DEPLOYMENTS / "xlayer-testnet.json").read_text(encoding="utf-8")
    )
    del manifest["deployment_state"]
    return manifest


def test_the_schema_rejects_a_manifest_with_no_state() -> None:
    """A negative case. The other schema tests only validate correct manifests.

    Validating known-good files proves the files are good, not that the schema would refuse
    a bad one — removing `deployment_state` from `required` would have survived every test
    written before this.
    """
    errors = list(Draft202012Validator(SCHEMA).iter_errors(_without_state()))

    assert any("deployment_state" in error.message for error in errors)


def test_the_schema_rejects_a_state_outside_the_closed_set() -> None:
    """Relaxing or deleting the enum would otherwise survive."""
    manifest = json.loads(
        (DEPLOYMENTS / "xlayer-testnet.json").read_text(encoding="utf-8")
    )
    manifest["deployment_state"] = "retired"

    errors = list(Draft202012Validator(SCHEMA).iter_errors(manifest))

    assert any("retired" in error.message for error in errors)


def test_the_loader_refuses_a_manifest_with_no_state(tmp_path: Path) -> None:
    """Schema and loader must agree.

    They did not: the schema listed the field as required while the loader defaulted an
    absent one to "active" — so a manifest the schema rejected still loaded, and loaded as
    publishable. The one manifest that must never read as active is an obsolete one, which
    is exactly where a field goes missing.
    """
    from touchstone.deployment import DeploymentError

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_without_state()), encoding="utf-8")

    with pytest.raises(DeploymentError, match="deployment_state"):
        DeploymentManifest.load(path)


def test_the_upgrade_repairs_every_publisher_workspace() -> None:
    guide = (ROOT / "docs" / "DEPLOY-SERVICE.md").read_text(encoding="utf-8")

    assert 'for W in "$NETWORK_ROOT"/*; do' in guide
    assert '[ -d "$W" ] || continue' in guide
    assert "for asset in ustb fobxx" not in guide
    assert 'if [ -d "$W/evidence" ]; then' in guide
    assert "for name in observations.jsonl observer.lock; do" in guide
