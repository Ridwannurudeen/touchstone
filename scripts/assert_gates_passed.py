"""Fail unless every gate result handed to this script is `success`.

The aggregate CI check used to enforce this in inline shell, and inline shell is exactly what
a static checker cannot verify. `assert_ci_gates.py` could confirm the step *mentioned*
`needs.*.result` and not that it did anything with them — so a step that read the results and
echoed them passed the checker while enforcing nothing.

Moving the decision here makes it testable, which is the point: the tests below this script
prove it refuses, and the workflow checker only has to prove this script is the thing being
run. A property that is proved once and then referenced beats a property re-asserted in prose
beside every copy of the logic.

`skipped` and `cancelled` are refusals, not absences. A gate that did not run has not passed,
and treating "no result" as "no problem" is the failure this whole arrangement exists to
prevent.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

PASSING = "success"


def problems(results: Sequence[str]) -> list[str]:
    """Return every reason these results must not be accepted."""
    if not results:
        # `join(needs.*.result, ' ')` expands to nothing when the aggregate waits for no
        # job at all, and an empty string is the most dangerous input here: it looks like
        # "nothing went wrong" and means "nothing was checked".
        return ["no gate results were given, so nothing was checked"]
    return [
        f"gate {position} reported {result!r}, not {PASSING!r}"
        for position, result in enumerate(results, start=1)
        if result != PASSING
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "results",
        nargs="*",
        help="the gate results, as `join(needs.*.result, ' ')` produces them. A single "
        "space-separated argument and several arguments are both accepted, because the "
        "shell may split the expansion either way.",
    )
    arguments = parser.parse_args(argv)

    results = [result for argument in arguments.results for result in argument.split()]
    found = problems(results)
    if found:
        print(f"gate results: {' '.join(results) if results else '(none)'}")
        for problem in found:
            print(f"  - {problem}")
        return 1

    print(f"all {len(results)} gates reported {PASSING}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
