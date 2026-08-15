"""Prove each regression fails when the fix it guards is removed.

A green suite says the code passes its tests. It does not say the tests would notice if
the code stopped being correct, and an assertion that holds either way is worse than an
absent one: it reports coverage it does not provide. Every claim in this repository that a
defect is "closed and regression-tested" rests on that difference, so the difference is
checked here rather than asserted in a commit message.

Each entry below removes exactly one shipped fix and names the tests that exist because of
it. Those tests must fail. A surviving mutant is a finding: either the fix is unnecessary
or the regression is vacuous, and both are worth knowing before an auditor finds out.

Run from the repository root with a clean tree:

    python scripts/mutation_check.py

The tree is verified clean before anything is written and verified clean again afterwards,
because this edits tracked source files in place and a crash mid-run must not be mistaken
for authored work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.etree import ElementTree


ROOT = Path(__file__).parents[1]


@dataclass(frozen=True)
class Mutation:
    """One shipped fix, removed, and the tests that must notice."""

    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


MUTATIONS = (
    Mutation(
        name="bundle-controls-read-twice",
        path="touchstone/verify.py",
        old="    records = tuple(control_records)",
        new="    records = control_records",
        tests=(
            "tests/test_verify.py::test_a_bundle_holds_every_control_of_a_single_pass_sequence",
        ),
    ),
    Mutation(
        name="verified-bundle-aliases-its-caller",
        path="touchstone/verify.py",
        old='            else frozen_snapshot(value, "bundle")',
        new="            else value",
        tests=(
            "tests/test_verify.py::test_a_verified_report_does_not_change_when_the_caller_mutates_the_bundle",
        ),
    ),
    Mutation(
        name="verified-report-aliases-its-caller",
        path="touchstone/signing.py",
        old='        else frozen_snapshot(envelope, "signature envelope")',
        new="        else envelope",
        tests=(
            "tests/test_signing.py::test_verified_report_does_not_change_when_the_caller_mutates_the_envelope",
        ),
    ),
    Mutation(
        name="key-records-resolved-from-the-caller",
        path="touchstone/signing.py",
        old='    key_records = frozen_snapshot(key_records, "key_records")\n',
        new="",
        tests=(
            "tests/test_signing.py::test_verification_resolves_the_key_record_from_its_own_snapshot",
        ),
    ),
    Mutation(
        name="reference-cycle-escapes-as-recursion-error",
        path="touchstone/signing.py",
        old='        raise ValueError(f"{field} contains a reference cycle") from error',
        new="        raise",
        tests=(
            "tests/test_signing.py::test_frozen_snapshot_refuses_a_self_referential_mapping",
            "tests/test_signing.py::test_frozen_snapshot_refuses_a_cycle_reached_through_a_sequence",
        ),
    ),
    Mutation(
        name="observations-read-once-per-control",
        path="touchstone/evaluate.py",
        old="    observed = dict(observations)",
        new="    observed = observations",
        tests=(
            "tests/test_evaluate.py::test_one_report_describes_one_set_of_observations",
        ),
    ),
    Mutation(
        name="prior-observations-read-once-per-control",
        path="touchstone/evaluate.py",
        old="    prior = dict(prior_observations)",
        new="    prior = prior_observations",
        tests=(
            "tests/test_evaluate.py::test_one_report_describes_one_set_of_prior_observations",
        ),
    ),
    Mutation(
        name="startup-leaks-workspace-io-failure",
        path="scripts/run_service.py",
        old="    except (DeploymentError, IdentityError, OSError, ValueError) as error:",
        new="    except (DeploymentError, IdentityError, ValueError) as error:",
        tests=(
            "tests/test_service_startup.py::test_an_unusable_workspace_fails_the_service_rather_than_crashing_it",
        ),
    ),
    Mutation(
        name="service-lock-follows-the-process",
        path="scripts/run_service.py",
        old='            else Path(operations.directory).parent / "service.lock"\n        ).resolve()',
        new='            else Path(operations.directory).parent / "service.lock"\n        )',
        tests=(
            "tests/test_workspace.py::test_a_relative_path_holder_operates_where_it_was_created",
        ),
    ),
    Mutation(
        name="fixture-transport-follows-the-process",
        path="touchstone/epoch.py",
        old="        self.fixtures_dir = Path(fixtures_dir).resolve()",
        new="        self.fixtures_dir = Path(fixtures_dir)",
        tests=(
            "tests/test_workspace.py::test_a_relative_path_holder_operates_where_it_was_created",
        ),
    ),
    Mutation(
        name="oracle-tolerance-may-be-unbounded",
        path="touchstone/oracles.py",
        old="    if not isinstance(tolerance, Decimal) or not tolerance.is_finite():",
        new="    if not isinstance(tolerance, Decimal) or False:",
        tests=(
            "tests/test_oracles.py::test_a_tolerance_that_is_not_a_number_is_refused",
        ),
    ),
    Mutation(
        name="oracle-block-number-unchecked",
        path="touchstone/oracles.py",
        old="    elif type(block_number) is not int or block_number < 0:",
        new="    elif False:",
        tests=(
            "tests/test_oracles.py::test_a_block_that_is_not_a_block_number_is_refused",
        ),
    ),
    Mutation(
        name="oracle-round-data-checked-by-length-only",
        path="touchstone/oracles.py",
        old='        or len(answer_raw) != 2 + 64 * 5\n        or any(\n            character not in "0123456789abcdefABCDEF" for character in answer_raw[2:]\n        )',
        new="        or len(answer_raw) < 2 + 64 * 5",
        tests=(
            "tests/test_oracles.py::test_malformed_round_data_is_this_modules_failure",
        ),
    ),
    Mutation(
        name="oracle-timestamp-overflow-escapes-untyped",
        path="touchstone/oracles.py",
        # The normalisation, not the caught tuple: which member fires is platform
        # dependent — this host raises OSError where others raise OverflowError — so
        # narrowing the tuple is a mutation that only bites on some machines.
        old='        raise OracleUnavailable(\n            f"oracle round timestamp {updated_at_word} is not a representable instant"\n        ) from error',
        new="        raise",
        tests=(
            "tests/test_oracles.py::test_an_unrepresentable_round_timestamp_is_this_modules_failure",
        ),
    ),
    Mutation(
        name="an-offsetless-instant-is-accepted",
        path="touchstone/quantities.py",
        old='        raise ValueError(f"{field} must be timezone-aware")',
        new="        offset = timedelta(0)",
        tests=(
            "tests/test_quantities.py::test_only_an_aware_datetime_is_an_instant",
            "tests/test_incidents.py::test_an_instant_must_be_timezone_aware",
            "tests/test_operations.py::test_an_instant_must_be_timezone_aware",
            "tests/test_keyring.py::test_a_lifecycle_instant_must_be_timezone_aware",
            "tests/test_compiler.py::test_a_retrieval_instant_must_be_timezone_aware",
        ),
    ),
    Mutation(
        name="instant-resolved-from-a-second-offset-read",
        path="touchstone/quantities.py",
        old="        return value.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)",
        new="        return value.astimezone(timezone.utc)",
        tests=(
            "tests/test_quantities.py::test_an_instant_is_resolved_from_the_offset_that_was_validated",
        ),
    ),
    Mutation(
        name="a-zone-that-raises-escapes-untyped",
        path="touchstone/quantities.py",
        old='    except Exception as error:\n        raise ValueError(f"{field} could not report a UTC offset',
        new='    except ValueError as error:\n        raise ValueError(f"{field} could not report a UTC offset',
        tests=(
            "tests/test_quantities.py::test_a_zone_that_refuses_to_answer_is_this_modules_refusal",
        ),
    ),
    Mutation(
        name="an-unconvertible-instant-escapes-untyped",
        path="touchstone/quantities.py",
        old='    except (OSError, OverflowError, ValueError) as error:\n        raise ValueError(f"{field} cannot be converted to UTC',
        new='    except OSError as error:\n        raise ValueError(f"{field} cannot be converted to UTC',
        tests=(
            "tests/test_quantities.py::test_an_instant_that_cannot_be_converted_is_this_modules_refusal",
        ),
    ),
    Mutation(
        name="identity-not-established-at-each-observation",
        path="touchstone/incidents.py",
        old="        self._refuse_hardlink()\n        entries = self._read()",
        new="        entries = self._read()",
        tests=(
            "tests/test_incidents.py::test_a_log_that_stops_being_identifiable_is_refused_when_it_is_read",
            "tests/test_incidents.py::test_identity_is_only_ever_checked_inside_the_one_observation",
        ),
    ),
    Mutation(
        name="pending-journal-follows-the-process",
        path="touchstone/publish.py",
        old="        self.pending_path = Path(pending_path).resolve()",
        new="        self.pending_path = Path(pending_path)",
        tests=(
            "tests/test_workspace.py::test_a_relative_path_holder_operates_where_it_was_created",
        ),
    ),
)


# pytest's documented exit codes. Only TESTS_FAILED means a mutant was noticed by an
# assertion. Every other nonzero code means the run did not happen as intended — a
# mistyped node id collects nothing and exits USAGE_ERROR — and counting those as kills
# is how a harness reports full coverage while executing none of it.
_TESTS_FAILED = 1
_NO_TESTS_COLLECTED = 5


def tree_is_clean() -> tuple[bool, str]:
    """Report whether the tree matches HEAD, including the index and untracked files.

    `git diff` alone sees neither, so a staged source change or an untracked `conftest.py`
    could shape every result while the harness announced a clean tree.
    """
    finished = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        return False, finished.stderr.strip()
    return not finished.stdout.strip(), finished.stdout.strip()


def run_tests(
    tests: tuple[str, ...], report_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run exactly these test nodes, capturing everything."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        *tests,
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    if report_path is not None:
        command.append(f"--junit-xml={report_path}")
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def wanted_nodes(tests: tuple[str, ...]) -> set[tuple[str, str]]:
    """Map requested node ids to the (module, function) pairs pytest reports them as."""
    wanted = set()
    for test in tests:
        path, _, function = test.partition("::")
        wanted.add((path.removesuffix(".py").replace("/", "."), function))
    return wanted


def reported_failures(report_path: Path, tests: tuple[str, ...]) -> list[str] | None:
    """Return the requested tests pytest recorded as failed, or None if it recorded none.

    An exit code says a run ended badly; it does not say a test noticed anything. Pytest
    can exit 1 without collecting a single node — an unwritable temporary directory, a
    plugin that fails during initialisation — and reading that as a kill credits the
    mutation with a failure no assertion ever made. Only a structured report naming a node
    from *this* mutation's target set is evidence.
    """
    if not report_path.exists():
        return None
    wanted = wanted_nodes(tests)
    failures = []
    for case in ElementTree.parse(report_path).iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        module = case.get("classname", "")
        function = case.get("name", "").partition("[")[0]
        if (module, function) in wanted:
            failures.append(case.get("name", function))
    return failures or None


def read_exactly(path: Path) -> str:
    """Read without translating line endings, so what is written back is what was read."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_exactly(path: Path, text: str) -> None:
    """Write without translating line endings."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def run_mutation(mutation: Mutation) -> tuple[str, str]:
    """Apply one mutation, judge the run, and always restore.

    Returns a verdict of "killed", "survived" or "broken", with the detail worth printing.
    """
    target = ROOT / mutation.path
    original = read_exactly(target)
    # Anchors are written with newlines, because that is how source is quoted. The file on
    # disk may not use them: `write_text` translating a restored `\n` back to `\r\n` left
    # the file byte-different from the one that was read, which the restore check then
    # correctly reported as work this harness had failed to put back.
    ending = "\r\n" if "\r\n" in original else "\n"
    old = mutation.old.replace("\n", ending)
    new = mutation.new.replace("\n", ending)
    occurrences = original.count(old)
    if occurrences != 1:
        return (
            "broken",
            f"found {occurrences} matches in {mutation.path}, expected exactly one — "
            "the harness anchor is stale",
        )
    with tempfile.TemporaryDirectory() as workspace:
        # Outside the repository, so the report cannot become an untracked file that the
        # clean-tree check then reports as unrestored work.
        report_path = Path(workspace) / "report.xml"
        try:
            write_exactly(target, original.replace(old, new))
            finished = run_tests(mutation.tests, report_path)
        finally:
            write_exactly(target, original)
        failures = reported_failures(report_path, mutation.tests)

    if finished.returncode == 0:
        return "survived", "every named test still passed"
    if finished.returncode == _TESTS_FAILED and failures is not None:
        return "killed", "; ".join(failures[:2])
    if finished.returncode == _TESTS_FAILED:
        reason = "pytest exited 1 without recording a failure in any targeted test"
    elif finished.returncode == _NO_TESTS_COLLECTED:
        reason = "no tests were collected"
    else:
        reason = f"pytest exited {finished.returncode}"
    return "broken", f"{reason}\n{_diagnostic(finished)}"


def _diagnostic(finished: subprocess.CompletedProcess[str]) -> str:
    """Both streams. An initialisation failure explains itself only on stderr."""
    return "\n".join(
        part
        for part in (finished.stdout.strip()[-800:], finished.stderr.strip()[-800:])
        if part
    )


def main() -> int:
    clean, detail = tree_is_clean()
    if not clean:
        print(
            "refusing to run: the tree does not match HEAD, and this harness edits "
            f"tracked files in place\n{detail}",
            file=sys.stderr,
        )
        return 2

    # Every mutation's tests must pass before anything is mutated. Otherwise a node that
    # was renamed, or a test already failing for an unrelated reason, is indistinguishable
    # from a mutant that was caught.
    baseline = run_tests(tuple(dict.fromkeys(t for m in MUTATIONS for t in m.tests)))
    if baseline.returncode != 0:
        print(
            "refusing to run: the unmutated tests do not all pass, so no result would "
            f"mean anything\n{_diagnostic(baseline)}",
            file=sys.stderr,
        )
        return 2

    survivors, broken = [], []
    for mutation in MUTATIONS:
        verdict, detail = run_mutation(mutation)
        print(f"{verdict:9} {mutation.name}" + (f"  ({detail})" if detail else ""))
        if verdict == "survived":
            survivors.append(mutation.name)
        elif verdict == "broken":
            broken.append(mutation.name)

    clean, detail = tree_is_clean()
    if not clean:
        print(
            f"the tree was not restored — inspect it before committing\n{detail}",
            file=sys.stderr,
        )
        return 2

    killed = len(MUTATIONS) - len(survivors) - len(broken)
    print(f"\n{killed}/{len(MUTATIONS)} mutants killed")
    for name in survivors:
        print(f"  survived: {name}", file=sys.stderr)
    for name in broken:
        print(f"  broken:   {name}", file=sys.stderr)
    return 1 if survivors or broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
