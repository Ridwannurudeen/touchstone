"""Prove a suite actually ran, rather than passing by finding nothing to do.

A green pytest exit code says no test failed. It does not say a test ran. A `pytest.skip` on
an unavailable dependency, a marker that stopped matching, a renamed file, a `-k` expression
that selects nothing — each of those produces exit code 0, and the check that depended on it
goes green while proving nothing.

That matters most for the one suite that cannot be replaced by reasoning: the managed
local-chain E2E is the only place the whole path from evidence to a published report and a
verifying bundle runs against a real chain. A silently skipped E2E is worse than an absent
one, because the absent one is visible.

So the count is asserted, not the exit code. Run pytest with `--junitxml` and hand the report
here.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from xml.etree import ElementTree


def counts(report: Path) -> tuple[int, int, int, int]:
    """Return (cases, skipped, failures, errors) across every suite in the report."""
    root = ElementTree.parse(report).getroot()
    # A JUnit report is either one <testsuite> or a <testsuites> wrapper around several, and
    # pytest emits the wrapper. Reading only the root's attributes therefore worked on one
    # shape and silently reported zero on the other.
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    skipped = sum(len(case.findall("skipped")) for case in cases)
    failures = sum(int(suite.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.get("errors", 0)) for suite in suites)
    return len(cases), skipped, failures, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="a pytest --junitxml report")
    parser.add_argument(
        "--expect",
        type=int,
        required=True,
        help="the exact number of test cases that must have run",
    )
    arguments = parser.parse_args(argv)

    if arguments.expect < 1:
        print("--expect must be at least 1: asserting zero tests ran proves nothing")
        return 2
    if not arguments.report.is_file():
        print(f"no JUnit report at {arguments.report}: pytest did not write one")
        return 1

    try:
        cases, skipped, failures, errors = counts(arguments.report)
    except ElementTree.ParseError as error:
        print(f"{arguments.report} is not readable XML: {error}")
        return 1

    problems: list[str] = []
    if cases != arguments.expect:
        problems.append(f"{cases} test cases ran, expected exactly {arguments.expect}")
    if skipped:
        problems.append(f"{skipped} test case(s) were skipped")
    if failures or errors:
        problems.append(f"{failures} failure(s) and {errors} error(s)")

    if problems:
        print(f"{arguments.report}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"{cases} test cases ran, none skipped, none failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
