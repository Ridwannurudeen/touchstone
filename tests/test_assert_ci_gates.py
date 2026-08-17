"""The check that the aggregate gate covers everything needs to be checked itself.

`assert_ci_gates.py` exists because the workflow claimed a property it did not have: adding
a job was said to be unable to leave it unguarded, when `needs` is a hand-written list and a
new job is simply not waited for. A checker that fails open here restores exactly the
false confidence it was written to remove, so its refusals are tested rather than assumed.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from assert_ci_gates import (  # noqa: E402
    AGGREGATE,
    WorkflowError,
    main,
    ungoverned,
    weakened,
)

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_ACTION = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"


def covering(*jobs: str) -> dict:
    """A workflow whose aggregate waits for exactly ``jobs``."""
    declared = {name: {"runs-on": "ubuntu-24.04"} for name in jobs}
    declared[AGGREGATE] = {"runs-on": "ubuntu-24.04", "needs": list(jobs)}
    return {"jobs": declared}


def test_the_real_workflow_covers_every_job() -> None:
    """Not a fixture. The file that actually runs is the one that has to be right."""
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert ungoverned(loaded) == ([], [])
    assert main([str(WORKFLOW)]) == 0


def test_a_job_missing_from_the_aggregate_is_refused() -> None:
    """The defect this exists for: a job is added, `needs` is not, nothing waits for it."""
    workflow = covering("lint", "tests")
    workflow["jobs"]["a_new_gate"] = {"runs-on": "ubuntu-24.04"}

    assert ungoverned(workflow) == (["a_new_gate"], [])


def test_a_need_naming_no_job_is_refused() -> None:
    """A renamed job leaves the aggregate waiting for something that cannot run."""
    workflow = covering("lint")
    workflow["jobs"][AGGREGATE]["needs"].append("renamed_away")

    assert ungoverned(workflow) == ([], ["renamed_away"])


def test_a_single_dependency_written_as_a_string_is_understood() -> None:
    """GitHub accepts a bare string, which a naive reader consumes one character at a time."""
    workflow = covering("lint")
    workflow["jobs"][AGGREGATE]["needs"] = "lint"

    assert ungoverned(workflow) == ([], [])


def test_an_aggregate_that_waits_for_nothing_is_refused() -> None:
    workflow = covering("lint", "tests")
    workflow["jobs"][AGGREGATE]["needs"] = []

    assert ungoverned(workflow) == (["lint", "tests"], [])


@pytest.mark.parametrize(
    ("workflow", "reason"),
    [
        ({"jobs": {"lint": {}}}, "no 'required' job"),
        ({"jobs": {}}, "declares no jobs"),
        ({"on": "push"}, "declares no jobs"),
        ("not a mapping at all", "not a mapping"),
    ],
)
def test_a_workflow_this_cannot_read_is_refused(workflow: object, reason: str) -> None:
    """Silence on an unreadable workflow would be the fail-open this check exists to close."""
    with pytest.raises(WorkflowError):
        ungoverned(workflow)


def test_a_needs_that_is_not_a_list_of_names_is_refused() -> None:
    workflow = covering("lint")
    workflow["jobs"][AGGREGATE]["needs"] = [{"job": "lint"}]

    with pytest.raises(WorkflowError):
        ungoverned(workflow)


def test_a_missing_workflow_file_is_refused(tmp_path: Path) -> None:
    assert main([str(tmp_path / "absent.yml")]) == 1


def test_unreadable_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ci.yml"
    path.write_text("jobs: [unclosed", encoding="utf-8")

    assert main([str(path)]) == 1


def test_a_workflow_with_an_uncovered_job_exits_nonzero(tmp_path: Path) -> None:
    """Through `main`, because the exit code is what CI actually reads."""
    workflow = covering("lint")
    workflow["jobs"]["forgotten"] = {"runs-on": "ubuntu-24.04"}
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(workflow), encoding="utf-8")

    assert main([str(path)]) == 1


def enforcing(*jobs: str) -> dict:
    """A workflow whose aggregate both covers every job and actually enforces the results.

    This fixture was itself a finding. It used to end in `echo "${results}"` — it read the
    results and did nothing with them — and the checker accepted it, which is precisely the
    bypass being closed. A fixture that demonstrates the hole while being named for its
    absence is worse than no fixture, so it now runs the enforcer.
    """
    workflow = covering(*jobs)
    workflow["jobs"][AGGREGATE]["if"] = "always()"
    workflow["jobs"][AGGREGATE]["steps"] = [
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
            "run": "python scripts/assert_gates_passed.py \"${{ join(needs.*.result, ' ') }}\""
        }
    ]
    return workflow


def test_the_real_workflow_is_not_neutralised() -> None:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert weakened(loaded) == []


def test_an_aggregate_without_always_is_refused() -> None:
    """A failing gate would skip the aggregate, and a skipped check is not a failed one."""
    workflow = enforcing("lint")
    del workflow["jobs"][AGGREGATE]["if"]

    assert any("always()" in problem for problem in weakened(workflow))


def test_a_job_that_continues_on_error_is_refused() -> None:
    """`needs.lint.result` reads `success` off a job that failed, so the gate reads green."""
    workflow = enforcing("lint")
    workflow["jobs"]["lint"]["continue-on-error"] = True

    assert any("continue-on-error" in problem for problem in weakened(workflow))


def test_an_aggregate_that_never_reads_the_results_is_refused() -> None:
    """Every gate listed, none consulted — the membership is intact and the check is not."""
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"] = [{"run": "echo nothing to see"}]

    assert any("needs.*.result" in problem for problem in weakened(workflow))


def test_an_enforcing_aggregate_is_accepted() -> None:
    assert weakened(enforcing("lint", "tests")) == []


def test_a_neutralised_workflow_exits_nonzero(tmp_path: Path) -> None:
    """Through `main`, because the exit code is what CI actually reads."""
    workflow = enforcing("lint")
    workflow["jobs"]["lint"]["continue-on-error"] = True
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(workflow), encoding="utf-8")

    assert main([str(path)]) == 1


def test_an_aggregate_that_only_echoes_the_results_is_refused() -> None:
    """The bypass that survived the first widening, and that this fixture used to contain.

    Reading `needs.*.result` and printing it satisfies every textual test while enforcing
    nothing, which is why the decision moved into a script the checker can name.
    """
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"] = [
        {"run": 'echo "${{ join(needs.*.result, \' \') }}"'}
    ]

    assert any("without being judged" in problem for problem in weakened(workflow))


def test_an_enforcer_given_no_results_is_refused() -> None:
    """Naming the script is half of it; a call with no results judges an empty list."""
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"] = [
        {"run": "python scripts/assert_gates_passed.py"}
    ]

    assert any("without being judged" in problem for problem in weakened(workflow))


def test_an_enforcer_receiving_only_one_gate_result_is_refused() -> None:
    """Judging one named dependency leaves every other gate outside the decision."""
    workflow = enforcing("lint", "tests")
    workflow["jobs"][AGGREGATE]["steps"] = [
        {
            "run": 'python scripts/assert_gates_passed.py "${{ needs.lint.result }}"'
        }
    ]

    assert any("canonical command" in problem for problem in weakened(workflow))


def test_an_enforcer_whose_failure_is_ignored_is_refused() -> None:
    """The right command followed by `|| true` reports success when the enforcer refuses."""
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"][-1]["run"] += " || true"

    assert any("canonical command" in problem for problem in weakened(workflow))


def test_an_enforcer_command_that_is_only_echoed_is_refused() -> None:
    """Mentioning the whole command is still not executing it."""
    workflow = enforcing("lint")
    command = workflow["jobs"][AGGREGATE]["steps"][-1]["run"]
    workflow["jobs"][AGGREGATE]["steps"][-1]["run"] = f"echo {command}"

    assert any("canonical command" in problem for problem in weakened(workflow))


def test_a_step_that_continues_on_error_is_refused() -> None:
    """Step-level continue-on-error lets the job pass when that step fails."""
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"][-1]["continue-on-error"] = True

    assert any("step 3 of job" in problem for problem in weakened(workflow))


def test_a_gates_own_step_continuing_on_error_is_refused() -> None:
    """Not only the aggregate's steps: a gate whose failing step is excused reads green."""
    workflow = enforcing("lint")
    workflow["jobs"]["lint"]["steps"] = [{"run": "false", "continue-on-error": True}]

    assert any("job 'lint'" in problem for problem in weakened(workflow))


@pytest.mark.parametrize("level", ["job", "step"])
def test_an_expression_cannot_make_continue_on_error_safe(level: str) -> None:
    """Only absent or literal false is safe; expressions can resolve to true at runtime."""
    workflow = enforcing("lint")
    target = (
        workflow["jobs"]["lint"]
        if level == "job"
        else workflow["jobs"][AGGREGATE]["steps"][-1]
    )
    target["continue-on-error"] = "${{ true }}"

    assert any("continue-on-error" in problem for problem in weakened(workflow))


def test_a_condition_that_merely_contains_always_is_refused() -> None:
    """`always() && false` contains the substring and never runs the job."""
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["if"] = "${{ always() && false }}"

    assert any("not exactly" in problem for problem in weakened(workflow))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shell", "bash {0} || true"),
        ("working-directory", "forged-checkout"),
        ("env", {"BASH_ENV": "forged-checkout/neutralise.sh"}),
        ("if", "${{ false }}"),
    ],
)
def test_the_enforcer_step_has_one_trusted_execution_recipe(
    field: str, value: object
) -> None:
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"][-1][field] = value

    assert any("trusted execution recipe" in problem for problem in weakened(workflow))


def test_a_preceding_step_cannot_replace_the_enforcer() -> None:
    workflow = enforcing("lint")
    workflow["jobs"][AGGREGATE]["steps"].insert(
        -1,
        {"run": "printf 'raise SystemExit(0)' > scripts/assert_gates_passed.py"},
    )

    assert any("trusted execution recipe" in problem for problem in weakened(workflow))


@pytest.mark.parametrize(
    ("scope", "value"),
    [
        ("workflow_defaults", {"run": {"shell": "bash {0} || true"}}),
        ("workflow_env", {"BASH_ENV": "neutralise.sh"}),
        ("job_env", {"PATH": "forged-bin"}),
        ("runner", "self-hosted"),
        ("container", "forged-python:latest"),
    ],
)
def test_the_aggregate_execution_context_is_trusted(scope: str, value: object) -> None:
    workflow = enforcing("lint")
    aggregate = workflow["jobs"][AGGREGATE]
    if scope == "workflow_defaults":
        workflow["defaults"] = value
    elif scope == "workflow_env":
        workflow["env"] = value
    elif scope == "job_env":
        aggregate["env"] = value
    elif scope == "runner":
        aggregate["runs-on"] = value
    else:
        aggregate["container"] = value

    assert any("trusted execution context" in problem for problem in weakened(workflow))


def test_a_gate_step_condition_cannot_skip_its_work() -> None:
    workflow = enforcing("lint")
    workflow["jobs"]["lint"]["steps"] = [
        {"run": "python -m pytest", "if": "${{ false }}"}
    ]

    assert any("step condition" in problem for problem in weakened(workflow))
