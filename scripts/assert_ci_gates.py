"""Prove the aggregate CI check actually covers every job in the workflow.

The workflow funnels every gate into one `required` job so that branch protection has a
single check to demand. That arrangement carried a comment claiming a new job "cannot
silently leave it unguarded", and the claim was false: `needs` is a hand-written list, so a
job added below it is simply not waited on, and the aggregate goes green without it. The
comment described an intention, and an intention is not a control.

This is the control. It reads the workflow and refuses any job the aggregate does not
depend on, which turns "we remembered" into "the build fails if we forgot".

It deliberately does not check that branch protection *requires* the aggregate — that lives
in repository settings, not in the tree, and no script in the tree can honestly assert it.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import shlex
import sys

import yaml

# The single job branch protection is meant to require. Every other job must reach it.
AGGREGATE = "required"

# The only condition that makes the aggregate run when a gate has failed. Compared exactly,
# because `always() && false` contains it and never runs.
ALWAYS = "always()"

# The script that actually judges the results is kept separate so its behaviour is proved by
# its own tests instead of being asserted about inline shell, which cannot be checked.
ENFORCER_COMMAND = (
    'python scripts/assert_gates_passed.py "${{ join(needs.*.result, \' \') }}"'
)
CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_ACTION = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
SAFE_WORKFLOW_ENV = {"PYTHONUTF8": "1"}

# These jobs deliberately compare the repository with external state. Adding an exemption is
# meant to be a visible review decision, and the hermetic-CI test proves these names are the
# complete job set of the live workflow rather than a stale or misspelled list.
NETWORKED_JOB_ALLOWLIST = {
    ".github/workflows/site-live-truth.yml": frozenset(
        {"live_public_truth", "onchain_registry_truth"}
    )
}


class WorkflowError(Exception):
    """The workflow is not shaped the way this check needs to read it."""


@dataclass(frozen=True, slots=True)
class BlockingScriptCommand:
    """One checked-in Python entrypoint run by an aggregate dependency."""

    job: str
    command: str
    working_directory: str | None
    environment: tuple[tuple[str, str], ...]


def _jobs(workflow: object) -> Mapping[str, object]:
    if not isinstance(workflow, Mapping):
        raise WorkflowError("the workflow is not a mapping")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        raise WorkflowError("the workflow declares no jobs")
    return jobs


def _needs(job: object) -> list[str]:
    if not isinstance(job, Mapping):
        raise WorkflowError(f"the {AGGREGATE!r} job is not a mapping")
    declared = job.get("needs", [])
    # GitHub accepts a bare string for a single dependency, and a workflow that used one
    # would otherwise be read character by character.
    if isinstance(declared, str):
        return [declared]
    if not isinstance(declared, list) or not all(
        isinstance(name, str) for name in declared
    ):
        raise WorkflowError(
            f"{AGGREGATE!r} declares a `needs` that is not a list of names"
        )
    return declared


def ungoverned(workflow: object) -> tuple[list[str], list[str]]:
    """Return (jobs the aggregate does not wait for, names it waits for that do not exist)."""
    jobs = _jobs(workflow)
    if AGGREGATE not in jobs:
        raise WorkflowError(f"there is no {AGGREGATE!r} job to aggregate anything")
    needs = _needs(jobs[AGGREGATE])
    missing = sorted(set(jobs) - {AGGREGATE} - set(needs))
    unknown = sorted(set(needs) - set(jobs))
    return missing, unknown


def _steps(job: Mapping[str, object]) -> list[Mapping[str, object]]:
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, Mapping)]


def _environment(scope: Mapping[str, object], name: str) -> dict[str, str]:
    declared = scope.get("env", {})
    if not isinstance(declared, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in declared.items()
    ):
        raise WorkflowError(f"{name} has an environment that is not text-to-text")
    return dict(declared)


def _is_python_repository_check(arguments: list[str], job: str) -> bool:
    if not arguments or arguments[0] != "python":
        if any(
            argument.lstrip("./").replace("\\", "/").startswith("scripts/")
            for argument in arguments
        ):
            raise WorkflowError(
                f"job {job!r} invokes a repository script without the guarded Python "
                "interpreter"
            )
        return False

    position = 1
    while position < len(arguments) and arguments[position] in {
        "-B",
        "-E",
        "-O",
        "-OO",
        "-s",
        "-u",
        "-v",
        "-x",
    }:
        position += 1
    if position >= len(arguments):
        raise WorkflowError(f"job {job!r} runs Python without a command")
    entrypoint = arguments[position]
    if entrypoint in {"-I", "-S"}:
        raise WorkflowError(
            f"job {job!r} disables the startup hook required by the network guard"
        )
    if entrypoint == "-m":
        if position + 1 >= len(arguments):
            raise WorkflowError(f"job {job!r} runs Python -m without a module")
        return arguments[position + 1] not in {"pip", "pytest"}
    if entrypoint == "-c":
        if position + 1 >= len(arguments):
            raise WorkflowError(f"job {job!r} runs Python -c without code")
        return True
    if entrypoint.startswith("-"):
        raise WorkflowError(
            f"job {job!r} uses unsupported Python invocation option {entrypoint!r}"
        )
    normalized = entrypoint.lstrip("./").replace("\\", "/")
    if normalized.startswith("scripts/") and normalized.endswith(".py"):
        return True
    raise WorkflowError(
        f"job {job!r} uses unclassified Python entrypoint {entrypoint!r}"
    )


def blocking_script_commands(workflow: object) -> list[BlockingScriptCommand]:
    """Return direct ``python scripts/...`` commands from jobs the aggregate requires.

    Package installation and test runners provision or exercise the environment; they are
    not repository script entrypoints and some intentionally use the loopback network. The
    returned class is the one the hermetic guard can execute without recursively invoking
    itself, and a new checked-in script step is selected without changing this function.
    """
    jobs = _jobs(workflow)
    if AGGREGATE not in jobs:
        raise WorkflowError(f"there is no {AGGREGATE!r} job to aggregate anything")

    workflow_environment = _environment(workflow, "the workflow")
    commands: list[BlockingScriptCommand] = []
    for name in [*_needs(jobs[AGGREGATE]), AGGREGATE]:
        job = jobs.get(name)
        if not isinstance(job, Mapping):
            raise WorkflowError(f"the blocking job {name!r} is not a mapping")
        job_environment = {**workflow_environment, **_environment(job, f"job {name!r}")}
        for step in _steps(job):
            body = step.get("run")
            if not isinstance(body, str):
                continue
            command = body.strip()
            if "\n" in command and "python" in command:
                raise WorkflowError(
                    f"job {name!r} has a multi-command Python step the guard cannot "
                    "execute exactly"
                )
            try:
                arguments = shlex.split(command, posix=True)
            except ValueError as error:
                raise WorkflowError(
                    f"job {name!r} has an unreadable run command: {error}"
                ) from error
            if not _is_python_repository_check(arguments, name):
                continue
            if "shell" in step:
                raise WorkflowError(
                    f"job {name!r} gives a repository script a custom shell"
                )
            working_directory = step.get("working-directory")
            if working_directory is not None and not isinstance(working_directory, str):
                raise WorkflowError(f"job {name!r} has a non-text working-directory")
            environment = {
                **job_environment,
                **_environment(step, f"a step of job {name!r}"),
            }
            commands.append(
                BlockingScriptCommand(
                    name,
                    command,
                    working_directory,
                    tuple(environment.items()),
                )
            )
    return commands


def _runs_the_enforcer(step: Mapping[str, object]) -> bool:
    """Does this step run exactly the one command that judges every gate result?

    Anything looser proves text, not execution. A command that names one result, appends
    `|| true`, or merely echoes the canonical command contains every interesting substring
    while enforcing nothing. Only surrounding whitespace is immaterial; the shell body is
    otherwise exact so no unverified operator or extra command can change its result.
    """
    body = step.get("run")
    return isinstance(body, str) and body.strip() == ENFORCER_COMMAND


def _trusted_aggregate_steps(job: Mapping[str, object]) -> bool:
    """Does the aggregate reach the enforcer through only the pinned setup steps?"""
    return job.get("steps") == [
        {
            "uses": CHECKOUT_ACTION,
            "with": {"persist-credentials": False},
        },
        {
            "uses": SETUP_PYTHON_ACTION,
            "with": {"python-version": "3.12"},
        },
        {
            "name": "Every gate must have succeeded",
            "run": ENFORCER_COMMAND,
        },
    ]


def weakened(workflow: object) -> list[str]:
    """Return the ways the aggregate could be present and still enforce nothing.

    Covering every job is necessary and nowhere near sufficient. Each shape below leaves the
    `needs` list perfectly intact while removing its effect, and the first version of this
    function missed four of them because it looked for text rather than for enforcement:

    * any `continue-on-error` value other than absent or literal `false` on a job — or on
      any single step — may report a failure as success, so the aggregate reads green off
      red. Expressions are rejected because their runtime value is not statically false.
    * without `if: always()` the aggregate is *skipped* when a gate fails, and a skipped
      check is not a failed one. The condition must be exactly `always()`: a substring test
      accepts `always() && false`, which never runs it at all.
    * an aggregate that reads only one result, ignores failure, or merely echoes the command
      enforces nothing. That is why the decision lives in `scripts/assert_gates_passed.py`,
      which is tested directly; the workflow must run its one canonical invocation exactly.
    * an exact command still means nothing under a shell that ignores its status, from a
      forged working directory or interpreter, after another step replaces the script, or
      on a step condition that skips it. The aggregate therefore has one pinned runner,
      environment and three-step execution recipe.
    """
    jobs = _jobs(workflow)
    if AGGREGATE not in jobs:
        raise WorkflowError(f"there is no {AGGREGATE!r} job to aggregate anything")
    aggregate = jobs[AGGREGATE]
    if not isinstance(aggregate, Mapping):
        raise WorkflowError(f"the {AGGREGATE!r} job is not a mapping")

    problems: list[str] = []
    for name, job in sorted(jobs.items()):
        if not isinstance(job, Mapping):
            continue
        if job.get("continue-on-error", False) is not False:
            problems.append(
                f"job {name!r} sets continue-on-error, so its failure is reported as success"
            )
        for position, step in enumerate(_steps(job), start=1):
            if step.get("continue-on-error", False) is not False:
                problems.append(
                    f"step {position} of job {name!r} sets continue-on-error, so its "
                    "failure is reported as success"
                )
            step_condition = step.get("if")
            if step_condition is not None and not (
                isinstance(step_condition, str)
                and step_condition.strip() == ALWAYS
            ):
                problems.append(
                    f"step {position} of job {name!r} has a step condition that can "
                    "skip its work"
                )

    condition = aggregate.get("if")
    if not (isinstance(condition, str) and condition.strip() == ALWAYS):
        problems.append(
            f"{AGGREGATE!r} has `if: {condition!r}`, not exactly {ALWAYS!r}; anything else "
            "can leave it skipped rather than failed when a gate fails"
        )

    if not any(_runs_the_enforcer(step) for step in _steps(aggregate)):
        problems.append(
            f"no step of {AGGREGATE!r} runs the canonical command {ENFORCER_COMMAND!r}, "
            "so the results are collected without being judged"
        )
    workflow_environment = workflow.get("env", {})
    environment = (
        dict(workflow_environment)
        if isinstance(workflow_environment, Mapping)
        else None
    )
    if (
        aggregate.get("runs-on") != "ubuntu-24.04"
        or "defaults" in workflow
        or environment not in ({}, SAFE_WORKFLOW_ENV)
        or any(
            key in aggregate for key in ("defaults", "env", "container", "strategy")
        )
    ):
        problems.append(
            f"{AGGREGATE!r} does not use the trusted execution context for the enforcer"
        )
    if not _trusted_aggregate_steps(aggregate):
        problems.append(
            f"{AGGREGATE!r} does not use the trusted execution recipe for the enforcer"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workflow",
        type=Path,
        nargs="?",
        default=Path(".github/workflows/ci.yml"),
        help="the workflow to check",
    )
    arguments = parser.parse_args(argv)

    if not arguments.workflow.is_file():
        print(f"no workflow at {arguments.workflow}")
        return 1
    try:
        loaded = yaml.safe_load(arguments.workflow.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        print(f"{arguments.workflow} is not readable YAML: {error}")
        return 1
    try:
        missing, unknown = ungoverned(loaded)
        neutralised = weakened(loaded)
    except WorkflowError as error:
        print(f"{arguments.workflow}: {error}")
        return 1

    if neutralised:
        print(f"{arguments.workflow}:")
        for problem in neutralised:
            print(f"  - {problem}")
        return 1

    if missing or unknown:
        print(f"{arguments.workflow}:")
        for name in missing:
            print(
                f"  - job {name!r} is not in {AGGREGATE!r}'s `needs`, so it gates nothing"
            )
        for name in unknown:
            print(
                f"  - {AGGREGATE!r} needs {name!r}, which is not a job in this workflow"
            )
        return 1

    covered = len(_needs(_jobs(loaded)[AGGREGATE]))
    print(f"{AGGREGATE!r} waits for all {covered} other job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
