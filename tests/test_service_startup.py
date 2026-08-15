"""A refused workspace has to reach the operator as this service's failure, not a crash."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import run_service  # noqa: E402


class _StubManifest:
    """Stands in for a committed deployment manifest that loads without a chain."""

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
