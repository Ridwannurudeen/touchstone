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
from pathlib import Path
import sys

import yaml

# The single job branch protection is meant to require. Every other job must reach it.
AGGREGATE = "required"


class WorkflowError(Exception):
    """The workflow is not shaped the way this check needs to read it."""


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


def _steps_text(job: Mapping[str, object]) -> str:
    """Every string in the job's steps, flattened. Enough to see what it references."""
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return ""
    parts: list[str] = []
    for step in steps:
        if isinstance(step, Mapping):
            parts.extend(
                str(value) for value in step.values() if isinstance(value, str)
            )
    return "\n".join(parts)


def weakened(workflow: object) -> list[str]:
    """Return the ways the aggregate could be present and still enforce nothing.

    Covering every job is necessary and not sufficient. Three shapes leave the list intact
    while removing its effect, and a checker that missed them would protect the membership
    of the gate but not the gate:

    * `continue-on-error: true` on any job makes that job's `result` `success` even when it
      failed, so the aggregate reads a green result off a red job.
    * without `if: always()` the aggregate is *skipped* when a dependency fails, and a
      skipped check is not a failed one.
    * an aggregate whose steps never mention `needs.*.result` is not reading the results at
      all, which is the failure mode where every gate is listed and none is consulted.
    """
    jobs = _jobs(workflow)
    if AGGREGATE not in jobs:
        raise WorkflowError(f"there is no {AGGREGATE!r} job to aggregate anything")
    aggregate = jobs[AGGREGATE]
    if not isinstance(aggregate, Mapping):
        raise WorkflowError(f"the {AGGREGATE!r} job is not a mapping")

    problems: list[str] = []
    for name, job in sorted(jobs.items()):
        if isinstance(job, Mapping) and job.get("continue-on-error") is True:
            problems.append(
                f"job {name!r} sets continue-on-error, so its failure is reported as success"
            )

    condition = aggregate.get("if")
    if not (isinstance(condition, str) and "always()" in condition):
        problems.append(
            f"{AGGREGATE!r} has no `if: always()`, so a failing gate skips it rather than "
            "failing it"
        )

    body = _steps_text(aggregate)
    if "needs." not in body or ".result" not in body:
        problems.append(
            f"{AGGREGATE!r} never reads `needs.*.result`, so it waits for the gates without "
            "checking them"
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
