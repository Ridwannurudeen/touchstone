"""The release document has to be something a claim can be checked against.

`build_release.py` exists because a tag plus a spoken test count is not a release:
the tag does not name the compiler settings, the lockfiles, the fixtures or the
deployments, and a count nobody supplied is a number nobody ran. Each case below
is one of the ways that document would stop being checkable — two runs that
disagree, a zero that was never counted, a dirty tree reported clean, a template
placeholder shipped as an address, a digest of decoded text, a missing input
turned into an empty section.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from build_release import encode_release, main, sha256_file  # noqa: E402


REPO = Path(__file__).parents[1]
SCRIPT = REPO / "scripts" / "build_release.py"
BUILT_AT = "2026-08-17T00:00:00Z"

PYPROJECT = """\
[project]
requires-python = ">=3.11"
dependencies = [
    "cryptography==49.0.0",
    "psutil==7.2.2",
]
[project.optional-dependencies]
dev = [
    "pytest==8.3.3",
]
"""

HARDHAT = """\
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
};
"""

HARDHAT_WITH_EVM = """\
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
      evmVersion: "paris",
    },
  },
};
"""

LOCKFILE = b'{"lockfileVersion": 3}\n'
SCHEMA = b'{"title": "deployment"}\n'
FIXTURE = b'{"nav": "11.17"}\n'
SOURCE = b'{"source_id": "ustb"}\n'

ACTIVE = {
    "network": "xlayer-testnet",
    "chain_id": 1952,
    "registry_address": "0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C",
    "deployment_state": "active",
    "registry_runtime_bytecode_sha256": "ab" * 32,
}
SUPERSEDED = {
    **ACTIVE,
    "registry_address": "0xc9d58e4496bF061C3177301Ff02518eBB70AD30d",
    "deployment_state": "superseded",
    "registry_runtime_bytecode_sha256": "cd" * 32,
}
TEMPLATE = {
    **ACTIVE,
    "network": "xlayer-mainnet",
    "chain_id": 196,
    "registry_address": "0x1111111111111111111111111111111111111111",
}


def _git(root: Path, *arguments: str) -> str:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}
    }
    finished = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def _write(path: Path, body: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, str):
        path.write_bytes(body.encode("utf-8"))
    else:
        path.write_bytes(body)


def _tree(tmp_path: Path, *, evm: bool = False) -> Path:
    """A complete tree the builder can describe, committed so HEAD exists."""
    root = tmp_path / "repo"
    _write(root / "pyproject.toml", PYPROJECT)
    config = HARDHAT_WITH_EVM if evm else HARDHAT
    _write(root / "contracts" / "hardhat.config.js", config)
    _write(root / "contracts" / "package-lock.json", LOCKFILE)
    _write(root / "deployments" / "manifest.schema.json", SCHEMA)
    _write(
        root / "deployments" / "xlayer-testnet.json",
        json.dumps(SUPERSEDED).encode("utf-8"),
    )
    _write(
        root / "deployments" / "xlayer-testnet-2.json",
        json.dumps(ACTIVE).encode("utf-8"),
    )
    _write(
        root / "deployments" / "xlayer-mainnet.template.json",
        json.dumps(TEMPLATE).encode("utf-8"),
    )
    _write(
        root / "deployments" / "xlayer-testnet-2.json.attempt.json",
        b'{"stage":"prepared"}\n',
    )
    _write(root / "fixtures" / "ustb-nav.json", FIXTURE)
    _write(root / "manifests" / "sources" / "ustb.json", SOURCE)
    _git(root, "init")
    _git(root, "config", "user.email", "release-test@invalid")
    _git(root, "config", "user.name", "release-test")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "tree")
    return root


def _build(root: Path, out: Path, extra: list[str] | None = None) -> int:
    argv = ["--root", str(root), "--built-at", BUILT_AT, "--out", str(out)]
    if extra:
        argv.extend(extra)
    return main(argv)


def _document(path: Path) -> dict:
    return json.loads(path.read_bytes())


def test_two_runs_over_one_tree_produce_identical_bytes(tmp_path: Path) -> None:
    """Two runs in one process, which is the weaker half of the claim.

    Kept because it is the cheap check, and labelled because it cannot fail for the reason
    its name suggests: both calls share one interpreter, so dict insertion order and
    directory enumeration are identical by construction. Removing every `sorted()` in the
    builder leaves this passing. The cross-process case below is the one that bites.
    """
    root = _tree(tmp_path)
    first = tmp_path / "r1.json"
    second = tmp_path / "r2.json"
    counts = ["--tests-passed", "1600", "--tests-skipped", "1"]

    assert _build(root, first, counts) == 0
    assert _build(root, second, counts) == 0
    assert first.read_bytes() == second.read_bytes()


def test_separate_processes_with_different_hash_seeds_agree(tmp_path: Path) -> None:
    """The reproducibility claim, tested where it can actually break.

    `PYTHONHASHSEED` changes the iteration order of sets and of anything built from them, and
    it is fixed for the life of an interpreter — so no in-process test can vary it. Two
    subprocesses with different seeds are the only way to find out whether the output depends
    on ordering the builder was never entitled to rely on.
    """
    root = _tree(tmp_path)
    outputs = []
    for seed in ("0", "1", "12345"):
        destination = tmp_path / f"seed-{seed}.json"
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        finished = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--out",
                str(destination),
                "--built-at",
                BUILT_AT,
                "--tests-passed",
                "1600",
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
        assert finished.returncode == 0, finished.stderr
        outputs.append(destination.read_bytes())

    assert outputs[0] == outputs[1] == outputs[2], (
        "the document changed with the hash seed, so it depends on iteration order"
    )


def test_an_omitted_test_summary_is_recorded_as_absent_not_as_zero(
    tmp_path: Path,
) -> None:
    """Zero is a real count. Inventing it is the defect this field exists to stop."""
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out) == 0
    tests = _document(out)["tests"]

    assert tests["passed"] is None
    assert tests["skipped"] is None
    assert tests["mutants_killed"] is None
    assert tests["mutants_total"] is None
    assert tests["note"] is not None
    assert "not supplied" in tests["note"]
    assert "0" not in json.dumps(tests)


def test_a_partial_test_summary_does_not_fill_the_missing_counts_with_zero(
    tmp_path: Path,
) -> None:
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out, ["--tests-passed", "1600"]) == 0
    tests = _document(out)["tests"]

    assert tests["passed"] == 1600
    assert tests["skipped"] is None
    assert tests["mutants_killed"] is None
    assert tests["mutants_total"] is None
    assert "skipped" in tests["note"]


def test_supplied_counts_are_recorded_verbatim(tmp_path: Path) -> None:
    out = tmp_path / "release.json"

    assert (
        _build(
            _tree(tmp_path),
            out,
            [
                "--tests-passed",
                "1600",
                "--tests-skipped",
                "1",
                "--mutants-killed",
                "12",
                "--mutants-total",
                "15",
            ],
        )
        == 0
    )
    tests = _document(out)["tests"]

    assert tests == {
        "passed": 1600,
        "skipped": 1,
        "mutants_killed": 12,
        "mutants_total": 15,
        "note": None,
    }


def test_a_dirty_tree_is_recorded_as_not_clean_and_still_emits_a_manifest(
    tmp_path: Path,
) -> None:
    """Refusing would hide the dirt; pretending it is clean would be a false release."""
    root = _tree(tmp_path)
    (root / "dirty.txt").write_bytes(b"not in HEAD\n")
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    commit = _document(out)["commit"]

    assert commit["tree_clean"] is False
    assert commit["sha"] == _git(root, "rev-parse", "HEAD").strip()


def test_a_clean_tree_is_recorded_as_clean(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    assert _document(out)["commit"]["tree_clean"] is True


def test_the_commit_is_the_full_forty_hex_of_head(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    sha = _document(out)["commit"]["sha"]

    assert len(sha) == 40
    assert sha == _git(root, "rev-parse", "HEAD").strip()


def test_built_at_is_the_argument_not_the_clock(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    out = tmp_path / "release.json"
    stamped = "2024-01-02T03:04:05Z"

    assert main(["--root", str(root), "--built-at", stamped, "--out", str(out)]) == 0
    assert _document(out)["built_at"] == stamped


def test_a_timestamp_without_a_utc_suffix_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A naive instant is a different time on every machine that reads it."""
    out = tmp_path / "release.json"

    assert (
        main(
            [
                "--root",
                str(_tree(tmp_path)),
                "--built-at",
                "2026-08-17T00:00:00",
                "--out",
                str(out),
            ]
        )
        == 1
    )
    assert "built-at" in capsys.readouterr().err
    assert not out.exists()


def test_template_and_attempt_and_schema_files_are_not_recorded_as_deployments(
    tmp_path: Path,
) -> None:
    """The mainnet template's 0x1111… address is a placeholder and must not ship."""
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out) == 0
    deployments = _document(out)["deployments"]

    assert "deployments/xlayer-mainnet.template.json" not in deployments
    assert "deployments/xlayer-testnet-2.json.attempt.json" not in deployments
    assert "deployments/manifest.schema.json" not in deployments
    assert "0x1111111111111111111111111111111111111111" not in out.read_bytes().decode(
        "ascii"
    )


def test_a_superseded_deployment_is_recorded_rather_than_omitted(
    tmp_path: Path,
) -> None:
    """Omitting it would make the release look as if the obsolete registry was gone."""
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out) == 0
    deployments = _document(out)["deployments"]

    assert deployments["deployments/xlayer-testnet.json"]["deployment_state"] == (
        "superseded"
    )
    assert deployments["deployments/xlayer-testnet-2.json"]["deployment_state"] == (
        "active"
    )


def test_a_manifest_without_a_registry_address_records_not_deployed(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    body = {key: value for key, value in ACTIVE.items() if key != "registry_address"}
    _write(root / "deployments" / "local.json", json.dumps(body).encode("utf-8"))
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    recorded = _document(out)["deployments"]["deployments/local.json"]

    assert recorded["registry_address"] == "not_deployed"
    assert "0x0000000000000000000000000000000000000000" not in json.dumps(recorded)


def test_a_zero_address_is_recorded_as_not_deployed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    body = {**ACTIVE, "registry_address": "0x" + "00" * 20}
    _write(root / "deployments" / "local.json", json.dumps(body).encode("utf-8"))
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    recorded = _document(out)["deployments"]["deployments/local.json"]

    assert recorded["registry_address"] == "not_deployed"
    assert "0x0000000000000000000000000000000000000000" not in out.read_bytes().decode(
        "ascii"
    )


def test_digests_are_of_the_bytes_on_disk(tmp_path: Path) -> None:
    """A text read would translate these CR-LF bytes and change the digest."""
    root = _tree(tmp_path)
    payload = b"line\r\nwith\r\ncrs\x00and a nul\n"
    fixture = root / "fixtures" / "binary.dat"
    fixture.write_bytes(payload)
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    recorded = _document(out)["fixtures"]["fixtures/binary.dat"]

    assert recorded == hashlib.sha256(payload).hexdigest()
    assert recorded == sha256_file(fixture)
    assert recorded != hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def test_python_pins_are_split_into_runtime_and_dev(tmp_path: Path) -> None:
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out) == 0
    python = _document(out)["python"]

    assert python["requires_python"] == ">=3.11"
    assert python["runtime"] == {"cryptography": "49.0.0", "psutil": "7.2.2"}
    assert python["dev"] == {"pytest": "8.3.3"}
    assert "pytest" not in python["runtime"]
    assert "cryptography" not in python["dev"]


def test_an_unspecified_evm_version_is_recorded_as_absent(tmp_path: Path) -> None:
    """Hardhat's default is not a setting this file named."""
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out) == 0
    contracts = _document(out)["contracts"]

    assert contracts["solidity"] == "0.8.24"
    assert contracts["optimizer"] == {"enabled": True, "runs": 200}
    assert contracts["evm_version"] is None
    assert contracts["package_lock_sha256"] == hashlib.sha256(LOCKFILE).hexdigest()


def test_a_declared_evm_version_is_read_from_the_config(tmp_path: Path) -> None:
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path, evm=True), out) == 0
    assert _document(out)["contracts"]["evm_version"] == "paris"


def test_the_output_contains_no_absolute_paths(tmp_path: Path) -> None:
    out = tmp_path / "release.json"

    assert _build(_tree(tmp_path), out) == 0
    _assert_no_absolute_paths(_document(out))


def test_encoded_keys_are_sorted_and_end_in_a_newline(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    out = tmp_path / "release.json"

    assert _build(root, out) == 0
    payload = out.read_bytes()

    assert payload.endswith(b"\n")
    assert payload == encode_release(_document(out))
    parsed = json.loads(payload)
    assert list(parsed) == sorted(parsed)


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ("pyproject.toml", "pyproject.toml"),
        ("contracts/hardhat.config.js", "contracts/hardhat.config.js"),
        ("fixtures", "fixtures"),
    ],
)
def test_a_missing_required_input_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    missing: str,
    named: str,
) -> None:
    root = _tree(tmp_path)
    target = root / missing
    if target.is_dir():
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
    else:
        target.unlink()
    out = tmp_path / "release.json"

    assert _build(root, out) == 1
    err = capsys.readouterr().err
    assert named in err
    assert not out.exists()


def test_a_missing_built_at_is_refused() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--out", "release.json"])
    assert raised.value.code == 2


def test_the_repository_itself_builds_a_manifest(tmp_path: Path) -> None:
    """Not a fixture. The tree this script will be asked to describe has to parse."""
    out = tmp_path / "release.json"

    assert (
        main(
            [
                "--root",
                str(REPO),
                "--built-at",
                BUILT_AT,
                "--out",
                str(out),
                "--tests-passed",
                "1600",
                "--tests-skipped",
                "1",
            ]
        )
        == 0
    )
    document = _document(out)

    assert document["release_version"] == "1"
    assert document["built_at"] == BUILT_AT
    assert document["python"]["requires_python"] == ">=3.11"
    assert "cryptography" in document["python"]["runtime"]
    assert "pytest" in document["python"]["dev"]
    assert document["contracts"]["solidity"] == "0.8.24"
    assert document["contracts"]["optimizer"] == {"enabled": True, "runs": 200}
    assert document["contracts"]["evm_version"] is None
    deployments = document["deployments"]
    assert "deployments/xlayer-mainnet.template.json" not in deployments
    assert "deployments/xlayer-testnet.template.json" not in deployments
    assert deployments["deployments/xlayer-testnet.json"]["deployment_state"] == (
        "superseded"
    )
    assert deployments["deployments/xlayer-testnet-2.json"]["deployment_state"] == (
        "active"
    )
    assert document["tests"]["passed"] == 1600
    assert document["tests"]["skipped"] == 1
    assert document["tests"]["mutants_killed"] is None
    _assert_no_absolute_paths(document)


def _assert_no_absolute_paths(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert not Path(str(key)).is_absolute(), key
            assert "\\" not in str(key)
            _assert_no_absolute_paths(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_absolute_paths(item)
        return
    if isinstance(value, str):
        assert not Path(value).is_absolute(), value
        assert "\\" not in value


def test_a_commented_out_compiler_setting_is_not_recorded(tmp_path: Path) -> None:
    """A release document must name what the compiler enacted, not what was disabled.

    The scanner matches the first occurrence of each key, and a commented-out setting is an
    earlier occurrence. Before comments were stripped, `// runs: 1` above the real `runs: 200`
    was recorded as 1, and a commented `version: "0.4.0"` was recorded as the solidity
    version — a manifest naming a compiler configuration that never existed.
    """
    root = _tree(tmp_path)
    poisoned = b"""module.exports = {
  solidity: {
    // version: "0.4.0",
    version: "0.8.24",
    settings: {
      optimizer: {
        /* enabled: false, */
        // runs: 1,
        enabled: true,
        runs: 200,
      },
    },
  },
};
"""
    _write(root / "contracts" / "hardhat.config.js", poisoned)
    destination = tmp_path / "release.json"

    assert _build(root, destination, ["--tests-passed", "1"]) == 0
    contracts = json.loads(destination.read_text(encoding="utf-8"))["contracts"]
    assert contracts["solidity"] == "0.8.24"
    assert contracts["optimizer"] == {"enabled": True, "runs": 200}


def test_a_template_named_in_upper_case_is_still_excluded(tmp_path: Path) -> None:
    """The exclusion cannot depend on the platform's idea of a filename.

    `Path.glob("*.json")` is case-insensitive on Windows and case-sensitive on Linux, so the
    same tree produced different documents on different platforms. Worse, the `.template.`
    filter compared case-sensitively, so on Windows a `.TEMPLATE.json` was globbed in and not
    filtered out — shipping its placeholder address as though it were a deployment.
    """
    root = _tree(tmp_path)
    _write(
        root / "deployments" / "xlayer-mainnet.TEMPLATE.json",
        json.dumps(TEMPLATE).encode("utf-8"),
    )
    destination = tmp_path / "release.json"

    assert _build(root, destination, ["--tests-passed", "1"]) == 0
    deployments = json.loads(destination.read_text(encoding="utf-8"))["deployments"]
    assert not any("TEMPLATE" in path.upper() for path in deployments), (
        f"a template was recorded as a deployment: {sorted(deployments)}"
    )
