from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from touchstone.controls import AssetState, ControlRecord, EvaluationResult
from touchstone.epoch import (
    EpochControlReport,
    EpochSourceReport,
    FixtureTransport,
    USTBEpochReport,
    run_ustb_epoch,
)
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
        )


def test_report_builds_stale_epoch_with_contract_valid_timestamps(
    tmp_path: Path,
) -> None:
    """Evidence that aged past its deadline, with timestamps the registry accepts.

    `AssetGate` compares `block.timestamp` against `observedAt` and `validUntil`, and the
    registry refuses `validUntil < observedAt`. An epoch observed after its own deadline
    would derive a `validUntil` in the past, so it is clamped to the observation instant —
    which is exactly the case this pins.
    """
    store = EvidenceStore(tmp_path)
    run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
    )
    retrieved_at = datetime(2026, 8, 20, 14, 16, 17, tzinfo=timezone.utc)
    epoch = run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=store,
        now=date(2026, 8, 20),
        retrieved_at=retrieved_at,
    )
    report = build_observation_report(
        epoch,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-20",
        sequence=2,
        publisher_kid="ed25519:" + "11" * 32,
    )

    assert report["state"] == "STALE"
    assert report["observed_at"] == "2026-08-20T14:16:17Z"
    assert report["valid_until"] == report["observed_at"]


def test_report_requires_explicit_limitations(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limitations must not be empty"):
        build_observation_report(
            _epoch(tmp_path),
            default_ustb_controls(),
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
            limitations=[],
        )


def test_report_provenance_comes_from_the_controls_not_the_caller(
    tmp_path: Path,
) -> None:
    """A caller cannot name a compilation that produced none of the reported controls.

    The digests used to be a parameter, checked only as well-formed hex by both the builder
    and the offline verifier — so a report could carry provenance with no relationship to
    the controls it reported. They are now derived from the approved controls themselves.
    """
    controls = default_ustb_controls()
    report = build_observation_report(
        _epoch(tmp_path),
        controls,
        epoch_id="ustb-2026-08-13",
        sequence=1,
        publisher_kid="ed25519:" + "11" * 32,
    )

    assert report["compiler_provenance_digests"] == sorted(
        {control.compilation_sha256 for control in controls}
    )


def test_a_control_bound_to_no_compilation_cannot_be_reported(tmp_path: Path) -> None:
    """An approved control naming no artifact is exactly what the binding exists to stop."""
    from touchstone.approval import ApprovalError

    controls = list(default_ustb_controls())
    unbound = controls[0].to_mapping()
    unbound["compilation_sha256"] = None
    controls[0] = ControlRecord.from_mapping(unbound)

    with pytest.raises(ApprovalError, match="names no compilation"):
        build_observation_report(
            _epoch(tmp_path),
            controls,
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
        )


def test_a_control_edited_after_approval_cannot_be_reported(tmp_path: Path) -> None:
    """Approval may change two fields. Anything else is a control no compiler proposed."""
    from touchstone.approval import ApprovalError

    controls = list(default_ustb_controls())
    edited = controls[0].to_mapping()
    edited["grace_period"] = edited["grace_period"] + 7
    controls[0] = ControlRecord.from_mapping(edited)

    with pytest.raises(ApprovalError, match="differs from the candidate"):
        build_observation_report(
            _epoch(tmp_path),
            controls,
            epoch_id="ustb-2026-08-13",
            sequence=1,
            publisher_kid="ed25519:" + "11" * 32,
        )


def test_report_fixture_helper_is_canonical_json_compatible(tmp_path: Path) -> None:
    report = _report(tmp_path)
    assert report["version"] == "touchstone.observation-report.v3"
    assert all(len(digest) == 64 for digest in report["compiler_provenance_digests"])


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
    )

    assert counting._reads == {"sources": 1, "evaluations": 1}
    assert report == _report(tmp_path / "stable")


class _StatefulEvaluation(EpochControlReport):
    """An evaluation whose result changes between reads.

    The transition was validated from one reading and the controls were serialised from
    another, so a CONFIRMED report could carry controls that say CONTRADICTED.
    """

    __slots__ = ("_stored", "_reads")

    @property
    def result(self):
        self._reads.append(len(self._reads))
        first, later = self._stored["result"]
        return first if len(self._reads) == 1 else later


def _stateful(evaluation: EpochControlReport, later) -> _StatefulEvaluation:
    instance = object.__new__(_StatefulEvaluation)
    for name in ("control_id", "observed_value", "evidence_deadline", "observed_on"):
        object.__setattr__(instance, name, getattr(evaluation, name))
    object.__setattr__(instance, "_stored", {"result": (evaluation.result, later)})
    object.__setattr__(instance, "_reads", [])
    return instance


class _DriftingZone(tzinfo):
    """A zone whose offset differs between reads."""

    def __init__(self) -> None:
        self.reads = 0

    def utcoffset(self, dt):
        self.reads += 1
        return timedelta(hours=1) if self.reads == 1 else timedelta(0)

    def dst(self, dt):
        return None


def test_a_report_describes_one_reading_of_each_evaluation(tmp_path: Path) -> None:
    """Elements of the epoch are caller-owned too, not only the sequences holding them."""
    epoch = _epoch(tmp_path)
    drifted = USTBEpochReport(
        asset_key=epoch.asset_key,
        now=epoch.now,
        state=epoch.state,
        evidence_deadline=epoch.evidence_deadline,
        sources=epoch.sources,
        evaluations=tuple(
            _stateful(item, EvaluationResult.CONTRADICTED) for item in epoch.evaluations
        ),
        confirmation=epoch.confirmation,
    )

    report = build_observation_report(
        drifted,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid="ed25519:" + "11" * 32,
    )

    assert report["state"] == "CONFIRMED"
    assert {item["evaluation"]["result"] for item in report["controls"]} == {
        "SATISFIED"
    }, "the serialised controls describe the same reading the state was derived from"


def test_a_report_resolves_each_instant_once(tmp_path: Path) -> None:
    """`observed_at` reused the caller's datetime after the references had normalised it.

    A zone answering with a different offset the second time therefore committed the
    evidence root to one instant while the report declared another.
    """
    epoch = _epoch(tmp_path)
    zone = _DriftingZone()
    source = epoch.sources[0]
    shifted = USTBEpochReport(
        asset_key=epoch.asset_key,
        now=epoch.now,
        state=epoch.state,
        evidence_deadline=epoch.evidence_deadline,
        sources=(
            EpochSourceReport(
                source_id=source.source_id,
                source_url=source.source_url,
                content_type=source.content_type,
                byte_size=source.byte_size,
                evidence_sha256=source.evidence_sha256,
                retrieved_at=source.retrieved_at.replace(tzinfo=zone),
                observed_on=source.observed_on,
            ),
            *epoch.sources[1:],
        ),
        evaluations=epoch.evaluations,
        confirmation=epoch.confirmation,
    )

    report = build_observation_report(
        shifted,
        default_ustb_controls(),
        epoch_id="ustb-2026-08-14",
        sequence=1,
        publisher_kid="ed25519:" + "11" * 32,
    )

    assert zone.reads == 1, "the instant was resolved exactly once"

    # The same epoch with the offset the zone gave on that single read, pinned. If the
    # instant were resolved a second time the zone would answer UTC and this would differ.
    settled = USTBEpochReport(
        asset_key=shifted.asset_key,
        now=shifted.now,
        state=shifted.state,
        evidence_deadline=shifted.evidence_deadline,
        sources=(
            EpochSourceReport(
                source_id=source.source_id,
                source_url=source.source_url,
                content_type=source.content_type,
                byte_size=source.byte_size,
                evidence_sha256=source.evidence_sha256,
                retrieved_at=source.retrieved_at.replace(
                    tzinfo=timezone(timedelta(hours=1))
                ),
                observed_on=source.observed_on,
            ),
            *epoch.sources[1:],
        ),
        evaluations=shifted.evaluations,
        confirmation=shifted.confirmation,
    )
    assert report["evidence_root"] == evidence_root(evidence_references(settled))
