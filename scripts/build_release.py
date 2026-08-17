"""Record exactly what a release consists of, so a claim about it can be checked.

A release that is only a git tag plus a spoken test count cannot be audited: the tag
does not name the compiler settings, the lockfiles, the fixtures or the deployments
it was made of, and a count nobody supplied is a number nobody ran. This repository
has already failed that kind of audit. The document this script writes is the
alternative — a manifest whose every field is either read from the tree or taken as
an explicit argument.

It deliberately does not stamp the clock, does not run the test suite, and does not
invent a count. A builder that reads the clock cannot be reproduced; a builder that
runs the suite (or guesses its result) is another place a number can appear that
nobody can point at a run for.

It also does not execute ``contracts/hardhat.config.js``. That file reads the
deployer private key from the environment; evaluating it would be a secret read,
and the solidity settings are already sitting in the source as literals.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


RELEASE_VERSION = "1"

# The files and directories a manifest must be able to name. An absent one used to
# become an empty section, which reads as "this release has none" rather than "we
# did not look".
PYPROJECT = Path("pyproject.toml")
HARDHAT = Path("contracts") / "hardhat.config.js"
# The compiler settings as data, required by the Hardhat config above and read directly here.
SOLIDITY = Path("contracts") / "solidity.json"
PACKAGE_LOCK = Path("contracts") / "package-lock.json"
DEPLOYMENTS = Path("deployments")
SCHEMA = DEPLOYMENTS / "manifest.schema.json"
FIXTURES = Path("fixtures")
SOURCE_MANIFESTS = Path("manifests") / "sources"

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUILT_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")
_ZERO_ADDRESS = re.compile(r"0x0+\Z", re.IGNORECASE)
_PIN = re.compile(r"\A([^[\s]+(?:\[[^\]]+\])?)==(.*)\Z")


class ReleaseError(Exception):
    """The tree is missing something the manifest must name, or cannot be described."""


def sha256_file(path: Path) -> str:
    """Digest the bytes on disk.

    ``read_text`` / ``write_text`` translate line endings, which has already changed
    a file's digest in this repository. Release claims are about the bytes that
    would be packed, not about a decoded string.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_release(
    root: Path,
    *,
    built_at: str,
    tests_passed: int | None,
    tests_skipped: int | None,
    mutants_killed: int | None,
    mutants_total: int | None,
) -> dict[str, object]:
    """Assemble one release document for ``root``."""
    if _BUILT_AT.fullmatch(built_at) is None:
        raise ReleaseError(
            f"--built-at must be an ISO-8601 UTC instant ending in Z, not {built_at!r}"
        )
    # The shape is not the value. `2026-99-99T99:99:99Z` matched the pattern and was recorded
    # as the moment a release was built, which is a date that does not exist.
    try:
        datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseError(f"--built-at is not a real instant: {built_at!r}") from error
    _require_file(root / PYPROJECT, PYPROJECT)
    _require_file(root / HARDHAT, HARDHAT)
    _require_file(root / SOLIDITY, SOLIDITY)
    _require_file(root / PACKAGE_LOCK, PACKAGE_LOCK)
    _require_file(root / SCHEMA, SCHEMA)
    _require_dir(root / FIXTURES, FIXTURES)
    _require_dir(root / SOURCE_MANIFESTS, SOURCE_MANIFESTS)
    _require_dir(root / DEPLOYMENTS, DEPLOYMENTS)

    return {
        "release_version": RELEASE_VERSION,
        "commit": _commit(root),
        "built_at": built_at,
        "python": _python(root / PYPROJECT),
        "contracts": _contracts(root),
        "deployments": _deployments(root),
        "schemas": {SCHEMA.as_posix(): sha256_file(root / SCHEMA)},
        "fixtures": _file_digests(root / FIXTURES, root=root),
        "manifests": _file_digests(root / SOURCE_MANIFESTS, root=root),
        "tests": _tests(
            passed=tests_passed,
            skipped=tests_skipped,
            mutants_killed=mutants_killed,
            mutants_total=mutants_total,
        ),
    }


def encode_release(document: dict[str, object]) -> bytes:
    """Serialise so two builds of the same document are byte-identical.

    Keys and mappings are sorted; the indent is fixed; the payload is UTF-8 bytes
    with ``\\n`` separators. Writing the text through ``write_text`` on Windows
    would turn those into ``\\r\\n`` and two runs would no longer match.
    """
    return (
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="path to write the release document",
    )
    parser.add_argument(
        "--built-at",
        required=True,
        dest="built_at",
        help="ISO-8601 UTC instant recorded as built_at. The builder does not "
        "read the clock: a stamp it chose itself cannot be reproduced.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to describe (defaults to the tree this script lives in)",
    )
    parser.add_argument(
        "--tests-passed",
        type=_non_negative,
        default=None,
        dest="tests_passed",
        help="passed-test count from a run the caller actually performed",
    )
    parser.add_argument(
        "--tests-skipped",
        type=_non_negative,
        default=None,
        dest="tests_skipped",
        help="skipped-test count from that same run",
    )
    parser.add_argument(
        "--mutants-killed",
        type=_non_negative,
        default=None,
        dest="mutants_killed",
        help="mutants killed, from a mutation run the caller actually performed",
    )
    parser.add_argument(
        "--mutants-total",
        type=_non_negative,
        default=None,
        dest="mutants_total",
        help="mutants total, from that same mutation run",
    )
    arguments = parser.parse_args(argv)

    try:
        # The document records whether the tree matched HEAD when it was read. Writing the
        # document *into* that tree changes the answer, so the first build recorded
        # `tree_clean: true` and dirtied the tree, and an identical second build recorded
        # `false` — two different documents from one tree, which is precisely the
        # reproducibility this exists to provide. Refused rather than papered over: a release
        # artifact belongs outside the tree it describes.
        _refuse_output_inside(arguments.root, arguments.out)
        document = build_release(
            arguments.root,
            built_at=arguments.built_at,
            tests_passed=arguments.tests_passed,
            tests_skipped=arguments.tests_skipped,
            mutants_killed=arguments.mutants_killed,
            mutants_total=arguments.mutants_total,
        )
        _write(arguments.out, encode_release(document))
    except ReleaseError as error:
        print(f"RELEASE FAIL: {error}", file=sys.stderr)
        return 1

    print(f"wrote {arguments.out}")
    return 0


def _non_negative(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def _refuse_output_inside(root: Path, out: Path) -> None:
    """Refuse to write the release document into the tree it describes.

    Compared as resolved paths, because `--out release.json` from inside the repository and
    an absolute path to the same place are the same file and only one of them looks like it.
    """
    resolved_root = root.resolve()
    resolved_out = out.resolve()
    if resolved_root == resolved_out or resolved_root in resolved_out.parents:
        raise ReleaseError(
            f"{out} is inside {root}; writing the document into the tree it describes "
            "changes that tree's cleanliness and makes two identical builds disagree"
        )


def _require_file(path: Path, label: Path) -> None:
    if not path.is_file():
        raise ReleaseError(f"{label.as_posix()} is missing")


def _require_dir(path: Path, label: Path) -> None:
    if not path.is_dir():
        raise ReleaseError(f"{label.as_posix()} is missing")


def _commit(root: Path) -> dict[str, object]:
    sha = _git(root, "rev-parse", "HEAD").strip()
    if _COMMIT.fullmatch(sha) is None:
        raise ReleaseError(
            f"git rev-parse HEAD did not return a 40-hex commit: {sha!r}"
        )
    # Non-empty porcelain is the whole signal. A dirty tree still gets a document —
    # refusing would hide the fact that the release does not correspond to a commit,
    # and pretending it is clean would be worse.
    porcelain = _git(root, "status", "--porcelain")
    return {"sha": sha, "tree_clean": porcelain.strip() == ""}


def _git(root: Path, *arguments: str) -> str:
    # Drop GIT_DIR / GIT_WORK_TREE so we describe ``root``, not whatever repo the
    # caller's environment happens to be pointing at.
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
    if finished.returncode != 0:
        detail = (
            finished.stderr.strip()
            or finished.stdout.strip()
            or (f"exit {finished.returncode}")
        )
        raise ReleaseError(f"git {' '.join(arguments)} failed: {detail}")
    return finished.stdout


def _python(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        try:
            parsed = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            raise ReleaseError(
                f"{PYPROJECT.as_posix()} is not readable TOML: {error}"
            ) from error
    project = parsed.get("project")
    if not isinstance(project, dict):
        raise ReleaseError(f"{PYPROJECT.as_posix()} has no [project] table")
    requires = project.get("requires-python")
    if not isinstance(requires, str) or not requires:
        raise ReleaseError(f"{PYPROJECT.as_posix()} does not name requires-python")
    runtime = project.get("dependencies")
    extras = project.get("optional-dependencies")
    if not isinstance(runtime, list):
        raise ReleaseError(f"{PYPROJECT.as_posix()} does not name runtime dependencies")
    if not isinstance(extras, dict) or not isinstance(extras.get("dev"), list):
        raise ReleaseError(
            f"{PYPROJECT.as_posix()} does not name [project.optional-dependencies] dev"
        )
    return {
        "requires_python": requires,
        "runtime": _pins(runtime, where="runtime"),
        "dev": _pins(extras["dev"], where="dev"),
    }


def _pins(specifiers: list[object], *, where: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for specifier in specifiers:
        if not isinstance(specifier, str):
            raise ReleaseError(f"{where} dependency {specifier!r} is not a string")
        matched = _PIN.fullmatch(specifier)
        if matched is None:
            raise ReleaseError(
                f"{where} dependency {specifier!r} is not pinned with =="
            )
        name, version = matched.group(1), matched.group(2)
        if name in pins:
            raise ReleaseError(f"{where} dependency {name} is listed more than once")
        pins[name] = version
    return dict(sorted(pins.items()))


def _contracts(root: Path) -> dict[str, object]:
    """The compiler settings, read from the data file Hardhat itself consumes.

    These used to be recovered by regex over `hardhat.config.js`, and that was unsound in a
    way comment-stripping only partly hid: the scanner took the *first* object that looked
    like a solidity block, so an unused config earlier in the file was reported as the one
    the contracts were built with. A release document naming a compiler setting the compiler
    never used is worse than one that omits it. `contracts/solidity.json` is now the single
    source, required by the Hardhat config and parsed here, so there is nothing left to
    misread.
    """
    declared = json.loads((root / SOLIDITY).read_bytes())
    settings = declared.get("settings", {})
    optimizer = settings.get("optimizer", {})
    version = declared.get("version")
    if not isinstance(version, str) or not isinstance(optimizer.get("runs"), int):
        raise ReleaseError(
            f"{SOLIDITY.as_posix()} does not declare version and optimizer"
        )
    return {
        "solidity": version,
        "optimizer": {
            "enabled": bool(optimizer.get("enabled")),
            "runs": optimizer["runs"],
        },
        # Absent is recorded as absent rather than filled from the toolchain's default:
        # reading a default out of another program's memory is how a release claims a target
        # nobody set. It is declared explicitly in the data file, so this is not None.
        "evm_version": settings.get("evmVersion"),
        "solidity_config_sha256": sha256_file(root / SOLIDITY),
        "package_lock_sha256": sha256_file(root / PACKAGE_LOCK),
    }


def _deployments(root: Path) -> dict[str, object]:
    records: dict[str, object] = {}
    # `glob("*.json")` is case-insensitive on Windows and case-sensitive on Linux, so the
    # same tree produced different documents on different platforms — and a template named
    # `X.TEMPLATE.json` was matched by the glob on Windows while `_is_real_manifest`'s
    # case-sensitive `.template.` filter let it through, shipping its placeholder address.
    # Iterating the directory and comparing the suffix here makes the set identical
    # everywhere, which a reproducibility claim requires.
    for path in sorted(
        entry
        for entry in (root / DEPLOYMENTS).iterdir()
        if entry.is_file() and entry.name.endswith(".json")
    ):
        if not _is_real_manifest(path):
            continue
        relative = path.relative_to(root).as_posix()
        records[relative] = _deployment_record(path, label=relative)
    return records


def _is_real_manifest(path: Path) -> bool:
    # Case-folded, because the exclusions must not depend on how a file happens to be named
    # or on which filesystem it sits. A `.TEMPLATE.json` compared case-sensitively passed
    # every filter and shipped its placeholder address as a deployment.
    name = path.name.lower()
    # Templates carry placeholder addresses (including the 0x1111… marker in
    # xlayer-mainnet.template.json). Attempts are a journal, not a deployment.
    # The schema describes manifests; it is not one.
    if name == SCHEMA.name.lower():
        return False
    if ".template." in name:
        return False
    if ".attempt." in name:
        return False
    return True


def _deployment_record(path: Path, *, label: str) -> dict[str, object]:
    try:
        body = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"{label} is not JSON: {error}") from error
    if not isinstance(body, dict):
        raise ReleaseError(f"{label} is not a manifest object")
    for field in ("network", "chain_id", "deployment_state"):
        if field not in body:
            raise ReleaseError(f"{label} does not name {field}")
    digest = body.get("registry_runtime_bytecode_sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ReleaseError(f"{label} does not record a runtime bytecode digest")
    return {
        "network": body["network"],
        "chain_id": body["chain_id"],
        "registry_address": _registry_address(body.get("registry_address")),
        "deployment_state": body["deployment_state"],
        "registry_runtime_bytecode_sha256": digest,
    }


def _registry_address(value: object) -> str:
    # A field that needs a real deployed address and does not have one is recorded
    # as the literal "not_deployed". 0x000… is a placeholder, not an address this
    # release can be claimed against.
    if not isinstance(value, str) or not value:
        return "not_deployed"
    if _ZERO_ADDRESS.fullmatch(value):
        return "not_deployed"
    return value


def _file_digests(directory: Path, *, root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        mapping[path.relative_to(root).as_posix()] = sha256_file(path)
    if not mapping:
        relative = directory.relative_to(root).as_posix()
        raise ReleaseError(f"{relative} contains no files")
    return mapping


def _tests(
    *,
    passed: int | None,
    skipped: int | None,
    mutants_killed: int | None,
    mutants_total: int | None,
) -> dict[str, object]:
    # Zero is a real count. An omitted flag must not become zero, and must not be
    # guessed by running the suite — both have already produced numbers nobody ran.
    supplied = {
        "passed": passed,
        "skipped": skipped,
        "mutants_killed": mutants_killed,
        "mutants_total": mutants_total,
    }
    missing = [name for name, value in supplied.items() if value is None]
    if not missing:
        note: str | None = None
    elif len(missing) == 4:
        note = (
            "test counts were not supplied; this builder does not run the suite "
            "and will not invent them"
        )
    else:
        note = (
            "these test counts were not supplied and were not invented: "
            + ", ".join(missing)
        )
    return {**supplied, "note": note}


def _write(destination: Path, payload: bytes) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    except OSError as error:
        raise ReleaseError(f"{destination} cannot be written: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
