"""A watcher's only job is to say what it saw, and never to say more than that."""

from __future__ import annotations

import ast

from datetime import datetime, timezone
from pathlib import Path

import pytest

from touchstone.observation import (
    DEFAULT_INTERVAL_SECONDS,
    MINIMUM_INTERVAL_SECONDS,
    OBSERVATION_VERSION,
    Observation,
    Transition,
    append,
    build_record,
    canonical_digest,
    classify,
    latest_by_source,
    read_all,
    stamp,
    validate_interval,
)
from touchstone.assets import USTB_ASSET_KEY
from touchstone.locking import exclusive_lock
from touchstone.workspace import Workspace

AT = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
SOURCE = "superstate-ustb-nav-daily"


def observation(**changes: object) -> Observation:
    fields: dict[str, object] = {
        "source_id": SOURCE,
        "observed_at": stamp(AT),
        "transition": Transition.UNCHANGED,
        "payload_sha256": "a" * 64,
        "previous_payload_sha256": "a" * 64,
        "normalized_sha256": "n" * 64,
        "previous_normalized_sha256": "n" * 64,
        "byte_size": 1024,
        "detail": None,
    }
    fields.update(changes)
    return Observation(**fields)  # type: ignore[arg-type]


def digests(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "payload_sha256": "b",
        "previous_payload_sha256": "a",
        "normalized_sha256": "n",
        "previous_normalized_sha256": "n",
    }
    fields.update(changes)
    return fields


class TestClassify:
    def test_first_look_is_not_a_change(self) -> None:
        assert (
            classify(
                **digests(previous_payload_sha256=None, previous_normalized_sha256=None)
            )
            is Transition.FIRST_OBSERVATION
        )

    def test_identical_bytes_are_unchanged(self) -> None:
        assert classify(**digests(payload_sha256="a")) is Transition.UNCHANGED

    def test_moved_bytes_with_equal_substance_are_not_called_a_change(self) -> None:
        """The distinction the whole enum exists for.

        A feed that re-serialises or reorders has not told us anything new about the fund,
        and reporting it as a change would make every such event look like the issuer moved
        a number.
        """
        assert classify(**digests()) is Transition.PAYLOAD_CHANGED

    def test_moved_substance_is_a_change(self) -> None:
        assert (
            classify(**digests(normalized_sha256="m")) is Transition.OBSERVATION_CHANGED
        )

    def test_unavailable_outranks_everything(self) -> None:
        """Silence is never rendered as an observation, even when digests would match."""
        assert (
            classify(**digests(payload_sha256="a", failed=True))
            is Transition.SOURCE_UNAVAILABLE
        )

    def test_missing_payload_is_unavailable_not_unchanged(self) -> None:
        assert classify(**digests(payload_sha256=None)) is Transition.SOURCE_UNAVAILABLE

    def test_unparseable_payload_is_its_own_answer(self) -> None:
        """An artifact that arrived and would not parse is not the same as no artifact."""
        assert classify(**digests(normalized_sha256=None)) is Transition.PARSE_FAILED

    def test_no_prior_substance_to_compare_is_reported_as_uncomparable(self) -> None:
        """Neither PAYLOAD_CHANGED nor OBSERVATION_CHANGED: no comparison happened.

        This used to assert OBSERVATION_CHANGED, and the test encoded the overclaim rather
        than catching it: PAYLOAD_CHANGED would say the substance was checked and matched,
        OBSERVATION_CHANGED would say it was checked and differed, and on this branch there
        was nothing to check it against.
        """
        assert (
            classify(**digests(previous_normalized_sha256=None))
            is Transition.UNCOMPARABLE
        )

    def test_uncomparable_is_not_reported_when_a_prior_form_exists(self) -> None:
        assert classify(**digests()) is not Transition.UNCOMPARABLE
        assert (
            classify(**digests(normalized_sha256="m")) is Transition.OBSERVATION_CHANGED
        )


class TestInterval:
    def test_floor_is_enforced(self) -> None:
        with pytest.raises(ValueError, match="below the"):
            validate_interval(MINIMUM_INTERVAL_SECONDS - 1)

    def test_the_floor_itself_is_allowed(self) -> None:
        assert validate_interval(MINIMUM_INTERVAL_SECONDS) == MINIMUM_INTERVAL_SECONDS

    def test_the_shipped_default_clears_its_own_floor(self) -> None:
        assert validate_interval(DEFAULT_INTERVAL_SECONDS) == DEFAULT_INTERVAL_SECONDS

    @pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), "900"])
    def test_nonsense_intervals_are_refused(self, value: object) -> None:
        with pytest.raises((TypeError, ValueError)):
            validate_interval(value)  # type: ignore[arg-type]


class TestRecord:
    def test_record_carries_its_version(self) -> None:
        assert build_record(observation())["version"] == OBSERVATION_VERSION

    def test_transition_is_recorded_as_text(self) -> None:
        record = build_record(observation(transition=Transition.PAYLOAD_CHANGED))
        assert record["transition"] == "PAYLOAD_CHANGED"

    def test_empty_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source_id"):
            build_record(observation(source_id=""))


class TestLog:
    def test_absent_log_reads_as_no_observations(self, tmp_path: Path) -> None:
        assert read_all(tmp_path / "nothing.jsonl") == []
        assert latest_by_source(tmp_path / "nothing.jsonl") == {}

    def test_appends_accumulate_in_order(self, tmp_path: Path) -> None:
        log = tmp_path / "observations.jsonl"
        for digest in ("a", "b", "c"):
            append(log, build_record(observation(payload_sha256=digest)))
        assert [r["payload_sha256"] for r in read_all(log)] == ["a", "b", "c"]

    def test_latest_is_by_position_not_by_timestamp(self, tmp_path: Path) -> None:
        """A record's timestamp is a claim it makes about itself; position is what happened.

        A clock that steps backwards would otherwise let an older observation present itself
        as the current state of a source.
        """
        log = tmp_path / "observations.jsonl"
        append(
            log,
            build_record(
                observation(payload_sha256="first", observed_at="2026-08-18T09:00:00Z")
            ),
        )
        append(
            log,
            build_record(
                observation(payload_sha256="second", observed_at="2026-08-17T09:00:00Z")
            ),
        )
        assert latest_by_source(log)[SOURCE]["payload_sha256"] == "second"

    def test_sources_are_tracked_independently(self, tmp_path: Path) -> None:
        log = tmp_path / "observations.jsonl"
        append(log, build_record(observation(source_id="a", payload_sha256="1")))
        append(log, build_record(observation(source_id="b", payload_sha256="2")))
        append(log, build_record(observation(source_id="a", payload_sha256="3")))
        latest = latest_by_source(log)
        assert latest["a"]["payload_sha256"] == "3"
        assert latest["b"]["payload_sha256"] == "2"


class TestCanonicalDigest:
    def test_key_order_does_not_change_the_digest(self) -> None:
        assert canonical_digest({"a": 1, "b": 2}) == canonical_digest({"b": 2, "a": 1})

    def test_different_values_give_different_digests(self) -> None:
        assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})

    def test_decimals_are_not_routed_through_float(self) -> None:
        """A digest that depended on binary rounding would not describe what was published."""
        from decimal import Decimal

        assert canonical_digest(Decimal("11.17883400")) == canonical_digest(
            "11.17883400"
        )
        assert canonical_digest(Decimal("0.1")) != canonical_digest(0.1)


class TestWatcherCannotPublish:
    """The safety argument for running this most often, on a shared host.

    The process that runs every few minutes must be the one that can do least. These read
    the modules' actual import graph rather than their prose: an earlier version of this
    searched the file for the word "publish" and failed on the docstring explaining that it
    does not publish, which tested the comment instead of the code.
    """

    FORBIDDEN = {
        "web3",
        "eth_account",
        "touchstone.signing",
        "touchstone.publish",
        "touchstone.keyring",
        "touchstone.deployment",
        "touchstone.translog",
    }

    @staticmethod
    def imported_modules(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def test_the_observer_script_imports_nothing_that_can_publish(self) -> None:
        imported = self.imported_modules(
            Path(__file__).parents[1] / "scripts" / "run_observer.py"
        )
        assert not (imported & self.FORBIDDEN), (
            f"the observer must not import {sorted(imported & self.FORBIDDEN)}"
        )

    def test_the_observation_module_imports_nothing_that_can_publish(self) -> None:
        imported = self.imported_modules(
            Path(__file__).parents[1] / "touchstone" / "observation.py"
        )
        assert not (imported & self.FORBIDDEN), (
            f"observation.py must not import {sorted(imported & self.FORBIDDEN)}"
        )

    def test_no_publisher_secret_is_named_anywhere_in_the_watcher(self) -> None:
        """Names, not prose: an env var name only appears in code that intends to read it."""
        for relative in ("scripts/run_observer.py", "touchstone/observation.py"):
            text = (Path(__file__).parents[1] / relative).read_text(encoding="utf-8")
            assert "TOUCHSTONE_PUBLISHER_PRIVATE_KEY" not in text
            assert "TOUCHSTONE_SIGNING_SEED" not in text


def test_a_backup_evidence_snapshot_skips_one_pass_without_stopping_the_observer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A short snapshot collision is not a second observer or a source observation."""
    from scripts.run_observer import main

    workspace = Workspace(tmp_path / "asset")
    with exclusive_lock(workspace.evidence_lock):
        result = main(
            [
                "--workspace",
                str(workspace.root),
                "--asset-key",
                USTB_ASSET_KEY,
                "--interval-seconds",
                str(MINIMUM_INTERVAL_SECONDS),
                "--max-runs",
                "1",
            ]
        )

    output = capsys.readouterr()
    assert result == 0
    assert "evidence snapshot is in progress; observation pass skipped" in output.err
    assert "another observer" not in output.err
    assert not workspace.observation_log.exists()
