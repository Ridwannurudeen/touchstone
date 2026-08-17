"""Prove a suite actually ran the tests it was supposed to, rather than passing by finding
nothing to do.

A green pytest exit code says no test failed. It does not say a test ran. A `pytest.skip` on
an unavailable dependency, a marker that stopped matching, a renamed file, a `-k` expression
that selects nothing — each of those produces exit code 0, and the check that depended on it
goes green while proving nothing.

That matters most for the one suite that cannot be replaced by reasoning: the managed
local-chain E2E is the only place the whole path from evidence to a published report and a
verifying bundle runs against a real chain. A silently skipped E2E is worse than an absent
one, because the absent one is visible.

So the cases are named, not counted. An earlier version asserted only how many cases ran,
which two unrelated or duplicated cases satisfied just as well; naming them is the only form
of the question that cannot be answered by accident. Run pytest with `--junitxml` and hand
the report here.

Three ways this script itself used to fail open, all now closed and all now tested:

* It trusted `<testsuite failures="...">` while counting the cases themselves, so a report
  whose summary said zero and whose cases carried `<failure>` was accepted.
* It never checked the root element, so any XML at all containing `<testcase>` tags counted.
* It asserted a number of cases rather than which cases, so the right count of the wrong
  tests passed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from xml.etree import ElementTree

# The JUnit shapes pytest and the format itself permit. Anything else is not a report, and
# guessing at one is how arbitrary XML came to be accepted.
ROOT_TAGS = frozenset({"testsuite", "testsuites"})


class NotAReport(Exception):
    """The file parsed as XML but is not a JUnit report."""


@dataclass(frozen=True, slots=True)
class Report:
    """One reading of one report.

    Everything the caller needs comes from a single parse. Reading the file once for the
    counts and again for the identities would be two answers about two files whenever the
    second read saw something different, which is the defect class this repository keeps
    finding in its own modules.
    """

    skipped: int
    failures: int
    errors: int
    identities: tuple[str, ...]

    @property
    def cases(self) -> int:
        return len(self.identities)


def _count(suite: ElementTree.Element, attribute: str) -> int:
    raw = suite.get(attribute, "0")
    try:
        return int(raw)
    except ValueError as error:
        raise NotAReport(f"<testsuite {attribute}={raw!r}> is not a count") from error


def read_report(path: Path) -> Report:
    """Parse one JUnit report, or refuse it."""
    root = ElementTree.parse(path).getroot()
    if root.tag not in ROOT_TAGS:
        raise NotAReport(
            f"root element is <{root.tag}>, not one of "
            f"{' or '.join(f'<{tag}>' for tag in sorted(ROOT_TAGS))}"
        )
    # A JUnit report is either one <testsuite> or a <testsuites> wrapper around several, and
    # pytest emits the wrapper. Reading only the root's attributes therefore worked on one
    # shape and silently reported zero on the other.
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))

    # Both directions are read, and the larger wins, because the two disagree in two
    # different real ways. A summary saying zero over cases that carry `<failure>` is the
    # report that made this script accept a failing run; a suite-level error that produced
    # no case element at all is a collection that died before any test existed. Trusting
    # either source alone leaves one of those invisible.
    failures = max(
        sum(len(case.findall("failure")) for case in cases),
        sum(_count(suite, "failures") for suite in suites),
    )
    errors = max(
        sum(len(case.findall("error")) for case in cases),
        sum(_count(suite, "errors") for suite in suites),
    )
    return Report(
        skipped=sum(len(case.findall("skipped")) for case in cases),
        failures=failures,
        errors=errors,
        identities=tuple(
            f"{case.get('classname', '')}::{case.get('name', '')}" for case in cases
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="a pytest --junitxml report")
    parser.add_argument(
        "--expect-name",
        action="append",
        required=True,
        metavar="CLASSNAME::NAME",
        dest="expected",
        help="a test case that must have run, as it appears in the report. Repeat for "
        "each one; the report must contain exactly these and nothing else.",
    )
    arguments = parser.parse_args(argv)

    if not arguments.report.is_file():
        print(f"no JUnit report at {arguments.report}: pytest did not write one")
        return 1

    try:
        report = read_report(arguments.report)
    except ElementTree.ParseError as error:
        print(f"{arguments.report} is not readable XML: {error}")
        return 1
    except NotAReport as error:
        print(f"{arguments.report} is not a JUnit report: {error}")
        return 1

    problems: list[str] = []
    expected = sorted(arguments.expected)
    found = sorted(report.identities)
    if found != expected:
        missing = [name for name in expected if name not in found]
        unexpected = [name for name in found if name not in expected]
        if missing:
            problems.append(f"these cases did not run: {', '.join(missing)}")
        if unexpected:
            problems.append(
                f"these cases ran and were not expected: {', '.join(unexpected)}"
            )
        if not missing and not unexpected:
            # Same names, different multiplicity: a case reported twice still means the run
            # was not the one that was asked for.
            problems.append(f"expected {len(expected)} cases, {len(found)} ran")
    if report.skipped:
        problems.append(f"{report.skipped} test case(s) were skipped")
    if report.failures or report.errors:
        problems.append(f"{report.failures} failure(s) and {report.errors} error(s)")

    if problems:
        print(f"{arguments.report}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"{report.cases} named test cases ran, none skipped, none failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
