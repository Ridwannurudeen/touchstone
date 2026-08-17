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

Five ways this script itself used to fail open, all now closed and all now tested:

* It trusted `<testsuite failures="...">` while counting the cases themselves, so a report
  whose summary said zero and whose cases carried `<failure>` was accepted.
* It never checked the root element, so any XML at all containing `<testcase>` tags counted.
* It asserted a number of cases rather than which cases, so the right count of the wrong
  tests passed.
* It ignored disabled totals and testcase `status="notrun"`, so expected names counted even
  when the report said none of them ran.
* It treated any descendant with a familiar tag as structurally valid, so suites and cases
  buried under foreign elements counted, while failure elements outside their required
  direct testcase position disappeared.
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

    disabled: int
    skipped: int
    failures: int
    errors: int
    identities: tuple[str, ...]

    @property
    def cases(self) -> int:
        return len(self.identities)


# The five totals a JUnit suite declares about itself, each of which the cases below it also
# answer. They must agree, so both answers are read and compared rather than combined.
TALLIES = ("tests", "disabled", "skipped", "failures", "errors")


def _count(suite: ElementTree.Element, attribute: str) -> int:
    raw = suite.get(attribute, "0")
    try:
        count = int(raw)
    except ValueError as error:
        raise NotAReport(f"<testsuite {attribute}={raw!r}> is not a count") from error
    # A negative total is not a report about anything, and it used to cancel a real one:
    # combining the two sources with max() meant failures="-1" beside a genuine failure
    # still produced zero.
    if count < 0:
        raise NotAReport(f"<testsuite {attribute}={raw!r}> is negative")
    return count


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
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        raise NotAReport(f"<{root.tag}> contains no <testsuite>")

    # Cases are collected through the suites, not from the root, so that a <testcase> placed
    # outside any suite is counted as the anomaly it is rather than silently adopted. A
    # wrapper holding bare cases and no suite was accepted as a clean two-case run.
    cases = [case for suite in suites for case in suite.findall("testcase")]
    stray = len(list(root.iter("testcase"))) - len(cases)
    if stray:
        raise NotAReport(
            f"{stray} <testcase> element(s) are not a direct child of a <testsuite>"
        )

    # Both sources are read and required to agree, rather than combined. An earlier version
    # took max(), which stops a green result whenever either side reports a problem but
    # accepts an incoherent report as coherent on the way — and a summary that disagrees
    # with its own cases is not a report this script should be interpreting at all. pytest
    # emits internally consistent counts, so disagreement means something else wrote this.
    summary = {
        tally: sum(_count(suite, tally) for suite in suites) for tally in TALLIES
    }
    observed = {
        "tests": len(cases),
        "disabled": sum(case.get("status") == "notrun" for case in cases),
        "skipped": sum(len(case.findall("skipped")) for case in cases),
        "failures": sum(len(case.findall("failure")) for case in cases),
        "errors": sum(len(case.findall("error")) for case in cases),
    }
    misplaced_outcomes = [
        outcome
        for outcome, tally in (
            ("skipped", "skipped"),
            ("failure", "failures"),
            ("error", "errors"),
        )
        if len(list(root.iter(outcome))) != observed[tally]
    ]
    if misplaced_outcomes:
        raise NotAReport(
            "these outcomes are not direct children of test cases: "
            + ", ".join(misplaced_outcomes)
        )
    disagreements = [
        f"{tally}: the suites say {summary[tally]}, the cases show {observed[tally]}"
        for tally in TALLIES
        if summary[tally] != observed[tally]
    ]
    # The wrapper may declare its own totals, and reading only the children ignored them —
    # so a <testsuites> claiming a failure over child suites and cases that claim none was
    # accepted. pytest writes an *uncounted* wrapper, so this adds nothing on its output and
    # closes the gap on anything else's. These are totals across the suites, not further
    # counts to add, so they are compared rather than summed.
    if root.tag == "testsuites":
        disagreements.extend(
            f"{tally}: the <testsuites> wrapper says {_count(root, tally)}, "
            f"the cases show {observed[tally]}"
            for tally in TALLIES
            if root.get(tally) is not None and _count(root, tally) != observed[tally]
        )
    if disagreements:
        raise NotAReport(
            "the suite summary disagrees with its own cases — "
            + "; ".join(disagreements)
        )

    return Report(
        disabled=observed["disabled"],
        skipped=observed["skipped"],
        failures=observed["failures"],
        errors=observed["errors"],
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
    if report.disabled:
        problems.append(f"{report.disabled} test case(s) were disabled or not run")
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
