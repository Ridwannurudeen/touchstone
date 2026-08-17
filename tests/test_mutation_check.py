"""The harness that proves the other tests is itself a claim, so it is tested too.

Its whole job is to answer "did a targeted test run and reject this?", and its first two
versions answered a different question: the first read any nonzero pytest exit as a kill,
the second read exit 1 as a kill. Both credited infrastructure failures — a mistyped node,
an unwritable temporary directory, a plugin that dies during initialisation — to tests that
never ran. A verification instrument that over-reports is worse than none, because
everything downstream of it is stated with more confidence than it was earned.
"""

from __future__ import annotations

from pathlib import Path
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from mutation_check import (  # noqa: E402
    EXPECTED_MUTATIONS,
    MUTATIONS,
    ROOT,
    classify,
    reported_outcomes,
    wanted_nodes,
)


REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" tests="2">
<testcase classname="tests.test_signing" name="test_wanted" time="0.01">
<failure message="assert 1 == 2">assert 1 == 2</failure></testcase>
<testcase classname="tests.test_signing" name="test_passing" time="0.01"/>
</testsuite></testsuites>
"""

UNRELATED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" tests="1">
<testcase classname="tests.test_elsewhere" name="test_other" time="0.01">
<error message="collection failure">conftest raised</error></testcase>
</testsuite></testsuites>
"""


TARGETED_ERROR = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" tests="1">
<testcase classname="tests.test_signing" name="test_wanted" time="0.01">
<error message="setup">temporary directory unavailable</error></testcase>
</testsuite></testsuites>
"""

PARAMETRISED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" tests="1">
<testcase classname="tests.test_signing" name="test_wanted[Infinity]" time="0.01">
<failure message="assert">assert</failure></testcase>
</testsuite></testsuites>
"""

WANTED = ("tests/test_signing.py::test_wanted",)


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_failure_in_a_targeted_test_is_evidence(tmp_path: Path) -> None:
    assert reported_outcomes(write(tmp_path, REPORT), WANTED) == (["test_wanted"], [])
    assert classify(1, (["test_wanted"], []), "") == ("killed", "test_wanted")


def test_a_run_that_recorded_no_report_is_not_evidence(tmp_path: Path) -> None:
    """Pytest can exit nonzero without collecting anything, and writes no report then."""
    assert reported_outcomes(tmp_path / "absent.xml", WANTED) is None

    verdict, detail = classify(1, None, "")
    assert verdict == "broken"
    assert "without writing a report" in detail


def test_a_failure_outside_the_target_set_is_not_evidence(tmp_path: Path) -> None:
    """A conftest error fails the run without any targeted test having judged anything."""
    assert reported_outcomes(write(tmp_path, UNRELATED), WANTED) == ([], [])

    verdict, detail = classify(1, ([], []), "")
    assert verdict == "broken"
    assert "without recording a failure" in detail


def test_a_targeted_setup_error_is_not_evidence(tmp_path: Path) -> None:
    """Pytest means different things by `<failure>` and `<error>`.

    `<error>` is setup or teardown, so the test body never ran: the fixture could not build
    a workspace, a temporary directory had gone. That is infrastructure wearing the
    target's name, and counting it is the exit-code mistake one level further in.
    """
    assert reported_outcomes(write(tmp_path, TARGETED_ERROR), WANTED) == (
        [],
        ["test_wanted"],
    )

    verdict, detail = classify(1, ([], ["test_wanted"]), "")
    assert verdict == "broken"
    assert "never ran" in detail


def test_a_parametrised_failure_is_matched_to_its_function(tmp_path: Path) -> None:
    """The report names `test_wanted[Infinity]`; the target names the function."""
    assert reported_outcomes(write(tmp_path, PARAMETRISED), WANTED) == (
        ["test_wanted[Infinity]"],
        [],
    )
    assert ("tests.test_signing", "test_wanted") in wanted_nodes(WANTED)


def test_a_passing_run_is_a_survivor() -> None:
    assert classify(0, ([], []), "")[0] == "survived"


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.name)
def test_every_mutation_anchor_still_matches_its_source_exactly_once(mutation) -> None:
    """A stale anchor silently stops testing the fix it was written for.

    Formatting moved two of these once already. Checking it here means the tree says so
    during an ordinary test run, rather than only when someone remembers to run the
    harness.
    """
    source = (ROOT / mutation.path).read_text(encoding="utf-8")

    assert source.count(mutation.old) == 1, (
        f"{mutation.name} no longer matches its source"
    )
    assert mutation.new != mutation.old


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.name)
def test_every_named_test_exists(mutation) -> None:
    """A renamed test would otherwise be reported as `broken` only when the harness runs."""
    for node in mutation.tests:
        path, _, function = node.partition("::")
        source = (ROOT / path).read_text(encoding="utf-8")
        assert f"def {function}(" in source, f"{node} is not a test that exists"


def test_no_tests_collected_is_broken() -> None:
    verdict, detail = classify(5, ([], []), "")
    assert verdict == "broken"
    assert "no tests were collected" in detail


def test_any_other_nonzero_exit_is_broken() -> None:
    """Exit 2 is an interrupted run and exit 3 is an internal error; neither judged."""
    for returncode in (2, 3, 4):
        verdict, detail = classify(returncode, ([], []), "")
        assert verdict == "broken"
        assert f"pytest exited {returncode}" in detail


def test_a_malformed_report_is_not_read_as_evidence(tmp_path: Path) -> None:
    """A truncated report is the shape an interrupted run leaves behind."""
    path = tmp_path / "report.xml"
    path.write_text("<testsuites><testsuite>", encoding="utf-8")

    with pytest.raises(ElementTree.ParseError):
        reported_outcomes(path, WANTED)


def test_the_registered_inventory_matches_what_is_expected() -> None:
    """The harness grades itself against its own list, so the list needs a witness.

    `killed/len(MUTATIONS)` is a ratio of the inventory to itself: empty it and the run
    prints "0/0 mutants killed" and exits 0. The job that proves the tests bite would then
    go green having proved nothing, which is precisely the fail-open `assert_suite_ran.py`
    exists to close one job over. Updating this number is a deliberate line in a diff.
    """
    assert len(MUTATIONS) == EXPECTED_MUTATIONS
