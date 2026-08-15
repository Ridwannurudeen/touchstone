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
        old="    observed = dict(observations)\n    prior = dict(prior_observations)",
        new="    observed = observations\n    prior = prior_observations",
        tests=(
            "tests/test_evaluate.py::test_one_report_describes_one_set_of_observations",
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


def tree_is_clean() -> bool:
    """Report whether the working tree has no tracked modifications."""
    finished = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, capture_output=True)
    return finished.returncode == 0


def run_mutation(mutation: Mutation) -> bool:
    """Apply one mutation, report whether its tests noticed, and always restore."""
    target = ROOT / mutation.path
    original = target.read_text(encoding="utf-8")
    occurrences = original.count(mutation.old)
    if occurrences != 1:
        raise SystemExit(
            f"{mutation.name}: found {occurrences} matches in {mutation.path}, "
            "expected exactly one — the harness is stale"
        )
    try:
        target.write_text(
            original.replace(mutation.old, mutation.new), encoding="utf-8"
        )
        finished = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *mutation.tests,
                "-q",
                "--no-header",
                "-x",
                "-p",
                "no:cacheprovider",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        target.write_text(original, encoding="utf-8")
    return finished.returncode != 0


def main() -> int:
    if not tree_is_clean():
        print(
            "refusing to run: the working tree has uncommitted changes, and this "
            "harness edits tracked files in place",
            file=sys.stderr,
        )
        return 2

    survivors = []
    for mutation in MUTATIONS:
        killed = run_mutation(mutation)
        print(f"{'killed ' if killed else 'SURVIVED'}  {mutation.name}")
        if not killed:
            survivors.append(mutation.name)

    if not tree_is_clean():
        print(
            "the tree was not restored — inspect it before committing", file=sys.stderr
        )
        return 2

    print(f"\n{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutants killed")
    for name in survivors:
        print(f"  survived: {name}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
