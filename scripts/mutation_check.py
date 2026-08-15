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
        name="incident-instant-may-lack-an-offset",
        path="touchstone/incidents.py",
        old="        or occurred_at.utcoffset() is None\n",
        new="",
        tests=("tests/test_incidents.py::test_an_instant_must_be_timezone_aware",),
    ),
    Mutation(
        name="operation-instant-may-lack-an-offset",
        path="touchstone/operations.py",
        old="        or moment.utcoffset() is None\n",
        new="",
        tests=("tests/test_operations.py::test_an_instant_must_be_timezone_aware",),
    ),
    Mutation(
        name="key-lifecycle-instant-may-lack-an-offset",
        path="touchstone/keyring.py",
        old=" or at.utcoffset() is None",
        new="",
        tests=(
            "tests/test_keyring.py::test_a_lifecycle_instant_must_be_timezone_aware",
        ),
    ),
    Mutation(
        name="retrieval-instant-may-lack-an-offset",
        path="touchstone/compiler.py",
        old="        or retrieved_at.utcoffset() is None\n",
        new="",
        tests=(
            "tests/test_compiler.py::test_a_retrieval_instant_must_be_timezone_aware",
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


def run_tests(tests: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """Run exactly these test nodes, capturing everything."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *tests,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def run_mutation(mutation: Mutation) -> tuple[str, str]:
    """Apply one mutation, judge the run, and always restore.

    Returns a verdict of "killed", "survived" or "broken", with the detail worth printing.
    """
    target = ROOT / mutation.path
    original = target.read_text(encoding="utf-8")
    occurrences = original.count(mutation.old)
    if occurrences != 1:
        return (
            "broken",
            f"found {occurrences} matches in {mutation.path}, expected exactly one — "
            "the harness anchor is stale",
        )
    try:
        target.write_text(
            original.replace(mutation.old, mutation.new), encoding="utf-8"
        )
        finished = run_tests(mutation.tests)
    finally:
        target.write_text(original, encoding="utf-8")

    if finished.returncode == _TESTS_FAILED:
        failed = [
            line
            for line in finished.stdout.splitlines()
            if line.startswith("FAILED") or line.startswith("ERROR")
        ]
        return "killed", "; ".join(failed[:2])
    if finished.returncode == 0:
        return "survived", "every named test still passed"
    reason = (
        "no tests were collected"
        if finished.returncode == _NO_TESTS_COLLECTED
        else f"pytest exited {finished.returncode}"
    )
    return "broken", f"{reason}\n{finished.stdout.strip()[-800:]}"


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
            f"mean anything\n{baseline.stdout.strip()[-2000:]}",
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
