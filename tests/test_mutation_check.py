"""The harness that proves the other tests is itself a claim, so it is tested too.

Its whole job is to answer "did an assertion notice this?", and its first two versions
answered a different question: the first read any nonzero pytest exit as a kill, the second
read exit 1 as a kill. Both credited infrastructure failures — a mistyped node, an
unwritable temporary directory, a plugin that dies during initialisation — to assertions
that never ran. A verification instrument that over-reports is worse than none, because
everything downstream of it is stated with more confidence than it was earned.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from mutation_check import (  # noqa: E402
    MUTATIONS,
    ROOT,
    reported_failures,
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


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_failure_in_a_targeted_test_is_evidence(tmp_path: Path) -> None:
    failures = reported_failures(
        write(tmp_path, REPORT), ("tests/test_signing.py::test_wanted",)
    )

    assert failures == ["test_wanted"]


def test_a_run_that_recorded_no_report_is_not_evidence(tmp_path: Path) -> None:
    """Pytest can exit nonzero without collecting anything, and writes no report then.

    That is the case the classifier used to read as a kill: the mutation was credited with
    a failure that no assertion ever made.
    """
    assert (
        reported_failures(tmp_path / "absent.xml", ("tests/test_signing.py::test_x",))
        is None
    )


def test_a_failure_outside_the_target_set_is_not_evidence(tmp_path: Path) -> None:
    """A conftest error fails the run without any targeted test having judged anything."""
    assert (
        reported_failures(
            write(tmp_path, UNRELATED), ("tests/test_signing.py::test_wanted",)
        )
        is None
    )


def test_a_parametrised_failure_is_matched_to_its_function() -> None:
    assert ("tests.test_signing", "test_wanted") in wanted_nodes(
        ("tests/test_signing.py::test_wanted",)
    )


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
