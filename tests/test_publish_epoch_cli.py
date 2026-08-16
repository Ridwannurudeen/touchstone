"""The direct publisher refuses a superseded deployment, before any network access.

The unattended service checked `deployment_state` and this one-shot command did not, so the
documented operational entry point could still publish to a registry the repository had
marked obsolete — with a perfectly valid key, and no warning. The check now lives in
`SignedRegistryBackend`, which every publication passes through, so a second entry point
cannot be added without it.

The refusal must happen before the endpoint is contacted. A check that only fires after a
network round trip is one that a firewalled or offline operator never sees.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from touchstone.deployment import DeploymentManifest
from touchstone.keyring import PUBLISHER_KEY_ENV, PublisherKey
from touchstone.publish import PreflightFailed, SignedRegistryBackend

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import publish_epoch  # noqa: E402


ROOT = Path(__file__).parents[1]
DEPLOYMENTS = ROOT / "deployments"
# Hardhat's published development key. Known to everyone, which is the point: this test
# signs nothing and contacts nothing, it only needs an address the manifest can name.
DEV_KEY = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEV_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def _manifest(tmp_path: Path, **overrides: object) -> Path:
    manifest = json.loads(
        (DEPLOYMENTS / "xlayer-testnet.json").read_text(encoding="utf-8")
    )
    # The publisher identity is pinned to the key, so the state check is what refuses —
    # not an identity mismatch arriving first and hiding it.
    manifest["publisher_address"] = DEV_ADDRESS
    manifest["publisher_identity_address"] = DEV_ADDRESS
    manifest.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_the_backend_refuses_a_superseded_deployment(tmp_path: Path) -> None:
    """The shared boundary, not the caller. Every publication is constructed through it."""
    manifest = DeploymentManifest.load(_manifest(tmp_path))
    key = PublisherKey.from_hex(DEV_KEY, manifest)

    with pytest.raises(PreflightFailed, match="superseded"):
        SignedRegistryBackend(manifest, key)


def test_the_direct_publisher_refuses_before_touching_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`scripts/publish_epoch.py` is the documented one-shot operational command.

    It loaded the manifest and built a publisher without ever asking whether the deployment
    was still one that may be published to. A refusal that arrives only after an RPC call
    is one an offline operator never sees, so this asserts the endpoint is never contacted.
    """
    monkeypatch.setenv(PUBLISHER_KEY_ENV, DEV_KEY)

    def refuse_network(*args: object, **kwargs: object):
        raise AssertionError("the endpoint was contacted before the state was checked")

    monkeypatch.setattr(publish_epoch, "assert_role_separation", lambda: None)
    monkeypatch.setattr(
        "touchstone.publish.Web3.HTTPProvider", refuse_network, raising=False
    )

    code = publish_epoch.main(["--manifest", str(_manifest(tmp_path)), "--preflight"])

    assert code == 1
    assert "superseded" in capsys.readouterr().err


def test_an_active_deployment_is_not_refused_by_the_state_check(
    tmp_path: Path,
) -> None:
    """The guard must refuse the obsolete case and only that case."""
    manifest = DeploymentManifest.load(_manifest(tmp_path, deployment_state="active"))
    key = PublisherKey.from_hex(DEV_KEY, manifest)

    # Constructing must not raise on state. It may still fail later on the network, which
    # is a different boundary and not what this asserts.
    backend = SignedRegistryBackend(manifest, key)

    assert backend.manifest.is_active
