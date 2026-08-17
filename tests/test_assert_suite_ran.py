"""The gate that proves a suite ran needs its own gate.

`assert_suite_ran.py` exists because a green pytest exit code does not mean a test executed.
It is therefore the one script in CI whose failure would be invisible: if it accepted an empty
report, the local-chain E2E check would go green having proved nothing, and that check is the
only place the whole publish path runs against a real chain.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from assert_suite_ran import counts, main  # noqa: E402


def _report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(body, encoding="utf-8")
    return path


PASSED_TWO = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="2">
    <testcase classname="t" name="one" time="0.1"/>
    <testcase classname="t" name="two" time="0.1"/>
  </testsuite>
</testsuites>
"""


def test_a_run_that_did_what_it_claimed_is_accepted(tmp_path: Path) -> None:
    assert main([str(_report(tmp_path, PASSED_TWO)), "--expect", "2"]) == 0


def test_an_empty_report_is_refused(tmp_path: Path) -> None:
    """The failure this script exists for: pytest exits 0 having run nothing."""
    empty = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" tests="0"/></testsuites>
"""
    assert main([str(_report(tmp_path, empty)), "--expect", "2"]) == 1


def test_a_skipped_case_is_refused(tmp_path: Path) -> None:
    """A skip is the specific way this suite would go quiet: no node, no marker, no run."""
    skipped = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="1" tests="2">
    <testcase classname="t" name="one" time="0.1"/>
    <testcase classname="t" name="two"><skipped message="no hardhat"/></testcase>
  </testsuite>
</testsuites>
"""
    assert main([str(_report(tmp_path, skipped)), "--expect", "2"]) == 1


@pytest.mark.parametrize("attribute", ["failures", "errors"])
def test_a_failing_report_is_refused(tmp_path: Path, attribute: str) -> None:
    body = PASSED_TWO.replace(f'{attribute}="0"', f'{attribute}="1"')
    assert main([str(_report(tmp_path, body)), "--expect", "2"]) == 1


def test_the_wrapper_and_bare_shapes_are_both_counted(tmp_path: Path) -> None:
    """pytest emits a <testsuites> wrapper; the JUnit format also allows a bare <testsuite>.

    Reading failure counts off the root's own attributes worked on the bare shape and reported
    zero on the wrapper, which is the direction that fails open.
    """
    bare = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" errors="0" failures="2" skipped="0" tests="2">
  <testcase classname="t" name="one"/>
  <testcase classname="t" name="two"/>
</testsuite>
"""
    assert counts(_report(tmp_path, bare)) == (2, 0, 2, 0)
    assert counts(_report(tmp_path, PASSED_TWO)) == (2, 0, 0, 0)


def test_a_missing_report_is_refused(tmp_path: Path) -> None:
    """pytest writing no report at all must not read as success."""
    assert main([str(tmp_path / "absent.xml"), "--expect", "2"]) == 1


def test_unreadable_xml_is_refused(tmp_path: Path) -> None:
    assert main([str(_report(tmp_path, "not xml at all")), "--expect", "2"]) == 1


def test_expecting_nothing_is_refused(tmp_path: Path) -> None:
    """`--expect 0` would assert that nothing ran, which is what this script prevents."""
    assert main([str(_report(tmp_path, PASSED_TWO)), "--expect", "0"]) == 2
