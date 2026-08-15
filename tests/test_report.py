from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from touchstone.controls import AssetState, ControlRecord
from touchstone.epoch import FixtureTransport, USTBEpochReport, run_ustb_epoch
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import default_ustb_controls
from touchstone.report import (
    USTB_LIMITATIONS,
    build_observation_report,
    control_set_root,
    evidence_references,
    evidence_root,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)


def _epoch(tmp_path: Path, *, confirmed: bool = True):
    store = EvidenceStore(tmp_path)
    if confirmed:
        run_ustb_epoch(
            transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
            store=store,
            now=date(2026, 8, 13),
            retrieved_at=CONFIRMED_AT,
        )
    return run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
    )


def _report(tmp_path: Path):
    return build_observation_report(
        _epoch(tmp_path),
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid="ed25519:" + "11" * 32,
        compiler_provenance_digests=["22" * 32],
    )


def test_report_contains_recomputable_roots_and_honest_limitations(
    tmp_path: Path,
) -> None:
    epoch = _epoch(tmp_path)
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-13",
        sequence=1,
        publisher_kid="ed25519:" + "11" * 32,
        compiler_provenance_digests=["22" * 32],
    )

    assert report["control_set_root"] == control_set_root(default_ustb_controls())
    assert report["evidence_root"] == evidence_root(evidence_references(epoch))
    assert report["state"] == "CONFIRMED"
    assert report["limitations"] == list(USTB_LIMITATIONS)
    assert [item["control_id"] for item in report["controls"]] == sorted(
        control.control_id for control in default_ustb_controls()
    )


def test_root_construction_is_order_independent_but_content_sensitive(
    tmp_path: Path,
) -> None:
    epoch = _epoch(tmp_path)
    controls = default_ustb_controls()
    digests = evidence_references(epoch)

    assert control_set_root(controls) == control_set_root(reversed(controls))
    assert evidence_root(digests) == evidence_root(list(reversed(digests)))
    changed = [*digests]
    changed[0] = {**changed[0], "sha256": "ff" * 32}
    assert evidence_root(changed) != evidence_root(digests)
    assert {reference["capture_role"] for reference in digests} == {
        "current",
        "confirmation",
    }


def test_report_rejects_inconsistent_state(tmp_path: Path) -> None:
    epoch = _epoch(tmp_path)
    inconsistent = type(epoch)(
        asset_key=epoch.asset_key,
        now=epoch.now,
        state=AssetState.STALE,
        evidence_deadline=epoch.evidence_deadline,
        sources=epoch.sources,
        evaluations=epoch.evaluations,
        confirmation=epoch.confirmation,
    )

    with pytest.raises(ValueError, match="transition rules"):
        build_observation_report(
            inconsistent,
            default_ustb_controls(),
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
            compiler_provenance_digests=["22" * 32],
        )


def test_report_rejects_invalid_correction_reference(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="earlier positive sequence"):
        build_observation_report(
            _epoch(tmp_path),
            default_ustb_controls(),
            epoch_id="ustb-2026-08-13",
            sequence=2,
            correction_of=2,
            publisher_kid="ed25519:" + "11" * 32,
            compiler_provenance_digests=["22" * 32],
        )


def test_report_rejects_controls_for_another_asset(tmp_path: Path) -> None:
    controls = list(default_ustb_controls())
    changed = controls[0].to_mapping()
    changed["asset_key"] = "eip155:1:0x" + "22" * 20
    controls[0] = ControlRecord.from_mapping(changed)
    with pytest.raises(ValueError, match="report asset"):
        build_observation_report(
            _epoch(tmp_path),
            controls,
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
            compiler_provenance_digests=["22" * 32],
        )


def test_report_builds_stale_epoch_with_contract_valid_timestamps(
    tmp_path: Path,
) -> None:
    retrieved_at = datetime(2026, 8, 14, 14, 16, 17, tzinfo=timezone.utc)
    epoch = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES),
        store=EvidenceStore(tmp_path),
        now=date(2026, 8, 14),
        retrieved_at=retrieved_at,
    )
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=2,
        publisher_kid="ed25519:" + "11" * 32,
        compiler_provenance_digests=["22" * 32],
    )

    assert report["state"] == "STALE"
    assert report["observed_at"] == "2026-08-14T14:16:17Z"
    assert report["valid_until"] == report["observed_at"]


def test_report_requires_explicit_limitations(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limitations must not be empty"):
        build_observation_report(
            _epoch(tmp_path),
            default_ustb_controls(),
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
            compiler_provenance_digests=["22" * 32],
            limitations=[],
        )


def test_report_provenance_digests_are_strict(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="compiler provenance"):
        build_observation_report(
            _epoch(tmp_path),
            default_ustb_controls(),
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
            compiler_provenance_digests=["not-a-digest"],
        )

    with pytest.raises(ValueError, match="must not be empty"):
        build_observation_report(
            _epoch(tmp_path),
            default_ustb_controls(),
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
            compiler_provenance_digests=[],
        )


def test_report_fixture_helper_is_canonical_json_compatible(tmp_path: Path) -> None:
    report = _report(tmp_path)
    assert report["version"] == "touchstone.observation-report.v3"
    assert report["compiler_provenance_digests"] == ["22" * 32]


class _CountingEpoch(USTBEpochReport):
    """A genuine epoch subclass that counts how often each sequence is read.

    It is a subclass, so it satisfies `isinstance` and the type check alone cannot save the
    report — only taking one snapshot can. The parent is a frozen slots dataclass, so these
    properties shadow its slot descriptors and the values live in slots of their own,
    populated without going through the dataclass constructor.
    """

    __slots__ = ("_stored", "_reads")

    # Iterators, not tuples. A sequence attribute is not obliged to be re-readable, so a
    # snapshot that stores whatever it was handed without materialising it holds something
    # already spent by the time anything downstream looks.
    @property
    def sources(self):
        self._reads["sources"] += 1
        return iter(self._stored["sources"])

    @property
    def evaluations(self):
        self._reads["evaluations"] += 1
        return iter(self._stored["evaluations"])


def _counting(epoch: USTBEpochReport) -> _CountingEpoch:
    instance = object.__new__(_CountingEpoch)
    for name in ("asset_key", "now", "state", "evidence_deadline", "confirmation"):
        object.__setattr__(instance, name, getattr(epoch, name))
    object.__setattr__(
        instance,
        "_stored",
        {"sources": epoch.sources, "evaluations": epoch.evaluations},
    )
    object.__setattr__(instance, "_reads", {"sources": 0, "evaluations": 0})
    return instance


def test_a_report_describes_one_epoch(tmp_path: Path) -> None:
    """One report, one set of observations, or the report is about nothing in particular.

    Each sequence was read three times, and `evidence_references` read the sources a fourth,
    so an epoch answering those reads differently produced a report whose state, evidence
    root and serialised controls each described a different set — every individual check
    satisfied, none of them able to see the others. Counting the reads is what makes this
    behavioural: a type check alone would let a subclass straight back through, and a
    report built from one read cannot disagree with itself whatever the epoch does.
    """
    epoch = _epoch(tmp_path)
    counting = _counting(epoch)

    report = build_observation_report(
        counting,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid="ed25519:" + "11" * 32,
        compiler_provenance_digests=["22" * 32],
    )

    assert counting._reads == {"sources": 1, "evaluations": 1}
    assert report == _report(tmp_path / "stable")
