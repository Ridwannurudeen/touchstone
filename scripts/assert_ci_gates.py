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
    except WorkflowError as error:
        print(f"{arguments.workflow}: {error}")
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
