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

# The only condition that makes the aggregate run when a gate has failed. Compared exactly,
# because `always() && false` contains it and never runs.
ALWAYS = "always()"

# The script that actually judges the results. Kept separate so its behaviour is proved by
# its own tests instead of being asserted about inline shell, which cannot be checked.
ENFORCER = "scripts/assert_gates_passed.py"


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


def _steps(job: Mapping[str, object]) -> list[Mapping[str, object]]:
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, Mapping)]


def _runs_the_enforcer(step: Mapping[str, object]) -> bool:
    """Does this step hand the gate results to the script that judges them?

    Both halves are required. A step naming the script without the results judges an empty
    list, and a step expanding the results without the script is the echo that passed the
    previous version of this checker.
    """
    body = step.get("run")
    if not isinstance(body, str):
        return False
    return ENFORCER in body and "needs." in body and ".result" in body


def weakened(workflow: object) -> list[str]:
    """Return the ways the aggregate could be present and still enforce nothing.

    Covering every job is necessary and nowhere near sufficient. Each shape below leaves the
    `needs` list perfectly intact while removing its effect, and the first version of this
    function missed four of them because it looked for text rather than for enforcement:

    * `continue-on-error: true` on a job — or on any single step — reports a failure as
      success, so the aggregate reads green off red.
    * without `if: always()` the aggregate is *skipped* when a gate fails, and a skipped
      check is not a failed one. The condition must be exactly `always()`: a substring test
      accepts `always() && false`, which never runs it at all.
    * an aggregate that reads `needs.*.result` and merely echoes it enforces nothing. That
      is why the decision lives in `scripts/assert_gates_passed.py`, which is tested
      directly; all this has to establish is that the script is what runs, with the results
      passed to it.
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
        if job.get("continue-on-error") is True:
            problems.append(
                f"job {name!r} sets continue-on-error, so its failure is reported as success"
            )
        for position, step in enumerate(_steps(job), start=1):
            if step.get("continue-on-error") is True:
                problems.append(
                    f"step {position} of job {name!r} sets continue-on-error, so its "
                    "failure is reported as success"
                )

    condition = aggregate.get("if")
    if not (isinstance(condition, str) and condition.strip() == ALWAYS):
        problems.append(
            f"{AGGREGATE!r} has `if: {condition!r}`, not exactly {ALWAYS!r}; anything else "
            "can leave it skipped rather than failed when a gate fails"
        )

    if not any(_runs_the_enforcer(step) for step in _steps(aggregate)):
        problems.append(
            f"no step of {AGGREGATE!r} runs {ENFORCER} with `needs.*.result`, so the "
            "results are collected without being judged"
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
