"""A refused workspace has to reach the operator as this service's failure, not a crash."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from touchstone.deployment import DeploymentError, DeploymentManifest
from touchstone.keyring import PUBLISHER_KEY_ENV
from touchstone.signing import SIGNING_SEED_ENV

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import run_service  # noqa: E402


class _StubManifest:
    """Stands in for a committed deployment manifest that loads without a chain."""

    network = "hardhat-local"
    is_local = True
    is_active = True
    deployment_state = "active"

    @staticmethod
    def load(path):
        return _StubManifest()


class _StubKey:
    @staticmethod
    def from_env(manifest):
        return None


def _incident_log_is_a_directory(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "incidents.jsonl").mkdir(parents=True)
    return workspace


def _incident_log_has_two_names(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir()
    log = workspace / "incidents.jsonl"
    log.write_bytes(b"")
    os.link(log, workspace / "incidents-copy.jsonl")
    return workspace


def _workspace_is_a_file(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.write_text("", encoding="utf-8")
    return workspace


def _operations_directory_is_a_file(root: Path) -> Path:
    """A refusal the operating system raises, not one this project words itself.

    `OperationsStore` creates its directory, so a file already occupying that name fails
    the construction with `FileExistsError`. Every refusal covered above is a `ValueError`
    this project writes, and catching only those left the whole `OSError` family — the
    refusals the workspace's own filesystem issues — reaching the operator as a traceback.
    """
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "operations").write_bytes(b"")
    return workspace


@pytest.mark.parametrize(
    "make_workspace",
    [
        pytest.param(
            _incident_log_is_a_directory,
            id="incident-log-is-a-directory",
        ),
        pytest.param(
            _incident_log_has_two_names,
            id="incident-log-has-two-names",
        ),
        pytest.param(
            _workspace_is_a_file,
            id="workspace-is-a-file",
        ),
        pytest.param(
            _operations_directory_is_a_file,
            id="operations-directory-is-a-file",
        ),
    ],
)
def test_an_unusable_workspace_fails_the_service_rather_than_crashing_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    make_workspace,
) -> None:
    """Refusing to start is correct. Refusing with a traceback is not.

    The stores judge a workspace usable at construction. Those refusals come in two
    kinds: the `ValueError`s this project words itself, and the `OSError`s the workspace's
    own filesystem issues. `main` caught neither at first and then only the former, so a
    deliberate refusal reached the operator as an uncaught traceback instead of the
    service's own failure line. A new way to fail still has to fit the startup contract.
    """
    workspace = make_workspace(tmp_path)
    # Everything before the durable stores is stubbed out, because a missing manifest
    # raises DeploymentError — which the old handler already caught, so the test would
    # have passed against the very code it exists to catch.
    monkeypatch.setattr(run_service, "DeploymentManifest", _StubManifest)
    monkeypatch.setattr(run_service, "assert_role_separation", lambda: None)
    monkeypatch.setattr(run_service, "PublisherKey", _StubKey)
    monkeypatch.setattr(
        run_service, "SignedRegistryBackend", lambda manifest, key: None
    )

    code = run_service.main(
        [
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--workspace",
            str(workspace),
            "--asset-key",
            "eip155:1:0x" + "11" * 20,
            "--resolve-only",
        ]
    )

    assert code == 1
    error = capsys.readouterr().err
    assert error.startswith("SERVICE FAIL: "), f"crashed instead of failing: {error!r}"


def test_fixtures_require_a_capture_to_be_named(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No default. The default served bytes the approved controls could never publish.

    Argparse refuses this before anything is constructed, so the operator finds out at the
    command line rather than from an incident written after a slot has already failed.
    """
    with pytest.raises(SystemExit) as refusal:
        run_service.main(
            [
                "--manifest",
                "manifest.json",
                "--workspace",
                "workspace",
                "--asset-key",
                "eip155:1:0x" + "11" * 20,
                "--fixtures",
                "fixtures",
            ]
        )

    assert refusal.value.code == 2
    assert "--fixtures requires --fixture-capture" in capsys.readouterr().err


def _public_manifest(tmp_path: Path, **overrides: object) -> Path:
    """A real, loadable manifest for a public network — not a stub."""
    manifest = json.loads(
        (Path(__file__).parents[1] / "deployments" / "xlayer-testnet.json").read_text(
            encoding="utf-8"
        )
    )
    manifest.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_a_public_network_is_never_served_from_committed_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Historical bytes signed as today's observation are indistinguishable on chain.

    Driven through `main` with no keys in the environment at all. The earlier version of
    this called `_serve_ustb` directly and so never exercised the CLI's real ordering —
    where `build_service` constructs the publisher, and therefore reads the EVM key, before
    anything reached this refusal. It passed while the guard was unreachable.
    """
    monkeypatch.delenv(SIGNING_SEED_ENV, raising=False)
    monkeypatch.delenv(PUBLISHER_KEY_ENV, raising=False)
    manifest = _public_manifest(tmp_path, deployment_state="active")

    code = run_service.main(
        [
            "--manifest",
            str(manifest),
            "--workspace",
            str(tmp_path / "workspace"),
            "--asset-key",
            "eip155:1:0x" + "11" * 20,
            "--fixtures",
            str(Path(__file__).parents[1] / "fixtures"),
            "--fixture-capture",
            "2026-08-14",
        ]
    )

    assert code == 1
    error = capsys.readouterr().err
    assert "public network" in error
    assert "must be served from live sources" in error


def test_a_superseded_deployment_is_refused_before_any_key_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prose in a notes field refuses nothing.

    Preflight compares deployed code against the digest the manifest itself records, so a
    manifest describing an obsolete registry agrees with it perfectly. The X Layer testnet
    registry predates the epochKey change and cannot enforce one report per epoch; only a
    declared, machine-readable state keeps a publisher away from it.
    """
    monkeypatch.delenv(SIGNING_SEED_ENV, raising=False)
    monkeypatch.delenv(PUBLISHER_KEY_ENV, raising=False)
    manifest = _public_manifest(tmp_path, deployment_state="superseded")

    code = run_service.main(
        [
            "--manifest",
            str(manifest),
            "--workspace",
            str(tmp_path / "workspace"),
            "--asset-key",
            "eip155:1:0x" + "11" * 20,
            "--max-runs",
            "1",
        ]
    )

    assert code == 1
    assert "marked 'superseded'" in capsys.readouterr().err


def test_the_committed_testnet_manifest_is_marked_superseded() -> None:
    """The deployed registry has no epochSequence, so nothing may publish to it."""
    manifest = DeploymentManifest.load(
        Path(__file__).parents[1] / "deployments" / "xlayer-testnet.json"
    )

    assert manifest.deployment_state == "superseded"
    assert not manifest.is_active


def test_an_unknown_deployment_state_is_refused(tmp_path: Path) -> None:
    """A typo must not read as permission."""
    manifest = _public_manifest(tmp_path, deployment_state="actve")

    with pytest.raises(DeploymentError, match="deployment_state must be one of"):
        DeploymentManifest.load(manifest)
