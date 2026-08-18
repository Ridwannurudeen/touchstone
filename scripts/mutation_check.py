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

# Generous against the slowest targeted set here (the subprocess restart tests run real
# daemons) and well inside CI's 45-minute cap on the mutation job, so a hanging mutant is
# reported as one rather than reaching the cap and taking the whole job's result with it.
# This bound also applies when the harness is run by hand, where no job timeout exists.
MUTATION_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class Mutation:
    """One shipped fix, removed, and the tests that must notice."""

    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


# The inventory is asserted, not counted. `killed/len(MUTATIONS)` is a ratio of the list to
# itself: an empty or truncated MUTATIONS reports "0/0 mutants killed" and exits 0, so the
# job that exists to prove the tests bite would go green having run nothing. That is the same
# failure `assert_suite_ran.py` exists to prevent one job over, and this harness had it too.
# Raise this deliberately when a mutation is added; a diff that changes it is the point.
EXPECTED_MUTATIONS = 125


MUTATIONS = (
    Mutation(
        name="a-freshness-window-need-not-match-the-source-manifest",
        path="touchstone/compiler.py",
        old="""        if (
            control.grace_period != source_manifest.grace_period
            or source_manifest.grace_unit not in control.expected_value
        ):""",
        new="        if False:",
        tests=(
            "tests/test_compiler.py::"
            "test_a_freshness_window_must_be_the_one_the_source_manifest_declares",
        ),
    ),
    Mutation(
        name="an-inert-grace-period-may-ride-along-in-a-control",
        path="touchstone/compiler.py",
        old="    elif control.grace_period != 0:",
        new="    elif False:",
        tests=("tests/test_compiler.py::test_a_grace_period_nothing_reads_is_refused",),
    ),
    Mutation(
        name="the-code-may-drift-from-the-manifests-grace-policy",
        path="touchstone/sources.py",
        old='        grace_period=0,\n        grace_unit="business_days",',
        new='        grace_period=7,\n        grace_unit="business_days",',
        tests=(
            "tests/test_sources.py::"
            "test_the_code_carries_the_grace_policy_its_manifest_declares",
        ),
    ),
    Mutation(
        name="a-freshness-control-may-advertise-a-window-it-does-not-enforce",
        path="touchstone/compiler.py",
        old="        if len(declared) != 1 or declared[0] != control.grace_period:",
        new="        if False:",
        tests=(
            "tests/test_compiler.py::"
            "test_a_freshness_window_must_equal_the_window_that_is_enforced",
        ),
    ),
    Mutation(
        name="an-expected-value-may-carry-keys-the-operator-does-not-define",
        path="touchstone/evaluate.py",
        old="""        permitted = _EXPECTED_KEYS.get(operator)
        if permitted is None or not set(expected_value) <= permitted:
            return False""",
        new="        pass",
        tests=(
            "tests/test_evaluate.py::"
            "test_a_key_the_operator_does_not_define_is_refused",
        ),
    ),
    Mutation(
        name="a-freshness-window-may-name-the-wrong-time-unit",
        path="touchstone/evaluate.py",
        old="        if len(windows) != 1 or unit not in expected_value:",
        new="        if len(windows) != 1:",
        tests=(
            "tests/test_evaluate.py::"
            "test_a_freshness_window_must_name_the_unit_its_deadline_is_computed_in",
        ),
    ),
    Mutation(
        name="the-prompt-stops-asking-for-a-row-age-window",
        path="touchstone/compiler.py",
        old="  minimum_row_age_business_days   int >= 0, optional",
        new="  (removed)",
        tests=("tests/test_compiler.py::test_the_prompt_asks_for_the_row_age_window",),
    ),
    Mutation(
        name="an-unusable-row-age-window-is-accepted-into-the-set",
        path="touchstone/evaluate.py",
        old="""    if isinstance(expected_value, Mapping) and "minimum_row_age_business_days" in (
        expected_value
    ):""",
        new="    if False:",
        tests=(
            "tests/test_evaluate.py::"
            "test_a_minimum_row_age_must_be_usable_to_be_accepted",
            "tests/test_evaluate.py::"
            "test_a_minimum_row_age_where_nothing_reads_a_row_is_refused",
        ),
    ),
    Mutation(
        name="preflight-takes-authorization-as-proof-of-lineage",
        path="touchstone/publish.py",
        old="        if identity != manifest.publisher_identity_address:",
        new="        if False:",
        # A pure-Python test, deliberately. An earlier version of this file claimed the only
        # tests reaching this branch were the managed-chain ones and dropped the mutation on
        # that basis -- which was false, and the claim sat in the repository until an audit
        # read it. `test_publish_signed` drives a stub JSON-RPC node and needs no Hardhat, so
        # the branch is mutation-covered in a job that has Python and nothing else.
        tests=(
            "tests/test_publish_signed.py::"
            "test_a_publisher_from_another_lineage_is_refused",
        ),
    ),
    Mutation(
        name="the-release-document-reads-a-config-hardhat-may-not-use",
        path="scripts/build_release.py",
        old="    declared = json.loads((root / SOLIDITY).read_bytes())",
        new='    declared = json.loads((root / Path("contracts") / "legacy.json").read_bytes())',
        tests=(
            "tests/test_build_release.py::"
            "test_the_compiler_settings_come_from_the_data_file_hardhat_reads",
        ),
    ),
    Mutation(
        name="the-release-document-may-be-written-into-its-own-tree",
        path="scripts/build_release.py",
        old="""        _refuse_output_inside(arguments.root, arguments.out)
""",
        new="",
        tests=(
            "tests/test_build_release.py::"
            "test_a_document_written_into_the_tree_it_describes_is_refused",
        ),
    ),
    Mutation(
        name="the-release-document-accepts-an-instant-that-never-happened",
        path="scripts/build_release.py",
        old="""    try:
        datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseError(f"--built-at is not a real instant: {built_at!r}") from error
""",
        new="",
        tests=(
            "tests/test_build_release.py::"
            "test_a_timestamp_of_the_right_shape_but_no_such_instant_is_refused",
        ),
    ),
    Mutation(
        name="the-release-document-excludes-templates-by-exact-case",
        path="scripts/build_release.py",
        old="    name = path.name.lower()",
        new="    name = path.name",
        tests=(
            "tests/test_build_release.py::"
            "test_a_template_named_in_upper_case_is_still_excluded",
        ),
    ),
    Mutation(
        name="the-release-document-is-not-reproducible",
        path="scripts/build_release.py",
        old='        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\\n"',
        new='        json.dumps(document, indent=2, sort_keys=False, allow_nan=False) + "\\n"',
        tests=(
            "tests/test_build_release.py::"
            "test_encoded_keys_are_sorted_and_end_in_a_newline",
        ),
    ),
    Mutation(
        name="the-release-document-invents-the-counts-it-was-not-given",
        path="scripts/build_release.py",
        old="    missing = [name for name, value in supplied.items() if value is None]",
        new="    missing = []",
        tests=(
            "tests/test_build_release.py::"
            "test_an_omitted_test_summary_is_recorded_as_absent_not_as_zero",
            "tests/test_build_release.py::"
            "test_a_partial_test_summary_does_not_fill_the_missing_counts_with_zero",
        ),
    ),
    Mutation(
        name="the-release-document-calls-every-tree-clean",
        path="scripts/build_release.py",
        old='    return {"sha": sha, "tree_clean": porcelain.strip() == ""}',
        new='    return {"sha": sha, "tree_clean": True}',
        tests=(
            "tests/test_build_release.py::"
            "test_a_dirty_tree_is_recorded_as_not_clean_and_still_emits_a_manifest",
        ),
    ),
    Mutation(
        name="a-recovery-never-retires-its-outage",
        path="scripts/run_service.py",
        old='        self._close_open_incidents("evidence was retrieved and published again")\n',
        new="",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_an_outage_is_retired_by_the_publication_that_follows_it",
        ),
    ),
    Mutation(
        name="the-workflow-gate-check-covers-nothing",
        path="scripts/assert_ci_gates.py",
        old="    missing = sorted(set(jobs) - {AGGREGATE} - set(needs))",
        new="    missing = []",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_a_job_missing_from_the_aggregate_is_refused",
            "tests/test_assert_ci_gates.py::"
            "test_an_aggregate_that_waits_for_nothing_is_refused",
            "tests/test_assert_ci_gates.py::"
            "test_a_workflow_with_an_uncovered_job_exits_nonzero",
        ),
    ),
    Mutation(
        name="the-aggregate-may-merely-echo-the-results",
        path="scripts/assert_ci_gates.py",
        old="    if not any(_runs_the_enforcer(step) for step in _steps(aggregate)):",
        new="    if False:",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_an_aggregate_that_only_echoes_the_results_is_refused",
            "tests/test_assert_ci_gates.py::"
            "test_an_aggregate_that_never_reads_the_results_is_refused",
        ),
    ),
    Mutation(
        name="the-enforcer-command-is-recognised-by-substrings",
        path="scripts/assert_ci_gates.py",
        old="    return isinstance(body, str) and body.strip() == ENFORCER_COMMAND",
        new=(
            '    return isinstance(body, str) and "scripts/assert_gates_passed.py" in body and '
            '"needs." in body and ".result" in body'
        ),
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_an_enforcer_receiving_only_one_gate_result_is_refused",
            "tests/test_assert_ci_gates.py::"
            "test_an_enforcer_whose_failure_is_ignored_is_refused",
            "tests/test_assert_ci_gates.py::"
            "test_an_enforcer_command_that_is_only_echoed_is_refused",
        ),
    ),
    Mutation(
        name="the-aggregate-execution-recipe-is-not-pinned",
        path="scripts/assert_ci_gates.py",
        old="""    if not _trusted_aggregate_steps(aggregate):
        problems.append(
            f"{AGGREGATE!r} does not use the trusted execution recipe for the enforcer"
        )
""",
        new="",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_the_enforcer_step_has_one_trusted_execution_recipe",
            "tests/test_assert_ci_gates.py::"
            "test_a_preceding_step_cannot_replace_the_enforcer",
        ),
    ),
    Mutation(
        name="the-aggregate-execution-context-is-not-pinned",
        path="scripts/assert_ci_gates.py",
        old="""    if (
        aggregate.get("runs-on") != "ubuntu-24.04"
        or "defaults" in workflow
        or environment not in ({}, SAFE_WORKFLOW_ENV)
        or any(
            key in aggregate for key in ("defaults", "env", "container", "strategy")
        )
    ):
        problems.append(
            f"{AGGREGATE!r} does not use the trusted execution context for the enforcer"
        )
""",
        new="",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_the_aggregate_execution_context_is_trusted",
        ),
    ),
    Mutation(
        name="a-step-condition-may-skip-a-gate",
        path="scripts/assert_ci_gates.py",
        old="""            step_condition = step.get("if")
            if step_condition is not None and not (
                isinstance(step_condition, str)
                and step_condition.strip() == ALWAYS
            ):
                problems.append(
                    f"step {position} of job {name!r} has a step condition that can "
                    "skip its work"
                )
""",
        new="",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_a_gate_step_condition_cannot_skip_its_work",
        ),
    ),
    Mutation(
        name="a-single-step-may-continue-on-error-unnoticed",
        path="scripts/assert_ci_gates.py",
        old='            if step.get("continue-on-error", False) is not False:',
        new="            if False:",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_a_step_that_continues_on_error_is_refused",
            "tests/test_assert_ci_gates.py::"
            "test_a_gates_own_step_continuing_on_error_is_refused",
        ),
    ),
    Mutation(
        name="a-step-continue-on-error-expression-is-trusted",
        path="scripts/assert_ci_gates.py",
        old='            if step.get("continue-on-error", False) is not False:',
        new='            if step.get("continue-on-error") is True:',
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_an_expression_cannot_make_continue_on_error_safe",
        ),
    ),
    Mutation(
        name="a-condition-containing-always-is-good-enough",
        path="scripts/assert_ci_gates.py",
        old="    if not (isinstance(condition, str) and condition.strip() == ALWAYS):",
        new="    if not (isinstance(condition, str) and ALWAYS in condition):",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_a_condition_that_merely_contains_always_is_refused",
        ),
    ),
    Mutation(
        name="the-enforcer-accepts-a-gate-that-did-not-run",
        path="scripts/assert_gates_passed.py",
        old="        if result != PASSING",
        new='        if result not in (PASSING, "skipped", "cancelled")',
        tests=(
            "tests/test_assert_gates_passed.py::"
            "test_anything_other_than_success_is_refused",
        ),
    ),
    Mutation(
        name="the-enforcer-accepts-having-judged-nothing",
        path="scripts/assert_gates_passed.py",
        old='        return ["no gate results were given, so nothing was checked"]',
        new="        return []",
        tests=("tests/test_assert_gates_passed.py::test_no_results_at_all_is_refused",),
    ),
    Mutation(
        name="the-gate-ignores-a-wrapper-that-declares-totals",
        path="scripts/assert_suite_ran.py",
        old="""    if root.tag == "testsuites":
        disagreements.extend(""",
        new="""    if False:
        disagreements.extend(""",
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_wrapper_declaring_totals_that_contradict_the_cases_is_refused",
        ),
    ),
    Mutation(
        name="a-gate-may-continue-on-error-unnoticed",
        path="scripts/assert_ci_gates.py",
        old='        if job.get("continue-on-error", False) is not False:',
        new="        if False:",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_a_job_that_continues_on_error_is_refused",
        ),
    ),
    Mutation(
        name="a-job-continue-on-error-expression-is-trusted",
        path="scripts/assert_ci_gates.py",
        old='        if job.get("continue-on-error", False) is not False:',
        new='        if job.get("continue-on-error") is True:',
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_an_expression_cannot_make_continue_on_error_safe",
        ),
    ),
    Mutation(
        name="the-aggregate-may-be-skipped-instead-of-failed",
        path="scripts/assert_ci_gates.py",
        old="    if not (isinstance(condition, str) and condition.strip() == ALWAYS):",
        new="    if False:",
        tests=(
            "tests/test_assert_ci_gates.py::"
            "test_an_aggregate_without_always_is_refused",
        ),
    ),
    Mutation(
        name="the-gate-accepts-any-xml-as-a-report",
        path="scripts/assert_suite_ran.py",
        old="""    if root.tag not in ROOT_TAGS:
        raise NotAReport(
            f"root element is <{root.tag}>, not one of "
            f"{' or '.join(f'<{tag}>' for tag in sorted(ROOT_TAGS))}"
        )
""",
        new="",
        # Not `test_xml_that_is_not_a_report_is_refused`. That input is a foreign root
        # holding loose cases, which the structural checks refuse on their own, so this
        # mutation survived it — the harness caught a registered fix that had stopped
        # deciding anything. The named test wraps a *valid, consistent* suite in a foreign
        # root, which nothing but this check refuses.
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_valid_report_buried_in_a_foreign_root_is_refused",
        ),
    ),
    Mutation(
        name="the-junit-gate-adopts-a-buried-suite",
        path="scripts/assert_suite_ran.py",
        old='    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")',
        new=(
            '    suites = [root] if root.tag == "testsuite" '
            'else list(root.iter("testsuite"))'
        ),
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_suite_buried_under_a_foreign_element_is_refused",
        ),
    ),
    Mutation(
        name="the-junit-gate-adopts-a-buried-case",
        path="scripts/assert_suite_ran.py",
        old='    cases = [case for suite in suites for case in suite.findall("testcase")]',
        new='    cases = [case for suite in suites for case in suite.iter("testcase")]',
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_case_buried_under_a_foreign_element_is_refused",
        ),
    ),
    Mutation(
        name="the-junit-gate-ignores-misplaced-outcomes",
        path="scripts/assert_suite_ran.py",
        old="""    if misplaced_outcomes:
        raise NotAReport(
            "these outcomes are not direct children of test cases: "
            + ", ".join(misplaced_outcomes)
        )
""",
        new="",
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_outcomes_outside_a_testcase_direct_child_are_refused",
        ),
    ),
    Mutation(
        name="the-gate-accepts-a-summary-contradicting-its-cases",
        path="scripts/assert_suite_ran.py",
        old="""    if disagreements:
        raise NotAReport(
            "the suite summary disagrees with its own cases — "
            + "; ".join(disagreements)
        )
""",
        new="",
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_summary_that_disagrees_with_its_own_cases_is_refused",
            "tests/test_assert_suite_ran.py::"
            "test_a_case_that_failed_under_a_summary_claiming_none_is_refused",
        ),
    ),
    Mutation(
        name="the-gate-accepts-a-negative-count",
        path="scripts/assert_suite_ran.py",
        old="""    if count < 0:
        raise NotAReport(f"<testsuite {attribute}={raw!r}> is negative")
""",
        new="",
        tests=("tests/test_assert_suite_ran.py::test_a_negative_count_is_refused",),
    ),
    Mutation(
        name="the-gate-ignores-disabled-suite-totals",
        path="scripts/assert_suite_ran.py",
        old='TALLIES = ("tests", "disabled", "skipped", "failures", "errors")',
        new='TALLIES = ("tests", "skipped", "failures", "errors")',
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_report_declaring_disabled_cases_is_refused",
        ),
    ),
    Mutation(
        name="the-gate-counts-a-notrun-case-as-executed",
        path="scripts/assert_suite_ran.py",
        old='        "disabled": sum(case.get("status") == "notrun" for case in cases),',
        new='        "disabled": 0,',
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_a_notrun_status_without_a_disabled_total_is_refused",
        ),
    ),
    Mutation(
        name="the-gate-accepts-a-coherent-disabled-report",
        path="scripts/assert_suite_ran.py",
        old="    if report.disabled:",
        new="    if False:",
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_disabled_cases_with_matching_notrun_statuses_are_refused",
        ),
    ),
    Mutation(
        name="the-gate-reads-a-wrapper-holding-no-suite",
        path="scripts/assert_suite_ran.py",
        old="""    if not suites:
        raise NotAReport(f"<{root.tag}> contains no <testsuite>")
""",
        new="",
        tests=(
            "tests/test_assert_suite_ran.py::test_a_wrapper_holding_no_suite_is_refused",
        ),
    ),
    Mutation(
        name="the-gate-adopts-a-case-outside-every-suite",
        path="scripts/assert_suite_ran.py",
        old="""    stray = len(list(root.iter("testcase"))) - len(cases)
    if stray:
        raise NotAReport(
            f"{stray} <testcase> element(s) are not a direct child of a <testsuite>"
        )
""",
        new="",
        tests=(
            "tests/test_assert_suite_ran.py::test_a_case_outside_any_suite_is_refused",
        ),
    ),
    Mutation(
        name="the-gate-counts-cases-instead-of-naming-them",
        path="scripts/assert_suite_ran.py",
        old="    if found != expected:",
        new="    if len(found) != len(expected):",
        tests=(
            "tests/test_assert_suite_ran.py::"
            "test_the_right_number_of_the_wrong_tests_is_refused",
            "tests/test_assert_suite_ran.py::test_a_case_in_a_different_module_is_refused",
            "tests/test_assert_suite_ran.py::test_one_case_reported_twice_is_refused",
        ),
    ),
    Mutation(
        name="recovery-parses-a-bundle-permissively",
        path="touchstone/ustb_daemon.py",
        old="            bundle = strict_json_loads(raw)",
        new="            bundle = json.loads(raw)",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_recovery_reads_a_bundle_the_way_a_reader_would",
        ),
    ),
    Mutation(
        name="recovery-republishes-without-a-bundle",
        path="touchstone/operations.py",
        old="        if before_publish is not None:",
        new="        if False:",
        tests=(
            "tests/test_service.py::"
            "test_recovery_will_not_republish_a_report_whose_bundle_is_missing",
        ),
    ),
    Mutation(
        name="recovery-accepts-a-bundle-for-another-report",
        path="touchstone/ustb_daemon.py",
        old="        if bundled != operation.signed_report:  # type: ignore[attr-defined]",
        new="        if False:",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_recovery_refuses_a_bundle_that_describes_a_different_report",
        ),
    ),
    Mutation(
        name="each-control-re-reads-the-ledger-under-the-snapshot",
        path="touchstone/evaluate.py",
        old="        approved_control(entry, ledger=snapshot) for entry in snapshot[APPROVED_KEY]",
        new="        approved_control(entry) for entry in snapshot[APPROVED_KEY]",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_one_slot_reads_the_approval_ledger_exactly_once",
        ),
    ),
    Mutation(
        name="a-bundle-is-published-without-being-verified",
        path="touchstone/ustb_daemon.py",
        old="            verify_bundle(bundle)",
        new="            pass",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_a_report_whose_bundle_cannot_be_verified_is_never_published",
        ),
    ),
    Mutation(
        name="the-report-never-checks-the-ledger-it-commits-to",
        path="touchstone/report.py",
        old="    assert_ledger_permits(records, ledger_from_bytes(ledger_snapshot))",
        new="    pass",
        tests=(
            "tests/test_verify.py::"
            "test_a_ledger_change_between_deriving_controls_and_signing_is_refused",
        ),
    ),
    Mutation(
        name="a-bundle-filename-is-taken-on-trust",
        path="touchstone/ustb_daemon.py",
        old="    if not isinstance(epoch_id, str) or not _EPOCH_ID.fullmatch(epoch_id):",
        new="    if False:",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_a_bundle_filename_cannot_escape_its_directory",
        ),
    ),
    Mutation(
        name="a-bundle-filename-may-be-a-windows-device",
        path="touchstone/ustb_daemon.py",
        old=(
            "    if (\n"
            "        isinstance(epoch_id, str)\n"
            '        and epoch_id.split(".", 1)[0].upper() in _WINDOWS_DEVICES\n'
            "    ):"
        ),
        new="    if False:",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_a_bundle_is_never_named_after_a_windows_device",
        ),
    ),
    Mutation(
        name="bundle-reads-the-ledger-a-second-time",
        path="touchstone/verify.py",
        old="    if bundled_ledger_digest != committed:",
        new="    if False:",
        tests=(
            "tests/test_verify.py::"
            "test_a_bundle_refuses_a_ledger_that_drifted_since_the_report_was_signed",
        ),
    ),
    Mutation(
        name="a-report-is-published-without-being-bundled",
        path="touchstone/ustb_daemon.py",
        old="        if bundle_sink is not None:",
        new="        if False:",
        tests=(
            "tests/test_ustb_daemon.py::"
            "test_an_unattended_run_writes_a_bundle_that_verifies",
        ),
    ),
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
        name="report-reads-the-epoch-more-than-once",
        path="touchstone/report.py",
        old="    epoch = _epoch_snapshot(epoch)",
        new="    pass",
        tests=("tests/test_report.py::test_a_report_describes_one_epoch",),
    ),
    Mutation(
        name="report-snapshot-is-not-materialised",
        path="touchstone/report.py",
        old="        sources=tuple(_source_snapshot(source) for source in epoch.sources),",
        new="        sources=(_source_snapshot(source) for source in epoch.sources),",
        tests=("tests/test_report.py::test_a_report_describes_one_epoch",),
    ),
    Mutation(
        name="report-elements-remain-caller-owned",
        path="touchstone/report.py",
        old="            _evaluation_snapshot(evaluation) for evaluation in epoch.evaluations",
        new="            evaluation for evaluation in epoch.evaluations",
        tests=(
            "tests/test_report.py::test_a_report_describes_one_reading_of_each_evaluation",
        ),
    ),
    Mutation(
        name="report-instant-resolved-more-than-once",
        path="touchstone/report.py",
        old='        retrieved_at=utc_instant(source.retrieved_at, "source retrieved_at"),',
        new="        retrieved_at=source.retrieved_at,",
        tests=("tests/test_report.py::test_a_report_resolves_each_instant_once",),
    ),
    Mutation(
        name="a-datetime-subclass-escapes-the-error-contract",
        path="touchstone/quantities.py",
        old="    if type(value) is not datetime:",
        new="    if not isinstance(value, datetime):",
        tests=(
            "tests/test_quantities.py::test_a_datetime_subclass_is_refused_rather_than_defended_against",
        ),
    ),
    Mutation(
        name="a-clock-reading-is-not-checked",
        path="touchstone/schedule.py",
        old='        return finite_number(monotonic(), "monotonic()")',
        new="        return monotonic()",
        tests=(
            "tests/test_schedule.py::test_a_clock_reading_that_is_not_a_number_ends_the_schedule",
        ),
    ),
    Mutation(
        name="deployment-manifest-read-more-than-once",
        path="touchstone/deployment.py",
        old='        try:\n            value = frozen_snapshot(value, "deployment manifest")\n        except ValueError as error:\n            raise DeploymentError(str(error)) from error',
        new="        pass",
        tests=(
            "tests/test_deployment.py::test_a_manifest_is_validated_and_built_from_one_reading",
        ),
    ),
    Mutation(
        name="transparency-read-failure-escapes-untyped",
        path="touchstone/translog.py",
        old="        except OSError as error:\n            # A log that cannot be read",
        new="        except UnicodeDecodeError as error:\n            # A log that cannot be read",
        tests=(
            "tests/test_translog.py::test_a_log_that_cannot_be_read_is_this_modules_failure",
        ),
    ),
    Mutation(
        name="evidence-read-failure-escapes-untyped",
        path="touchstone/evidence.py",
        old="    except OSError as error:\n        # An artifact that cannot be read",
        new="    except UnicodeDecodeError as error:\n        # An artifact that cannot be read",
        tests=(
            "tests/test_evidence.py::test_an_object_that_cannot_be_read_is_this_modules_failure",
        ),
    ),
    Mutation(
        name="a-hostile-mapping-escapes-the-snapshot-contract",
        path="touchstone/signing.py",
        old="    except Exception as error:\n        # Walking a caller's mapping runs caller code",
        new="    except UnicodeDecodeError as error:\n        # Walking a caller's mapping runs caller code",
        tests=(
            "tests/test_deployment.py::test_a_manifest_that_cannot_be_snapshotted_is_a_deployment_error",
        ),
    ),
    Mutation(
        name="a-failure-that-cannot-be-rendered-escapes-the-handler",
        path="touchstone/signing.py",
        old="    try:\n        detail = str(error)\n    except Exception:",
        new="    try:\n        detail = str(error)\n    except UnicodeDecodeError:",
        tests=(
            "tests/test_deployment.py::test_a_failure_that_cannot_describe_itself_is_still_a_deployment_error",
        ),
    ),
    Mutation(
        name="control-record-read-more-than-once",
        path="touchstone/controls.py",
        old="        value = dict(value)",
        new="        pass",
        tests=(
            "tests/test_controls.py::test_a_control_is_inspected_and_built_from_one_reading",
        ),
    ),
    Mutation(
        name="sequence-read-failure-bypasses-retry",
        path="touchstone/publish.py",
        old='            raise TransportUnavailable(\n                f"registry did not answer a sequence read: {error}"\n            ) from error',
        new="            raise",
        tests=(
            "tests/test_publish_signed.py::test_a_read_that_fails_after_preflight_is_still_a_transport_failure",
        ),
    ),
    Mutation(
        name="operations-write-failure-escapes-untyped",
        path="touchstone/operations.py",
        old='        raise OperationsError(f"cannot write {path.name}: {error}") from error',
        new="        raise",
        tests=(
            "tests/test_operations.py::test_a_durable_write_that_fails_is_this_stores_failure",
        ),
    ),
    Mutation(
        name="journal-write-failure-escapes-untyped",
        path="touchstone/publish.py",
        old='            raise PendingSubmission(\n                f"the pending journal cannot be written: {error}"\n            ) from error',
        new="            raise",
        tests=(
            "tests/test_publish.py::test_a_journal_that_cannot_be_written_is_a_pending_submission",
        ),
    ),
    Mutation(
        name="worker-start-failure-escapes-untyped",
        path="touchstone/normalize/ustb.py",
        old='        raise NormalizationError(\n            f"USTB normalization worker could not be started: {error}"\n        ) from error',
        new="        raise",
        tests=(
            "tests/test_normalize_ustb.py::test_a_worker_that_cannot_be_started_is_a_normalization_error",
        ),
    ),
    Mutation(
        name="a-heartbeat-can-outlive-its-expiry",
        path="touchstone/heartbeat.py",
        old='    if expires_at <= moment:\n        reasons.append("the heartbeat has expired")',
        new='    if False:\n        reasons.append("the heartbeat has expired")',
        tests=(
            "tests/test_heartbeat.py::test_a_heartbeat_expires_rather_than_staying_green",
        ),
    ),
    Mutation(
        name="a-future-heartbeat-is-accepted",
        path="touchstone/heartbeat.py",
        old="    if written_at > moment:",
        new="    if False:",
        tests=(
            "tests/test_heartbeat.py::test_a_heartbeat_written_in_the_future_is_refused",
        ),
    ),
    Mutation(
        name="a-shell-string-is-accepted-as-a-restart-command",
        path="touchstone/watchdog.py",
        old="    if isinstance(argv, (str, bytes)):",
        new="    if False:",
        tests=(
            "tests/test_watchdog.py::test_a_restart_command_must_be_an_argument_vector",
        ),
    ),
    Mutation(
        name="a-webhook-url-may-carry-a-secret-in-its-query",
        path="touchstone/alerts.py",
        old="    if parsed.query or parsed.fragment:",
        new="    if False:",
        tests=("tests/test_alerts.py::test_an_ambiguous_endpoint_is_refused",),
    ),
    Mutation(
        name="an-alert-failure-repeats-the-endpoint-response",
        path="touchstone/alerts.py",
        old='        raise AlertError(f"the webhook answered HTTP {error.code}") from None',
        new='        raise AlertError(f"the webhook answered {error.read()}") from None',
        tests=(
            "tests/test_alerts.py::test_a_failing_endpoint_does_not_leak_the_credential_or_its_body",
        ),
    ),
    Mutation(
        name="free-text-reaches-an-alert-body",
        path="touchstone/alerts.py",
        old="    if detail_code is not None and not _is_code(detail_code):",
        new="    if False:",
        tests=("tests/test_alerts.py::test_free_text_cannot_reach_the_detail_field",),
    ),
    Mutation(
        name="an-unknown-runway-is-treated-as-covered",
        path="touchstone/gas.py",
        old="        return self.funded_through is not None and self.funded_through >= until",
        new="        return self.funded_through is None or self.funded_through >= until",
        tests=(
            "tests/test_gas.py::test_no_measured_cost_is_unknown_rather_than_a_guess",
        ),
    ),
    Mutation(
        name="a-reverted-publication-raises-the-measured-cost",
        path="touchstone/gas.py",
        old='        if receipt.get("status") != 1:\n            continue',
        new="        if False:\n            continue",
        tests=(
            "tests/test_gas.py::test_a_reverted_publication_does_not_raise_the_measured_cost",
        ),
    ),
    Mutation(
        name="a-second-process-may-copy-a-live-workspace",
        path="touchstone/backup.py",
        # The lock itself, not the exception translation. The earlier mutation only changed
        # which error was caught, so it proved the refusal was worded correctly while the
        # lock still did all the work — it could not distinguish a backup module that
        # enforces the invariant from one that merely reports it.
        old="        with exclusive_lock(root.lock) as held, exclusive_lock(root.observer_lock):",
        new="        held = Held(path=root.lock.resolve())\n        if True:",
        tests=(
            "tests/test_backup.py::test_a_genuinely_separate_process_cannot_back_up_a_live_workspace",
        ),
    ),
    Mutation(
        name="a-backup-may-run-while-the-observer-is-appending",
        path="touchstone/backup.py",
        # The observer half of the same invariant. The daemon lock alone proved only that a
        # *daemon* was stopped; the watcher writes evidence into the same workspace under a
        # different lock, so dropping this one lets an archive be taken mid-append while the
        # code still reads as if it establishes quiescence.
        old="        with exclusive_lock(root.lock) as held, exclusive_lock(root.observer_lock):",
        new="        with exclusive_lock(root.lock) as held:",
        tests=("tests/test_backup.py::test_a_live_observer_also_blocks_a_backup",),
    ),
    Mutation(
        name="a-restore-trusts-the-inventory-that-travelled-with-it",
        path="touchstone/backup.py",
        old="        if hashlib.sha256(raw).hexdigest() != member.sha256:",
        new="        if False:",
        # Not the tampered-archive test: that one fails at decryption and never reaches
        # this check. The attacker here holds the key and forged the inventory.
        tests=(
            "tests/test_backup.py::test_a_valid_archive_whose_inventory_lies_is_refused",
        ),
    ),
    Mutation(
        name="a-restore-trusts-the-size-it-was-given",
        path="touchstone/backup.py",
        old="        if len(raw) != member.size:",
        new="        if False:",
        tests=(
            "tests/test_backup.py::test_a_valid_archive_whose_size_lies_is_refused",
        ),
    ),
    Mutation(
        name="an-archive-path-may-escape-its-target",
        path="touchstone/backup.py",
        old="    posix, windows = PurePosixPath(path), PureWindowsPath(path)",
        new="    posix, windows = PurePosixPath('safe'), PureWindowsPath('safe')",
        tests=(
            "tests/test_backup.py::test_a_valid_archive_cannot_write_outside_its_target",
        ),
    ),
    Mutation(
        name="a-restore-may-overwrite-an-existing-tree",
        path="touchstone/backup.py",
        old="    if target.exists():",
        new="    if False:",
        tests=(
            "tests/test_backup.py::test_restore_never_overwrites_an_existing_directory",
        ),
    ),
    Mutation(
        name="an-archive-nonce-is-reused",
        path="touchstone/backup.py",
        old="    chosen = secrets.token_bytes(NONCE_BYTES) if nonce is None else nonce",
        new="    chosen = bytes(NONCE_BYTES) if nonce is None else nonce",
        tests=("tests/test_backup.py::test_every_archive_uses_a_fresh_nonce",),
    ),
    Mutation(
        name="the-backup-key-may-be-another-secret",
        path="touchstone/backup.py",
        old='    for other in ("TOUCHSTONE_SIGNING_SEED", "TOUCHSTONE_PUBLISHER_PRIVATE_KEY"):',
        new="    for other in ():",
        tests=(
            "tests/test_backup.py::test_a_backup_key_that_is_another_secret_is_refused",
        ),
    ),
    Mutation(
        name="the-daemon-stops-beating-while-it-waits",
        path="scripts/run_service.py",
        old='    schedule_arguments["sleep"] = _beating_sleep(\n        service, schedule_arguments.pop("sleep", time.sleep)\n    )',
        new="        pass",
        tests=(
            "tests/test_service_reliability.py::test_the_daemon_stays_alive_through_a_daily_idle_period",
        ),
    ),
    Mutation(
        name="a-backup-accepts-a-hold-on-another-lock",
        path="touchstone/backup.py",
        old="        held.verify(root.lock)",
        new="        pass",
        tests=(
            "tests/test_backup.py::test_a_backup_requires_a_live_hold_on_this_workspaces_lock",
        ),
    ),
    Mutation(
        name="a-released-hold-still-proves-the-lock",
        path="touchstone/locking.py",
        old="        if not self.active:",
        new="        if False:",
        tests=(
            "tests/test_backup.py::test_a_backup_requires_a_live_hold_on_this_workspaces_lock",
        ),
    ),
    Mutation(
        name="the-credential-may-ride-in-a-hand-made-url",
        path="touchstone/alerts.py",
        old='    _refuse_credential(url, token, "URL")',
        new="    pass",
        tests=(
            "tests/test_alerts.py::test_a_hand_made_webhook_with_the_credential_in_its_url_is_refused",
        ),
    ),
    Mutation(
        name="an-alert-body-of-any-shape-is-sent",
        path="touchstone/alerts.py",
        old="    if set(body) != _BODY_FIELDS:",
        new="    if False:",
        tests=(
            "tests/test_alerts.py::test_a_body_that_is_not_the_declared_shape_is_refused",
        ),
    ),
    Mutation(
        name="a-malformed-webhook-url-escapes-untyped",
        path="touchstone/alerts.py",
        old='    except ValueError as error:\n        raise AlertError(f"the webhook URL cannot be parsed: {error}") from error',
        new='    except UnicodeDecodeError as error:\n        raise AlertError(f"the webhook URL cannot be parsed: {error}") from error',
        tests=("tests/test_alerts.py::test_a_malformed_url_is_this_modules_refusal",),
    ),
    Mutation(
        name="an-unreadable-archive-escapes-untyped",
        path="touchstone/backup.py",
        old='        raise BackupError(f"the archive is not readable JSON: {error}") from error',
        new="        raise",
        tests=(
            "tests/test_backup.py::test_an_authenticated_archive_that_is_not_json_is_this_modules_failure",
        ),
    ),
    Mutation(
        name="an-undecodable-member-escapes-untyped",
        path="touchstone/backup.py",
        old='            raw = bytes.fromhex(str(item["bytes"]))\n        except ValueError as error:',
        new='            raw = bytes.fromhex(str(item["bytes"]))\n        except UnicodeDecodeError as error:',
        tests=(
            "tests/test_backup.py::test_an_undecodable_member_payload_is_this_modules_failure",
        ),
    ),
    Mutation(
        name="a-forged-hold-passes-as-proof-of-the-lock",
        path="touchstone/locking.py",
        old="        if (held.st_ino, held.st_dev) != (named.st_ino, named.st_dev):",
        new="        if False:",
        # Not the three-case test: every case there is refused earlier, by isinstance, by
        # liveness or by the path. Only a live descriptor paired with the target's own path
        # reaches the inode comparison.
        tests=(
            "tests/test_backup.py::test_a_live_descriptor_borrowed_from_another_lock_is_refused",
        ),
    ),
    Mutation(
        name="the-endpoint-is-validated-only-at-construction",
        path="touchstone/alerts.py",
        old="    validate_endpoint(webhook.url, webhook.token)",
        new="    pass",
        tests=(
            "tests/test_alerts.py::test_a_hand_made_webhook_with_the_credential_in_its_url_is_refused",
            "tests/test_alerts.py::test_a_hand_made_webhook_over_plaintext_http_is_refused",
        ),
    ),
    Mutation(
        name="a-pending-operations-signature-is-not-verified",
        path="scripts/restore_workspace.py",
        old='            verify_signed_report(record["signed_report"], keys)',
        new="            pass",
        tests=(
            "tests/test_restore_cli.py::test_a_pending_operation_signed_by_an_unknown_key_fails_the_restore",
        ),
    ),
    Mutation(
        name="an-alert-fires-on-every-check",
        path="touchstone/watchdog.py",
        old="    if material == previous_fingerprint:",
        new="    if False:",
        tests=(
            "tests/test_watchdog.py::test_an_alert_fires_on_the_edge_not_on_every_check",
        ),
    ),
    Mutation(
        name="a-first-healthy-check-announces-a-recovery",
        path="touchstone/watchdog.py",
        old="        if previous_fingerprint is None:",
        new="        if False:",
        tests=(
            "tests/test_watchdog.py::test_a_first_healthy_observation_announces_nothing",
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
    Mutation(
        name="restart-republishes-a-served-epoch",
        path="scripts/run_service.py",
        old="        if not published:\n            return None",
        new="        if True:\n            return None",
        tests=(
            "tests/test_ustb_daemon.py::test_a_restart_on_a_served_day_publishes_nothing_and_reports_no_fault",
        ),
    ),
    Mutation(
        name="an-unreachable-registry-is-taken-as-permission",
        path="scripts/run_service.py",
        old="        except Exception as error:  # noqa: BLE001 - recorded, and the slot does not run\n            # Not knowing whether the epoch is published is not permission to publish it.\n            return self._record_incident(",
        new="        except Exception as error:  # noqa: BLE001 - recorded, and the slot does not run\n            del error\n            return None\n            return self._record_incident(",
        tests=(
            "tests/test_ustb_daemon.py::test_a_chain_that_will_not_answer_stops_the_slot_rather_than_guessing",
        ),
    ),
    Mutation(
        name="the-status-mapping-is-its-own-oracle",
        path="touchstone/publish.py",
        old="    AssetState.STALE.value: 1,\n    AssetState.INCONSISTENT.value: 2,",
        new="    AssetState.STALE.value: 2,\n    AssetState.INCONSISTENT.value: 1,",
        tests=(
            "tests/test_publish.py::test_each_state_reaches_the_chain_as_the_registry_declares_it",
        ),
    ),
    Mutation(
        name="the-schema-stops-requiring-a-deployment-state",
        path="deployments/manifest.schema.json",
        old='    "reporting_keys",\n    "deployment_state",',
        new='    "reporting_keys",',
        tests=(
            "tests/test_deployment_manifests.py::test_the_schema_rejects_a_manifest_with_no_state",
        ),
    ),
    Mutation(
        name="a-superseded-deployment-is-publishable",
        path="touchstone/publish.py",
        old="        if not manifest.is_active:",
        new="        if False:",
        tests=(
            "tests/test_publish_epoch_cli.py::test_the_backend_refuses_a_superseded_deployment",
            "tests/test_publish_epoch_cli.py::test_the_direct_publisher_refuses_before_touching_the_network",
        ),
    ),
    Mutation(
        name="fixture-mode-reaches-a-public-network",
        path="scripts/run_service.py",
        old="    if arguments.fixtures and not manifest.is_local:",
        new="    if False:",
        tests=(
            "tests/test_service_startup.py::test_a_public_network_is_never_served_from_committed_fixtures",
        ),
    ),
)


# pytest's documented exit codes. Only TESTS_FAILED can mean a targeted test ran and
# rejected the mutant. Every other nonzero code means the run did not happen as intended —
# a mistyped node id collects nothing and exits USAGE_ERROR — and counting those as kills
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
    # Bounded, because a mutation can produce a hang rather than a failure — removing a
    # guard on a retry count or a wait is exactly the kind of fix registered here. A mutant
    # that survives by never returning is named as `broken`, which is the honest verdict:
    # nothing judged it. Without this the run would stop only at the job's own cap, which
    # reports the whole job as timed out and says nothing about which mutant hung.
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=MUTATION_TIMEOUT_SECONDS,
    )


def wanted_nodes(tests: tuple[str, ...]) -> set[tuple[str, str]]:
    """Map requested node ids to the (module, function) pairs pytest reports them as."""
    wanted = set()
    for test in tests:
        path, _, function = test.partition("::")
        wanted.add((path.removesuffix(".py").replace("/", "."), function))
    return wanted


def reported_outcomes(
    report_path: Path, tests: tuple[str, ...]
) -> tuple[list[str], list[str]] | None:
    """Return this mutation's (failed, errored) nodes, or None if no report was written.

    An exit code says a run ended badly; it does not say a test noticed anything. Pytest
    can exit 1 without collecting a single node — an unwritable temporary directory, a
    plugin that fails during initialisation — and reading that as a kill credits the
    mutation with a failure no assertion ever made. Only a structured report naming a node
    from *this* mutation's target set is evidence.

    The two kinds are separated because pytest means different things by them. `<failure>`
    is the call phase: the test body ran and rejected the mutant. `<error>` is setup or
    teardown, so the body never ran — the fixture could not build a workspace, a temporary
    directory was gone. That is infrastructure wearing the target's name, and counting it
    is the same mistake as counting the exit code, one level further in.

    A kill therefore means precisely "a targeted test ran and failed", not "an assertion
    rejected it". Pytest maps every call-phase failure to `<failure>`, so an uncaught
    `KeyError` or `RuntimeError` from the mutated code counts too. That is still evidence
    the mutation changed observable behaviour, which is what a mutant is for; it is simply
    a weaker statement than "an assertion caught it", and this says the weaker one.
    """
    if not report_path.exists():
        return None
    wanted = wanted_nodes(tests)
    failures, errors = [], []
    for case in ElementTree.parse(report_path).iter("testcase"):
        module = case.get("classname", "")
        name = case.get("name", "")
        if (module, name.partition("[")[0]) not in wanted:
            continue
        if case.find("failure") is not None:
            failures.append(name)
        elif case.find("error") is not None:
            errors.append(name)
    return failures, errors


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
        except subprocess.TimeoutExpired:
            return (
                "broken",
                f"the targeted tests did not finish within {MUTATION_TIMEOUT_SECONDS}s — "
                "a mutant that hangs has judged nothing",
            )
        finally:
            write_exactly(target, original)
        outcomes = reported_outcomes(report_path, mutation.tests)

    return classify(finished.returncode, outcomes, _diagnostic(finished))


def classify(
    returncode: int, outcomes: tuple[list[str], list[str]] | None, diagnostic: str
) -> tuple[str, str]:
    """Turn one run into a verdict. Separated so the decision itself can be tested."""
    if returncode == 0:
        return "survived", "every named test still passed"
    failures, errors = outcomes if outcomes is not None else ([], [])
    if returncode == _TESTS_FAILED and failures:
        return "killed", "; ".join(failures[:2])
    if errors:
        reason = (
            f"setup or teardown errored in {'; '.join(errors[:2])}, so the test body "
            "never ran and nothing judged the mutant"
        )
    elif outcomes is None:
        reason = f"pytest exited {returncode} without writing a report"
    elif returncode == _TESTS_FAILED:
        reason = "pytest exited 1 without recording a failure in any targeted test"
    elif returncode == _NO_TESTS_COLLECTED:
        reason = "no tests were collected"
    else:
        reason = f"pytest exited {returncode}"
    return "broken", f"{reason}\n{diagnostic}"


def _diagnostic(finished: subprocess.CompletedProcess[str]) -> str:
    """Both streams. An initialisation failure explains itself only on stderr."""
    return "\n".join(
        part
        for part in (finished.stdout.strip()[-800:], finished.stderr.strip()[-800:])
        if part
    )


def main() -> int:
    if len(MUTATIONS) != EXPECTED_MUTATIONS:
        print(
            f"refusing to run: {len(MUTATIONS)} mutations are registered and "
            f"{EXPECTED_MUTATIONS} are expected. A harness that grades itself against its "
            "own list reports a perfect score for an empty one.",
            file=sys.stderr,
        )
        return 2

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
