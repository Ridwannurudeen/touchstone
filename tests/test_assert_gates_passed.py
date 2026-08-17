"""The aggregate CI check's actual decision, tested rather than asserted.

This logic used to be inline shell in the workflow, where no checker could reach it: the
workflow checker could confirm the step *mentioned* `needs.*.result` and not that it did
anything with them, so a step that read the results and echoed them passed while enforcing
nothing. Moving the decision into a script is what makes it provable, and these are the
proofs the workflow checker then gets to reference instead of re-asserting.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from assert_gates_passed import main, problems  # noqa: E402


def test_every_gate_green_is_accepted() -> None:
    assert main(["success success success"]) == 0


def test_the_results_may_arrive_already_split() -> None:
    """The shell may split the expansion, or hand it over as one argument."""
    assert main(["success", "success"]) == 0
    assert problems(["success", "success"]) == []


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral"])
def test_anything_other_than_success_is_refused(result: str) -> None:
    """`skipped` and `cancelled` included: a gate that did not run has not passed.

    This is the whole reason the aggregate exists. `needs` alone does not fail a job when a
    dependency is skipped or cancelled, so if those read as acceptable here the aggregate
    would add nothing at all.

    An *empty* result is deliberately not in this list, and the reason is worth stating: it
    cannot be seen from here. `join` would render it as a second space and the shell collapses
    runs of whitespace, so the gap closes before this script is reached. What guards that is
    `assert_ci_gates.py` proving every job is in `needs` in the first place — the count is
    established there, not recovered here.
    """
    assert main([f"success {result} success"]) == 1


def test_no_results_at_all_is_refused() -> None:
    """The most dangerous input: it looks like nothing went wrong and means nothing ran.

    `join(needs.*.result, ' ')` expands to an empty string when the aggregate waits for no
    job, which is exactly the state a mistaken edit to `needs` produces.
    """
    assert main([]) == 1
    assert main([""]) == 1
    assert main(["   "]) == 1
    assert problems([]) == ["no gate results were given, so nothing was checked"]


def test_the_refusal_names_which_gate_failed() -> None:
    """An operator reading the log should not have to count the results themselves."""
    found = problems(["success", "failure", "success"])
    assert len(found) == 1
    assert "gate 2" in found[0] and "failure" in found[0]


def test_a_case_difference_is_not_success() -> None:
    """GitHub writes `success` lowercase; anything else is not the value being checked."""
    assert main(["Success"]) == 1
