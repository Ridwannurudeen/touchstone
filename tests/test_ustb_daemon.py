"""The unattended USTB path, end to end, with nobody present.

Until this existed the service refused every mode except `--resolve-only`, and the count of
autonomous adapters was zero while a registry sat deployed and idle. So the assertions here
are about the loop actually closing: evidence retrieved, controls evaluated, a report built
and signed, and a publication reaching the chain — driven by the scheduler rather than by a
test calling each stage in turn.

Fixtures rather than the live issuer. A test that reaches api.superstate.com is a test that
fails when the network does, and the transport boundary is injected precisely so the rest of
the path can be exercised without it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys

import pytest

from touchstone.controls import AssetState
from touchstone.epoch import FixtureTransport
from touchstone.evidence import EvidenceStore
from touchstone.incidents import IncidentLog
from touchstone.operations import OperationsStore
from touchstone.publish import PublisherClient, TransportUnavailable
from touchstone.signing import Ed25519Signer, verify_signed_report
from touchstone.sources import SourceTransportError
from touchstone.translog import TransparencyLog
from touchstone.ustb_daemon import (
    EpochProductionError,
    asset_key_bytes,
    epoch_id_for,
    make_producer,
    report_uri,
    write_bundle,
)
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from run_service import Service, serve  # noqa: E402
from test_publish import FakeBackend, _signed_report  # noqa: E402
from test_service import Clock  # noqa: E402


class Dead:
    """A transport that refuses, and records that it was asked at all.

    Several tests here assert the issuer is *not* reached — a slot that suppresses itself
    must do so before fetching. Recording the calls is what makes that assertion mean
    something rather than merely not crashing.
    """

    def __init__(self, detail: str = "the issuer endpoint did not answer") -> None:
        self.detail = detail
        self.calls: list[str] = []

    def get(self, url, *, timeout, max_bytes):
        self.calls.append(url)
        raise SourceTransportError(self.detail)


FIXTURES = Path(__file__).parents[1] / "fixtures"
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"
REGISTRY = "0x" + "ab" * 20
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)


def seeded_store(tmp_path: Path) -> EvidenceStore:
    """A store with the earlier capture already in it.

    Value controls observe only a row confirmed across two captures, so an evidence store
    with nothing behind it produces an honest UNVERIFIABLE rather than a report. Seeding is
    what a real deployment does by running for a day before anyone looks.
    """
    from touchstone.epoch import run_ustb_epoch

    store = EvidenceStore(Workspace(tmp_path / "asset").evidence)
    run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
    )
    return store


def built(tmp_path: Path, backend: FakeBackend) -> tuple[Service, Workspace]:
    workspace = Workspace(tmp_path / "asset")
    workspace.root.mkdir(parents=True, exist_ok=True)
    service = Service(
        PublisherClient(
            backend,
            TransparencyLog(workspace.transparency_log),
            workspace.pending_journal,
        ),
        OperationsStore(workspace.operations, now=lambda: RETRIEVED_AT),
        IncidentLog(workspace.incidents),
        asset_key=ASSET,
        lock_path=workspace.lock,
        sleep=lambda seconds: None,
        now=lambda: RETRIEVED_AT,
        heartbeat_path=workspace.heartbeat,
        registry_address=REGISTRY,
    )
    return service, workspace


def producer(
    store: EvidenceStore,
    service: Service,
    backend: FakeBackend,
    *,
    capture: date = date(2026, 8, 14),
    transport=None,
    bundle_sink=None,
):
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    return signer, make_producer(
        store=store,
        signer=signer,
        next_sequence=lambda: backend.latest_sequence(asset_key_bytes(ASSET)) + 1,
        previous_state=lambda on: (
            AssetState.UNVERIFIABLE
            if service.operations.load_state(ASSET) is None
            else service.operations.load_state(ASSET).projected(on)
        ),
        transport=FixtureTransport(FIXTURES, capture)
        if transport is None
        else transport,
        bundle_sink=bundle_sink,
    )


def run(service: Service, produce, *, runs: int = 1):
    clock = Clock()
    return serve(
        service,
        produce,
        report_uri=report_uri,
        interval_seconds=86_400.0,
        max_runs=runs,
        epoch_of=epoch_id_for,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: RETRIEVED_AT,
    )


def test_the_daemon_produces_and_publishes_a_ustb_epoch_unattended(
    tmp_path: Path,
) -> None:
    """The gap this closes, in one assertion: a slot ran and something reached the chain."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    signer, produce = producer(store, service, backend)

    outcome = run(service, produce)

    assert outcome.completed == 1
    assert not outcome.failed
    assert len(backend.submissions) == 1, "nothing was published"

    entries = TransparencyLog(workspace.transparency_log).verify()
    assert len(entries) == 1
    report = verify_signed_report(
        entries[0]["signed_report"], {signer.kid: signer.public_key_record()}
    )
    assert report["state"] == "CONFIRMED"
    assert report["epoch_id"] == "ustb-2026-08-14"
    assert len(report["controls"]) == 8


def test_the_published_report_is_the_one_that_was_signed(tmp_path: Path) -> None:
    """The chain and the log must describe the same report, or neither means anything."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend)

    run(service, produce)

    entries = TransparencyLog(workspace.transparency_log).verify()
    published = entries[0]["publication"]
    # The log entry records the transaction and its receipt; the URI is derived from the
    # report rather than stored beside it, so it is checked against the report itself.
    assert published["receipt"]["status"] == 1
    assert published["transaction_hash"].startswith("0x")
    assert (
        report_uri(entries[0]["signed_report"])
        == "urn:touchstone:ustb:ustb-2026-08-14:1"
    )


def test_a_source_outage_opens_an_incident_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    """Silence is recorded as silence. It is never rendered as an observation."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend, transport=Dead())

    outcome = run(service, produce)

    assert outcome.completed == 1, "the slot completed; it simply published nothing"
    assert not backend.submissions, "an outage produced a publication"
    assert TransparencyLog(workspace.transparency_log).verify() == []
    incidents = IncidentLog(workspace.incidents).verify()
    assert len(incidents) == 1
    assert incidents[0]["kind"] == "SOURCE_UNAVAILABLE"
    assert "could not be retrieved" in incidents[0]["detail"]


def test_the_source_outage_is_not_recorded_as_an_epoch_failure(tmp_path: Path) -> None:
    """A retrieval failure is not a finding about the issuer, and the kinds differ."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend, transport=Dead("403"))
    run(service, produce)

    kinds = {entry["kind"] for entry in IncidentLog(workspace.incidents).verify()}
    assert kinds == {"SOURCE_UNAVAILABLE"}
    assert "EPOCH_FAILED" not in kinds


def test_the_sequence_comes_from_the_chain_not_a_local_counter(
    tmp_path: Path,
) -> None:
    """A local counter drifts the first time a publication fails, and the registry
    refuses an out-of-order sequence — so the drift would become a permanent outage."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    signer, produce = producer(store, service, backend)

    run(service, produce)

    entries = TransparencyLog(workspace.transparency_log).verify()
    first = verify_signed_report(
        entries[0]["signed_report"], {signer.kid: signer.public_key_record()}
    )

    # The registry's own key, from the publisher's derivation. The producer briefly had a
    # second implementation of this that hashed with SHA-256 while the publisher used
    # keccak, so it asked about a key the registry had never heard of, read zero, and would
    # have proposed a sequence the chain already held.
    key = asset_key_bytes(ASSET)
    assert first["sequence"] == backend.latest_sequence(key)
    assert backend.get_report(key, first["sequence"]).sequence == first["sequence"]


def test_an_unseeded_store_reports_unverifiable_rather_than_guessing(
    tmp_path: Path,
) -> None:
    """With no qualifying predecessor every value control abstains, by design.

    This is the honest answer on a service's first ever day, and it must publish that
    rather than inventing a confirmation from the single capture it has.
    """
    workspace = Workspace(tmp_path / "asset")
    workspace.root.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(workspace.evidence)
    backend = FakeBackend()
    service, _ = built(tmp_path, backend)
    signer, produce = producer(store, service, backend)

    run(service, produce)

    entries = TransparencyLog(workspace.transparency_log).verify()
    report = verify_signed_report(
        entries[0]["signed_report"], {signer.kid: signer.public_key_record()}
    )
    assert report["state"] == "UNVERIFIABLE"


def test_a_report_uri_is_a_urn_not_a_url_that_resolves_to_nothing() -> None:
    """Minting a https:// URI would put a promise on chain the project has not kept."""
    signed = _signed_report(7)
    signed["report"]["epoch_id"] = "ustb-2026-08-14"

    assert report_uri(signed).startswith("urn:touchstone:ustb:")
    assert "http" not in report_uri(signed)


def test_a_signed_report_without_a_report_is_refused() -> None:
    with pytest.raises(EpochProductionError, match="must carry its report"):
        report_uri({"report": "not a mapping"})


def test_the_producer_refuses_a_naive_instant(tmp_path: Path) -> None:
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, _ = built(tmp_path, backend)
    _, produce = producer(store, service, backend)

    with pytest.raises(ValueError, match="timezone-aware"):
        produce(datetime(2026, 8, 14, 17, 8, 12))


def test_a_second_slot_does_not_republish_the_same_day(tmp_path: Path) -> None:
    """Two slots, two sequences — never one report twice under different numbers."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend)

    run(service, produce, runs=2)

    entries = TransparencyLog(workspace.transparency_log).verify()
    sequences = [entry["signed_report"]["report"]["sequence"] for entry in entries]
    assert sequences == sorted(set(sequences)), (
        "a sequence was reused or went backwards"
    )


def test_a_restart_on_a_served_day_publishes_nothing_and_reports_no_fault(
    tmp_path: Path,
) -> None:
    """The defect this closes, reproduced exactly as it was found.

    A clean process starts its first slot at `now()`. A second process started the same day
    derives the same epoch, asks the chain for the next sequence — correctly getting 2 —
    and offers a second signed report about one day. Both are valid, both verify, and a
    consumer reading the latest report sees whichever landed last. Nothing in the durable
    state stopped it, because that state records a projection rather than what the chain
    holds.

    The second process must fetch nothing, sign nothing and publish nothing, and must not
    call that a failure: a daemon restarted on a day it has already served is working.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()

    first_service, workspace = built(tmp_path, backend)
    _, first_produce = producer(store, first_service, backend)
    run(first_service, first_produce)
    assert len(backend.submissions) == 1

    # A different Service object on the same workspace and the same chain, holding nothing
    # in memory from the first — which is what a restart is.
    second_service, _ = built(tmp_path, backend)
    dead = Dead("the issuer must not even be asked")
    _, second_produce = producer(store, second_service, backend, transport=dead)

    outcome = run(second_service, second_produce)

    assert outcome.completed == 1
    assert not outcome.failed
    assert len(backend.submissions) == 1, "the restart published a second report"
    assert not dead.calls, "the restart fetched evidence it had no reason to fetch"
    assert IncidentLog(workspace.incidents).verify() == [], (
        "a correct suppression was recorded as a fault"
    )
    epochs = [
        entry["signed_report"]["report"]["epoch_id"]
        for entry in TransparencyLog(workspace.transparency_log).verify()
    ]
    assert epochs == ["ustb-2026-08-14"]


def test_a_chain_that_will_not_answer_stops_the_slot_rather_than_guessing(
    tmp_path: Path,
) -> None:
    """Not knowing whether the epoch is published is not permission to publish it."""
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)

    def refuse(asset_key, epoch_key):
        raise TransportUnavailable("the registry did not answer")

    backend.epoch_sequence = refuse
    dead = Dead("the issuer must not be asked before the chain has answered")
    _, produce = producer(store, service, backend, transport=dead)

    run(service, produce)

    assert not backend.submissions
    assert not dead.calls
    incidents = IncidentLog(workspace.incidents).verify()
    assert [entry["kind"] for entry in incidents] == ["PUBLICATION_UNRESOLVED"]
    assert "would not say whether" in incidents[0]["detail"]


def test_the_slot_asks_about_the_epoch_the_producer_would_name(tmp_path: Path) -> None:
    """One derivation, or the suppression asks about a day the report is not about."""
    moment = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, _ = built(tmp_path, backend)
    _, produce = producer(store, service, backend)

    signed = produce(moment)

    assert signed["report"]["epoch_id"] == epoch_id_for(moment)


def test_a_suppressed_slot_does_not_retire_an_unrelated_incident(
    tmp_path: Path,
) -> None:
    """Suppression observed nothing, so it may not report a recovery.

    The fast path fetches no evidence and evaluates nothing. Closing every open incident
    there retired source outages and epoch failures on the strength of a publication that
    happened before they were opened — telling an operator the issuer was reachable again
    when nobody had looked.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()

    first_service, workspace = built(tmp_path, backend)
    _, produce = producer(store, first_service, backend)
    run(first_service, produce)

    incidents = IncidentLog(workspace.incidents)
    incidents.open_incident(
        asset_key=ASSET,
        kind="SOURCE_UNAVAILABLE",
        detail="the issuer endpoint has been down since yesterday",
        occurred_at=RETRIEVED_AT,
    )

    second_service, _ = built(tmp_path, backend)
    dead = Dead("the issuer must not even be asked")
    _, suppressed = producer(store, second_service, backend, transport=dead)
    run(second_service, suppressed)

    assert not dead.calls
    still_open = [entry.kind for entry in incidents.open_incidents()]
    assert "SOURCE_UNAVAILABLE" in still_open, (
        "a slot that looked at nothing reported the source recovered"
    )


def test_an_unattended_run_writes_a_bundle_that_verifies(tmp_path: Path) -> None:
    """The claim the whole project rests on, checked against what the service leaves behind.

    `create_bundle` had one caller before this: the local-chain rehearsal in
    `scripts/e2e_local.py`. So the unattended path signed a report, published it to the
    registry, and wrote nothing a stranger could verify it with — while the dossier's central
    promise is that they can. The report was on chain and the evidence for it was not
    exportable.
    """
    from touchstone.verify import verify_bundle

    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store, service, backend, bundle_sink=write_bundle(workspace.bundles)
    )

    run(service, produce)

    written = sorted(workspace.bundles.glob("*.json"))
    assert len(written) == 1, f"expected exactly one bundle, got {written}"
    assert written[0].name == "ustb-2026-08-14-1.json"
    # Partial files must never survive a completed write.
    assert not list(workspace.bundles.glob("*.partial"))

    import json as _json

    bundle = _json.loads(written[0].read_text(encoding="utf-8"))
    report = verify_bundle(bundle)
    assert report["epoch_id"] == "ustb-2026-08-14"
    assert report["sequence"] == 1


def test_no_bundle_is_written_when_no_sink_is_given(tmp_path: Path) -> None:
    """The parameter is optional, and every pre-existing caller passes nothing.

    Making it mandatory would have broken the service's own construction and every test that
    predates it, which is how an optional dependency becomes a silent one.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend)

    run(service, produce)

    assert not workspace.bundles.exists()
