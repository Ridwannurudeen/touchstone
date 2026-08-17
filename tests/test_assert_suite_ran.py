"""The gate that proves a suite ran needs its own gate.

`assert_suite_ran.py` exists because a green pytest exit code does not mean a test executed.
It is therefore the one script in CI whose failure would be invisible: if it accepted an empty
report, the local-chain E2E check would go green having proved nothing, and that check is the
only place the whole publish path runs against a real chain.

Three of the cases below are the inputs it used to accept — a summary that lies about its
own cases, arbitrary XML, and the right number of the wrong tests. Each was demonstrated
against the script before it was changed, so each is a regression test rather than a
hypothetical.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from assert_suite_ran import NotAReport, read_report  # noqa: E402
from assert_suite_ran import main  # noqa: E402

ONE = "t::one"
TWO = "t::two"
BOTH = ["--expect-name", ONE, "--expect-name", TWO]


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
    assert main([str(_report(tmp_path, PASSED_TWO)), *BOTH]) == 0


def test_an_empty_report_is_refused(tmp_path: Path) -> None:
    """The failure this script exists for: pytest exits 0 having run nothing."""
    empty = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" tests="0"/></testsuites>
"""
    assert main([str(_report(tmp_path, empty)), *BOTH]) == 1


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
    assert main([str(_report(tmp_path, skipped)), *BOTH]) == 1


@pytest.mark.parametrize("attribute", ["failures", "errors"])
def test_a_failing_report_is_refused(tmp_path: Path, attribute: str) -> None:
    body = PASSED_TWO.replace(f'{attribute}="0"', f'{attribute}="1"')
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


@pytest.mark.parametrize("element", ["failure", "error"])
def test_a_case_that_failed_under_a_summary_claiming_none_is_refused(
    tmp_path: Path, element: str
) -> None:
    """The summary and the cases disagreed, and the summary was believed.

    pytest never writes this, but the script's whole job is to be the thing that cannot be
    fooled by a report, so a report it accepted while a case inside it carried a `<failure>`
    was a fail-open hole regardless of who could produce one.
    """
    body = PASSED_TWO.replace(
        '<testcase classname="t" name="two" time="0.1"/>',
        f'<testcase classname="t" name="two"><{element} message="boom">boom'
        f"</{element}></testcase>",
    )
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_xml_that_is_not_a_report_is_refused(tmp_path: Path) -> None:
    """It parsed, it had two `<testcase>` elements, and it was not a JUnit report at all."""
    body = """<?xml version="1.0" encoding="utf-8"?>
<arbitrary>
  <testcase classname="t" name="one"/>
  <testcase classname="t" name="two"/>
</arbitrary>
"""
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_valid_report_buried_in_a_foreign_root_is_refused(tmp_path: Path) -> None:
    """The root check earns its place here, and only here.

    The mutation harness caught this: with the structural checks added, removing the
    root-element check no longer changed the verdict on arbitrary XML, because a foreign root
    holding loose cases is refused for containing no `<testsuite>`. A registered fix that no
    longer decides anything is a finding, so this is the input that isolates it — a perfectly
    well-formed suite, internally consistent, wrapped in a root that is not a JUnit report.
    Every other check passes it; only the root check refuses it.
    """
    body = """<?xml version="1.0" encoding="utf-8"?>
<arbitrary>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="2">
    <testcase classname="t" name="one"/>
    <testcase classname="t" name="two"/>
  </testsuite>
</arbitrary>
"""
    with pytest.raises(NotAReport, match="root element"):
        read_report(_report(tmp_path, body))
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_the_right_number_of_the_wrong_tests_is_refused(tmp_path: Path) -> None:
    """Counting two cases was satisfied by any two cases, which is what naming them fixes."""
    body = PASSED_TWO.replace('name="two"', 'name="something_else_entirely"')
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_one_case_reported_twice_is_refused(tmp_path: Path) -> None:
    """Same names, wrong multiplicity: the run still was not the one that was asked for."""
    body = PASSED_TWO.replace('name="two"', 'name="one"')
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_case_in_a_different_module_is_refused(tmp_path: Path) -> None:
    """The classname is half the identity; a same-named test elsewhere is a different test."""
    body = PASSED_TWO.replace(
        'classname="t" name="two"', 'classname="other" name="two"'
    )
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_the_wrapper_and_bare_shapes_are_both_read(tmp_path: Path) -> None:
    """pytest emits a <testsuites> wrapper; the JUnit format also allows a bare <testsuite>.

    Reading counts off the root's own attributes worked on the bare shape and reported zero
    on the wrapper, which is the direction that fails open.
    """
    bare = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" errors="0" failures="2" skipped="0" tests="2">
  <testcase classname="t" name="one"><failure message="a">a</failure></testcase>
  <testcase classname="t" name="two"><failure message="b">b</failure></testcase>
</testsuite>
"""
    read = read_report(_report(tmp_path, bare))
    assert (read.cases, read.skipped, read.failures, read.errors) == (2, 0, 2, 0)
    wrapped = read_report(_report(tmp_path, PASSED_TWO))
    assert (wrapped.cases, wrapped.skipped, wrapped.failures, wrapped.errors) == (
        2,
        0,
        0,
        0,
    )
    assert wrapped.identities == (ONE, TWO)


@pytest.mark.parametrize("tally", ["tests", "skipped", "failures", "errors"])
def test_a_summary_that_disagrees_with_its_own_cases_is_refused(
    tmp_path: Path, tally: str
) -> None:
    """A count in the header that no case supports means something else wrote this file.

    An earlier version reconciled the two sources with max(), which stops a green result
    whenever either side reports a problem but accepts the incoherent report as coherent on
    the way. The clearest example is a summary claiming a skip that no case carries: nothing
    was skipped by the cases, so nothing refused it, and the run passed.
    """
    body = PASSED_TWO.replace(f'{tally}="0"', f'{tally}="1"').replace(
        'tests="2"', 'tests="2"' if tally != "tests" else 'tests="3"'
    )
    with pytest.raises(NotAReport, match="disagrees"):
        read_report(_report(tmp_path, body))
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_negative_count_is_refused(tmp_path: Path) -> None:
    """It is not a report about anything, and under max() it cancelled a real failure."""
    body = PASSED_TWO.replace('failures="0"', 'failures="-1"')
    with pytest.raises(NotAReport, match="negative"):
        read_report(_report(tmp_path, body))
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_wrapper_holding_no_suite_is_refused(tmp_path: Path) -> None:
    """Cases directly under <testsuites> were adopted as a clean run of exactly those cases."""
    body = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testcase classname="t" name="one"/>
  <testcase classname="t" name="two"/>
</testsuites>
"""
    with pytest.raises(NotAReport, match="no <testsuite>"):
        read_report(_report(tmp_path, body))
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_case_outside_any_suite_is_refused(tmp_path: Path) -> None:
    """A stray case beside a well-formed suite is counted as the anomaly it is."""
    body = PASSED_TWO.replace(
        "</testsuite>", '</testsuite>\n  <testcase classname="t" name="stray"/>'
    )
    with pytest.raises(NotAReport, match="outside any"):
        read_report(_report(tmp_path, body))
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_count_that_is_not_a_number_is_refused(tmp_path: Path) -> None:
    body = PASSED_TWO.replace('failures="0"', 'failures="lots"')
    with pytest.raises(NotAReport):
        read_report(_report(tmp_path, body))
    assert main([str(_report(tmp_path, body)), *BOTH]) == 1


def test_a_missing_report_is_refused(tmp_path: Path) -> None:
    """pytest writing no report at all must not read as success."""
    assert main([str(tmp_path / "absent.xml"), *BOTH]) == 1


def test_unreadable_xml_is_refused(tmp_path: Path) -> None:
    assert main([str(_report(tmp_path, "not xml at all")), *BOTH]) == 1


def test_naming_nothing_is_refused(tmp_path: Path) -> None:
    """`--expect-name` is required: a call that names no test asserts nothing."""
    with pytest.raises(SystemExit) as raised:
        main([str(_report(tmp_path, PASSED_TWO))])
    assert raised.value.code == 2
