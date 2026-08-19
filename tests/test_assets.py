"""The engine is keyed by a descriptor, not by a USTB module constant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import json

import pytest

from touchstone.approval import LEDGER_VERSION_V1
from touchstone.assets import ASSET_BY_KEY, USTB, AssetDescriptor, get_asset
from touchstone.compiler import (
    CompilationStatus,
    DeterministicFixtureProvider,
    compile_evidence,
)
from touchstone.controls import AssetState, ControlRecord
from touchstone.epoch import FixtureTransport, run_epoch
from touchstone.evaluate import default_controls
from touchstone.evidence import EvidenceStore
from touchstone.report import USTB_LIMITATIONS, build_observation_report
from touchstone.signing import Ed25519Signer
from touchstone.sources import SourceManifest, TransportResponse
from touchstone.ustb_daemon import epoch_id_for, make_producer
from historical_pack import historical_controls, historical_ledger_bytes


FIXTURES = Path(__file__).parents[1] / "fixtures"
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)
PROBE_KEY = "eip155:1:0x" + "ab" * 20
PROBE_SOURCE_ID = "synthetic-probe-as-of"
PROBE_URL = "https://synthetic.example/as-of"
PROBE_BODY = b'{"as_of_date":"2026-08-14"}'
PROBE_SPAN = '"as_of_date":"2026-08-14"'
PUBLISHER = "ed25519:" + "11" * 32


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    as_of_date: date


def normalize_probe(source_id: str, raw: bytes, *, max_bytes: int, isolated: bool = False):
    del source_id, max_bytes, isolated
    payload = json.loads(raw.decode("utf-8"))
    return ProbeObservation(as_of_date=date.fromisoformat(payload["as_of_date"]))


def probe_manifest() -> SourceManifest:
    return SourceManifest(
        source_id=PROBE_SOURCE_ID,
        url=PROBE_URL,
        expected_mime="application/json",
        authority_class="issuer-api",
        cadence="daily",
        max_bytes=4_096,
        grace_period=0,
        grace_unit="calendar_days",
    )


def probe_asset() -> AssetDescriptor:
    return AssetDescriptor(
        asset_key=PROBE_KEY,
        display_name="PROBE",
        source_manifest=Path("synthetic-probe.json"),
        sources=(probe_manifest(),),
        adapters={PROBE_SOURCE_ID: "synthetic-as-of"},
        epoch_id_prefix="probe",
        normalize=normalize_probe,
        freshness_units={PROBE_SOURCE_ID: "calendar_days"},
    )


class ProbeTransport:
    def __init__(self, body: bytes = PROBE_BODY) -> None:
        self.body = body
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        del timeout, max_bytes
        self.calls.append(url)
        if url != PROBE_URL:
            raise ValueError("probe transport received an unregistered URL")
        return TransportResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=self.body,
        )


def probe_candidate() -> dict[str, object]:
    return {
        "asset_key": PROBE_KEY,
        "control_id": "probe-as-of-fresh",
        "control_version": 1,
        "predicate_type": "observation",
        "subject": "synthetic as-of date",
        "source_id": PROBE_SOURCE_ID,
        "source_authority_class": "issuer-api",
        "evidence_span": PROBE_SPAN,
        "cadence": "daily",
        "grace_period": 0,
        "observation_adapter": "synthetic-as-of",
        "comparison_operator": "fresh_within",
        "expected_value": {"calendar_days": 0},
        "effective_from": "2026-08-14",
        "effective_until": None,
        "compiler_confidence": 0.95,
        "approval_state": "proposed",
    }


def compiled_probe(tmp_path: Path, asset: AssetDescriptor):
    """One accepted compilation of the probe control, plus the ledger that approves it."""
    store = EvidenceStore(tmp_path / "compile")
    digest = store.store(
        PROBE_BODY,
        source_id=PROBE_SOURCE_ID,
        source_url=PROBE_URL,
        retrieved_at=RETRIEVED_AT,
        declared_mime="application/json",
    )
    result = compile_evidence(
        DeterministicFixtureProvider(
            json.dumps({"controls": [probe_candidate()]}, separators=(",", ":"))
        ),
        evidence_sha256=digest,
        source_manifest=probe_manifest(),
        store=store,
        retrieved_at=RETRIEVED_AT,
        asset=asset,
    )
    assert result.outcomes[0].status is CompilationStatus.ACCEPTED
    artifact = (store.objects_dir / result.compilation_sha256).read_bytes()
    ledger = {
        "version": LEDGER_VERSION_V1,
        "approved": [
            {
                "control_id": "probe-as-of-fresh",
                "compilation_sha256": result.compilation_sha256,
            }
        ],
        "declined": [],
    }
    return result.compilation_sha256, artifact, json.dumps(ledger).encode("utf-8")


def test_ustb_is_the_one_shipped_descriptor() -> None:
    assert get_asset(USTB.asset_key) is USTB
    assert ASSET_BY_KEY[USTB.asset_key] is USTB
    assert USTB.epoch_id_prefix == "ustb"
    assert [source.source_id for source in USTB.sources] == [
        "superstate-ustb-nav-daily",
        "superstate-ustb-yield",
        "superstate-ustb-holdings",
    ]
    assert dict(USTB.adapters) == {
        "superstate-ustb-nav-daily": "ustb-nav-daily",
        "superstate-ustb-yield": "ustb-yield",
        "superstate-ustb-holdings": "ustb-holdings",
    }
    assert USTB.source_manifest.is_file()


def test_an_unknown_asset_key_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown asset_key"):
        get_asset("eip155:1:0x" + "00" * 20)


def test_the_adapter_map_is_not_restated_in_the_compiler() -> None:
    """The two copies in compiler.py are what this change exists to delete."""
    import inspect

    from touchstone import compiler

    source = inspect.getsource(compiler)
    assert "ADAPTER_BY_SOURCE" not in source
    assert "expected_adapters" not in source


def test_two_descriptors_run_two_epochs_and_two_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the whole change is for: a second asset is another descriptor.

    Two stores, two prefixes, two keys. Nothing the first epoch wrote is visible to
    the second, and the reports cannot be swapped without the keys giving them away.
    """
    import touchstone.approval as approval

    probe = probe_asset()
    ustb_store = EvidenceStore(tmp_path / "ustb")
    run_epoch(
        USTB,
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=ustb_store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
        controls=historical_controls(),
    )
    ustb_epoch = run_epoch(
        USTB,
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        store=ustb_store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
        controls=historical_controls(),
    )
    ustb_id = epoch_id_for(RETRIEVED_AT, USTB)
    ustb_report = build_observation_report(
        ustb_epoch,
        historical_controls(),
        epoch_id=ustb_id,
        sequence=1,
        publisher_kid=PUBLISHER,
        approval_ledger=historical_ledger_bytes(),
        limitations=USTB_LIMITATIONS,
    )

    compilation_sha256, artifact, ledger_bytes = compiled_probe(tmp_path, probe)
    monkeypatch.setattr(
        approval, "from_directory", lambda directory=None: approval.from_mapping(
            {compilation_sha256: artifact}
        )
    )
    controls = default_controls(probe, json.loads(ledger_bytes))
    assert [control.asset_key for control in controls] == [PROBE_KEY]
    assert default_controls(USTB, json.loads(ledger_bytes)) == ()

    probe_store = EvidenceStore(tmp_path / "probe")
    probe_transport = ProbeTransport()
    probe_epoch = run_epoch(
        probe,
        transport=probe_transport,
        store=probe_store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
        controls=controls,
    )
    probe_id = epoch_id_for(RETRIEVED_AT, probe)
    probe_report = build_observation_report(
        probe_epoch,
        controls,
        epoch_id=probe_id,
        sequence=1,
        publisher_kid=PUBLISHER,
        approval_ledger=ledger_bytes,
        limitations=USTB_LIMITATIONS,
    )

    assert ustb_epoch.asset_key == USTB.asset_key
    assert probe_epoch.asset_key == PROBE_KEY
    assert ustb_report["asset_key"] == USTB.asset_key
    assert probe_report["asset_key"] == PROBE_KEY
    assert ustb_report["epoch_id"] == "ustb-2026-08-14"
    assert probe_report["epoch_id"] == "probe-2026-08-14"
    assert ustb_id != probe_id

    ustb_digests = {source.evidence_sha256 for source in ustb_epoch.sources}
    probe_digests = {source.evidence_sha256 for source in probe_epoch.sources}
    assert ustb_digests.isdisjoint(probe_digests)
    assert all((ustb_store.objects_dir / digest).is_file() for digest in ustb_digests)
    assert all(
        not (ustb_store.objects_dir / digest).is_file() for digest in probe_digests
    )
    assert all((probe_store.objects_dir / digest).is_file() for digest in probe_digests)
    assert all(
        not (probe_store.objects_dir / digest).is_file() for digest in ustb_digests
    )
    assert probe_transport.calls == [PROBE_URL]
    assert probe_epoch.confirmation is None

    signer = Ed25519Signer.from_seed(bytes(range(32)))
    produce = make_producer(
        store=EvidenceStore(tmp_path / "probe-producer"),
        signer=signer,
        next_sequence=lambda: 1,
        previous_state=lambda on: AssetState.UNVERIFIABLE,
        transport=ProbeTransport(),
        approval_ledger=ledger_bytes,
        asset=probe,
    )
    signed = produce(RETRIEVED_AT)
    assert signed is not None
    assert signed["report"]["asset_key"] == PROBE_KEY
    assert signed["report"]["epoch_id"] == "probe-2026-08-14"


def test_a_control_for_the_other_asset_is_refused() -> None:
    probe = probe_asset()
    foreign = ControlRecord.from_mapping(
        {
            **probe_candidate(),
            "asset_key": USTB.asset_key,
            "approval_state": "approved",
            "compilation_sha256": None,
        }
    )

    with pytest.raises(ValueError, match="asset_key"):
        from touchstone.evaluate import evaluate

        evaluate(
            probe,
            [foreign],
            {PROBE_SOURCE_ID: ProbeObservation(as_of_date=date(2026, 8, 14))},
            prior_observations={},
            now=date(2026, 8, 14),
        )
