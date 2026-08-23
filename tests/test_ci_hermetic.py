"""Blocking repository checks must remain reproducible without network access."""

from __future__ import annotations

from html import escape
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from assert_ci_gates import (  # noqa: E402
    NETWORKED_JOB_ALLOWLIST,
    blocking_script_commands,
)


ROOT = Path(__file__).parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LIVE_WORKFLOW = ".github/workflows/site-live-truth.yml"


def _run(arguments: list[str], *, cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1_200,
    )


def _snapshot(destination: Path) -> Path:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    checkout = destination / "repository"
    checkout.mkdir()
    for encoded in listed.split(b"\0"):
        if not encoded:
            continue
        relative = Path(os.fsdecode(encoded))
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"], cwd=checkout, check=True
    )
    subprocess.run(["git", "add", "--all"], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI Hermetic Test",
            "-c",
            "user.email=ci-hermetic@example.invalid",
            "commit",
            "-qm",
            "test fixture",
            "--no-gpg-sign",
        ],
        cwd=checkout,
        check=True,
    )
    return checkout


def _write_local_e2e_report(arguments: list[str]) -> None:
    if len(arguments) < 3 or not arguments[1].endswith("assert_suite_ran.py"):
        return
    expected = [
        arguments[index + 1]
        for index, argument in enumerate(arguments[:-1])
        if argument == "--expect-name"
    ]
    cases = "".join(
        f'<testcase classname="{escape(identity.rsplit("::", 1)[0])}" '
        f'name="{escape(identity.rsplit("::", 1)[1])}"/>'
        for identity in expected
    )
    Path(arguments[2]).write_text(
        '<testsuites><testsuite name="pytest" errors="0" failures="0" '
        f'skipped="0" tests="{len(expected)}">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )


def test_live_network_jobs_are_an_explicit_complete_allowlist() -> None:
    assert set(NETWORKED_JOB_ALLOWLIST) == {LIVE_WORKFLOW}
    for relative, allowed in NETWORKED_JOB_ALLOWLIST.items():
        workflow = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        assert frozenset(workflow["jobs"]) == allowed


def test_every_blocking_repository_script_is_hermetic(tmp_path: Path) -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    commands = blocking_script_commands(workflow)
    assert commands

    checkout = _snapshot(tmp_path)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(checkout / "tests" / "hermetic_sitecustomize"),
            environment.get("PYTHONPATH", ""),
        ]
    ).rstrip(os.pathsep)
    environment["RUNNER_TEMP"] = str(runner_temp)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"

    attempt = runner_temp / "network-attempt"
    environment["TOUCHSTONE_HERMETIC_ATTEMPT_FILE"] = str(attempt)
    construction = _run(
        [
            sys.executable,
            "-c",
            "import socket\nwith socket.socket(): pass",
        ],
        cwd=checkout,
        env=environment,
    )
    assert construction.returncode == 0
    assert not attempt.exists()

    connection_controls = [
        (
            "try: socket.create_connection(('example.invalid', 443))",
            "socket.create_connection",
        ),
        (
            "try: socket.getaddrinfo('example.invalid', 443)",
            "socket.getaddrinfo",
        ),
        (
            "try: socket.socket().connect(('example.invalid', 443))",
            "socket.socket.connect",
        ),
        (
            "try: socket.socket().connect_ex(('example.invalid', 443))",
            "socket.socket.connect_ex",
        ),
    ]
    for call, boundary in connection_controls:
        connection = _run(
            [
                sys.executable,
                "-c",
                f"import socket\n{call}\nexcept Exception: pass",
            ],
            cwd=checkout,
            env=environment,
        )
        assert connection.returncode == 0 and attempt.read_text(
            encoding="utf-8"
        ).startswith(boundary + "\n")
        attempt.unlink()

    for selected in commands:
        command = selected.command.replace("${{ runner.temp }}", str(runner_temp))
        command = command.replace(
            "${{ join(needs.*.result, ' ') }}",
            " ".join("success" for _ in workflow["jobs"]["required"]["needs"]),
        )
        command = command.replace("${RUNNER_TEMP}", str(runner_temp))
        arguments = shlex.split(command, posix=True)
        arguments[0] = sys.executable
        _write_local_e2e_report(arguments)
        command_environment = environment.copy()
        for name, value in selected.environment:
            command_environment[name] = value.replace(
                "${{ runner.temp }}", str(runner_temp)
            )
        if arguments[1].endswith("mutation_check.py"):
            command_environment["TOUCHSTONE_HERMETIC_MUTATION_STUBS"] = "1"
        working_directory = checkout
        if selected.working_directory is not None:
            working_directory /= selected.working_directory
        completed = _run(
            arguments,
            cwd=working_directory,
            env=command_environment,
        )
        output = completed.stdout + completed.stderr
        assert not attempt.exists(), (
            f"blocking job {selected.job!r} attempted network access while running "
            f"{selected.command!r} through {attempt.read_text(encoding='utf-8')}:\n"
            f"{output}"
        )
        assert completed.returncode == 0, (
            f"blocking job {selected.job!r} failed under the network guard while running "
            f"{selected.command!r}:\n{output}"
        )
