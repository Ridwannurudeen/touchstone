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

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from touchstone.controls import AssetState
from touchstone.epoch import FixtureTransport
from touchstone.evidence import EvidenceStore
from touchstone.incidents import IncidentLog
from touchstone.keyring import rolled_over, verification_keys
from touchstone.operations import OperationsStore
from touchstone.publish import PublisherClient, TransportUnavailable
from touchstone.signing import Ed25519Signer, verify_signed_report
from touchstone.sources import SourceTransportError
from touchstone.translog import TransparencyLog
from touchstone.verify import verify_bundle
from touchstone.ustb_daemon import (
    EpochProductionError,
    asset_key_bytes,
    epoch_id_for,
    make_producer,
    report_uri,
    require_verifying_bundle,
    write_bundle,
)
from touchstone.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from historical_pack import historical_controls, historical_ledger_bytes  # noqa: E402
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


class Recovering:
    """A transport that is down for a while and then is not.

    An outage is only half an event. The other half is the issuer coming back, and until
    something drives both halves in one run nothing proves the incident is ever retired —
    only that opening one works. It counts refusals rather than taking a signal, because the
    slots inside one `serve` call cannot be reached from outside it.
    """

    def __init__(self, fixtures: Path, capture: date, *, refusals: int = 1) -> None:
        self._live = FixtureTransport(fixtures, capture)
        self._remaining = refusals
        self.calls: list[str] = []
        self.refused: list[str] = []

    def get(self, url, *, timeout, max_bytes):
        self.calls.append(url)
        if self._remaining > 0:
            self._remaining -= 1
            self.refused.append(url)
            raise SourceTransportError("the issuer endpoint did not answer")
        return self._live.get(url, timeout=timeout, max_bytes=max_bytes)


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

    Seeded under the frozen ledger, matching the slot that consumes it. This defaulted to
    the shipped set, so the day of evidence behind every slot here was evaluated against one
    control set while the slot evaluated against another — invisible while the two happened
    to be equal, and a silent divergence on the next recompile.
    """
    from touchstone.epoch import run_ustb_epoch

    store = EvidenceStore(Workspace(tmp_path / "asset").evidence)
    run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
        controls=historical_controls(),
    )
    return store


def built(
    tmp_path: Path, backend: FakeBackend, *, now: datetime = RETRIEVED_AT
) -> tuple[Service, Workspace]:
    """Wire a service over ``tmp_path/asset``.

    ``now`` is a parameter so a second daemon can be built over the *same* workspace on a
    later day, which is what a restart across a key rollover or a rotation actually is.
    """
    workspace = Workspace(tmp_path / "asset")
    workspace.root.mkdir(parents=True, exist_ok=True)
    service = Service(
        PublisherClient(
            backend,
            TransparencyLog(workspace.transparency_log),
            workspace.pending_journal,
        ),
        OperationsStore(workspace.operations, now=lambda: now),
        IncidentLog(workspace.incidents),
        asset_key=ASSET,
        lock_path=workspace.lock,
        sleep=lambda seconds: None,
        now=lambda: now,
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
    signer: Ed25519Signer | None = None,
    approval_ledger: bytes | None = historical_ledger_bytes(),
):
    """The shared producer for this suite, pinned to the frozen ledger by default.

    These tests drive real slots against fixtures captured on 2026-08-14, and a control
    compiled today cannot evaluate evidence older than its own compile date — so under a
    freshly approved set the daemon publishes nothing and fifteen tests here fail as
    `StopIteration` and `assert 0 == 1`, none of which is about the daemon. Pinning the
    ledger keeps them about scheduling, restarts, outages and recovery.

    Pass `approval_ledger=None` for the production path, where the point *is* what ships.
    """
    if signer is None:
        signer = Ed25519Signer.from_seed(bytes(range(32)))
    return signer, make_producer(
        approval_ledger=approval_ledger,
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


def run(
    service: Service,
    produce,
    *,
    runs: int = 1,
    interval_seconds: float = 86_400.0,
    now: datetime = RETRIEVED_AT,
):
    """Drive the real scheduler on an injected clock.

    ``interval_seconds`` is a parameter because the schedule reads the wall clock exactly
    once and derives every later slot by adding the interval to it. At the daily default,
    two slots are two different days no matter what ``now`` returns — so a test that means
    to run two slots *within one day* has to say so here.
    """
    clock = Clock()
    return serve(
        service,
        produce,
        report_uri=report_uri,
        interval_seconds=interval_seconds,
        max_runs=runs,
        epoch_of=epoch_id_for,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: now,
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


def test_an_outage_is_retired_by_the_publication_that_follows_it(
    tmp_path: Path,
) -> None:
    """The whole arc, in one run: down, back, published, incident closed.

    Opening an incident was proved and closing one was proved, but never across a single
    unattended run driven by the scheduler — so nothing established that a daemon left alone
    through an outage recovers by itself rather than staying in a permanently open incident.
    That is the failure an operator would actually meet, and it is the one an alert would be
    firing about all night.

    Closure is an append, never an edit: the log keeps the opening entry and adds one that
    names it, so the history of the outage survives its resolution.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    transport = Recovering(FIXTURES, date(2026, 8, 14))
    _, produce = producer(store, service, backend, transport=transport)

    outcome = run(service, produce, runs=2)

    assert outcome.completed == 2
    assert not outcome.failed, "an outage that recovers is not a failed schedule"
    assert transport.refused, "the outage never happened, so nothing was recovered from"

    log = IncidentLog(workspace.incidents)
    entries = log.verify()
    opened = [entry for entry in entries if entry["closes"] is None]
    closed = [entry for entry in entries if entry["closes"] is not None]
    assert len(opened) == 1, "exactly one outage was opened"
    assert opened[0]["kind"] == "SOURCE_UNAVAILABLE"
    assert len(closed) == 1, "the outage was never retired"
    assert closed[0]["closes"] == opened[0]["entry_hash"], (
        "the closure does not name the incident it retires"
    )
    assert closed[0]["kind"] == "SOURCE_UNAVAILABLE", (
        "a closure keeps its incident's kind"
    )
    assert log.open_incidents() == [], "an incident is still open after recovery"

    # The recovery is what the second slot published, not merely something it logged.
    assert len(backend.submissions) == 1, "the recovered slot did not reach the chain"
    assert len(TransparencyLog(workspace.transparency_log).verify()) == 1


def test_a_reporting_key_rollover_leaves_both_days_verifiable(tmp_path: Path) -> None:
    """A key is retired between two days, and neither day stops verifying.

    The keyring tests roll a key over in isolation, with no report in the log on either side
    of it. What an operator does is different: a daemon serves one day, the reporting key is
    rotated, and another daemon serves the next day over the same workspace. The risk that
    creates is that yesterday's report becomes unverifiable — the retired key is exactly the
    one nobody keeps by accident — so what this asserts is that the log needs *both* keys and
    that each entry names the key that actually signed it.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    retiring = Ed25519Signer.from_seed(bytes(range(32)))
    succeeding = Ed25519Signer.from_seed(bytes(range(32, 64)))
    assert retiring.kid != succeeding.kid

    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend, signer=retiring)
    run(service, produce)

    # This is the coordinated rollover path, not a mismatched-key incident. The manifest is
    # rolled to the succeeding public key before the second service is constructed; it keeps
    # the retiring key for historical verification while selecting the succeeding key for
    # the next day's successful publication.
    tomorrow = RETRIEVED_AT + timedelta(days=1)
    backend.manifest = rolled_over(
        backend.manifest,
        new_public_key=bytes.fromhex(succeeding.public_key_record()["public_key"]),
        at=tomorrow,
    )
    assert backend.manifest.active_key.kid == succeeding.kid
    assert backend.manifest.key(retiring.kid).state == "superseded"

    # A second daemon, the next day, over the same workspace and with the new key.
    later, _ = built(tmp_path, backend, now=tomorrow)
    _, produce_later = producer(store, later, backend, signer=succeeding)
    second = run(later, produce_later, now=tomorrow)
    assert second.completed == 1 and not second.failed, f"second daemon: {second}"
    assert IncidentLog(workspace.incidents).open_incidents() == [], (
        "the rollover left an incident open"
    )

    entries = TransparencyLog(workspace.transparency_log).verify()
    assert len(entries) == 2, "the rollover cost a day's report"
    assert [entry["signed_report"]["kid"] for entry in entries] == [
        retiring.kid,
        succeeding.kid,
    ], "the entries do not name the keys that signed them"

    # The manifest's own key set is what a reader is handed, and it verifies both days.
    trusted = verification_keys(backend.manifest)
    assert set(trusted) == {retiring.kid, succeeding.kid}
    for entry in entries:
        verify_signed_report(entry["signed_report"], trusted)

    # And the retired key is genuinely required: dropping it costs the earlier day.
    with pytest.raises(ValueError, match="unknown signing key"):
        verify_signed_report(
            entries[0]["signed_report"],
            {succeeding.kid: succeeding.public_key_record()},
        )


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
    """One day, one report, however many slots fall inside it.

    Two things were wrong here, and the second hid the first.

    The assertion was `sequences == sorted(set(sequences))`, which the defect it is named for
    satisfies perfectly: two reports about one day carry sequences 1 and 2, unique and
    ascending. It passed whether or not the day was republished, proving only that the
    registry hands out increasing numbers.

    And the scenario could not have republished a day in any case. `run_schedule` reads the
    wall clock once and derives later slots by adding the interval, so at the daily default
    these two slots were two different days — two publications, correctly. Fixing only the
    assertion would have produced a test that failed on correct behaviour.

    So the slots are put an hour apart, inside the day the fixture capture is about, and what
    is counted is that the chain was written once. The suppression is `_epoch_already_published`
    asking the registry, which is why this asserts on submissions rather than on the log
    alone: the point is that nothing was sent, not merely that nothing was recorded.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(store, service, backend)

    outcome = run(service, produce, runs=2, interval_seconds=3600.0)

    assert outcome.completed == 2, "both slots must run; suppression is not skipping"
    assert not outcome.failed, "a suppressed republication is not a failure"
    assert len(backend.submissions) == 1, (
        f"the same day reached the chain {len(backend.submissions)} times"
    )
    entries = TransparencyLog(workspace.transparency_log).verify()
    assert len(entries) == 1, f"{len(entries)} reports were logged for one day"


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
        store, service, backend, bundle_sink=write_bundle(workspace.bundles, 1952)
    )

    run(service, produce)

    written = sorted(workspace.bundles.glob("*.json"))
    assert len(written) == 1, f"expected exactly one bundle, got {written}"
    assert written[0].name == "eip155-1952-ustb-2026-08-14-1.json"
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


@pytest.mark.parametrize(
    "epoch_id",
    [
        "../escaped",
        "..",
        ".",
        "a/b",
        # A raw string, holding one backslash byte. Written as a non-raw literal the
        # first time, so Python read it as a backspace and the case tested nothing
        # about separators. It then survived one attempt to fix it, because the fix
        # was applied through another layer of escaping and inserted the control
        # character again. Checked at the byte level this time.
        r"a\b",
        "",
        "x" * 200,
        "with space",
    ],
)
def test_a_bundle_filename_cannot_escape_its_directory(
    tmp_path: Path, epoch_id: str
) -> None:
    """`epoch_id` reaches a path, and the report builder only checks it is non-empty text.

    So `../escaped` wrote outside the bundle directory. `epoch_id_for` cannot produce one,
    but `write_bundle` is a public reusable sink and its safety should not depend on which
    caller happens to be wired to it today.
    """
    sink = write_bundle(tmp_path / "bundles", 1952)
    bundle = {
        "signed_report": {
            "report": {
                "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
                "epoch_id": epoch_id,
                "sequence": 1,
            }
        }
    }

    with pytest.raises(EpochProductionError, match="not usable as a filename"):
        sink(bundle)

    assert not list(tmp_path.rglob("*.json")), "a file escaped the bundle directory"


def test_one_slot_reads_the_approval_ledger_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counted at the filesystem, not at the call I happened to write.

    The first version of this claim was checked by grepping for `ledger_bytes()` in the
    daemon, which found one call and proved nothing: `approved_control` re-read the ledger
    file once per control underneath, so eight controls meant nine reads of one file in a
    single slot. Reads that nobody counts are reads that can disagree.
    """
    import touchstone.approval as approval

    real = approval.Path.read_bytes
    real_text = approval.Path.read_text
    reads: list[str] = []

    def counting_bytes(self, *args, **kwargs):
        if self == approval.LEDGER:
            reads.append("bytes")
        return real(self, *args, **kwargs)

    def counting_text(self, *args, **kwargs):
        if self == approval.LEDGER:
            reads.append("text")
        return real_text(self, *args, **kwargs)

    monkeypatch.setattr(approval.Path, "read_bytes", counting_bytes)
    monkeypatch.setattr(approval.Path, "read_text", counting_text)

    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store,
        service,
        backend,
        bundle_sink=write_bundle(workspace.bundles, 1952),
        approval_ledger=None,
    )
    reads.clear()

    run(service, produce)

    assert reads == ["bytes"], (
        f"the approval ledger was read {len(reads)} times in one slot: {reads}. "
        "Every read is another chance for two of them to disagree."
    )


def test_an_injected_ledger_replaces_the_shipped_one_rather_than_joining_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override must be the *only* ledger in the slot, not one of two.

    Reading the shipped ledger anywhere underneath an injected one would rebuild the exact
    defect `make_producer` was written to close: controls resolved from one ledger while the
    report commits the digest of another, each check passing because each agrees with the
    read beside it. So the shipped file is made to raise rather than counted — a count can
    be satisfied by reading the wrong file once.

    The bundle is verified, not merely inspected, because carrying the right bytes and
    being checkable against them are different claims.
    """
    import touchstone.approval as approval

    real_bytes = approval.Path.read_bytes
    real_text = approval.Path.read_text

    def refuse(self, *args, **kwargs):
        if self == approval.LEDGER:
            raise AssertionError(
                "the shipped ledger was read during a slot given an injected one"
            )
        return real_bytes(self, *args, **kwargs)

    def refuse_text(self, *args, **kwargs):
        if self == approval.LEDGER:
            raise AssertionError(
                "the shipped ledger was read during a slot given an injected one"
            )
        return real_text(self, *args, **kwargs)

    monkeypatch.setattr(approval.Path, "read_bytes", refuse)
    monkeypatch.setattr(approval.Path, "read_text", refuse_text)

    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store, service, backend, bundle_sink=write_bundle(workspace.bundles, 1952)
    )

    run(service, produce)

    written = sorted(workspace.bundles.glob("*.json"))
    assert len(written) == 1, f"expected one bundle, got {written}"
    bundle = json.loads(written[0].read_text(encoding="utf-8"))

    assert bundle["approval_ledger"] == historical_ledger_bytes().decode("utf-8"), (
        "the bundle carries a different ledger than the one injected"
    )
    verify_bundle(bundle)


@pytest.mark.parametrize(
    "epoch_id", ["CON", "nul", "CON.foo", "NUL.", "COM1.log", "LPT1", "aux"]
)
def test_a_bundle_is_never_named_after_a_windows_device(
    tmp_path: Path, epoch_id: str
) -> None:
    """Windows resolves a device name before the extension, and case-insensitively.

    So `CON.foo`, `NUL.` and `COM1.log` are the console, the null device and a serial port
    while passing an allowlist of letters, digits and dots perfectly. Writing a bundle to one
    discards it or blocks, and the directory afterwards is indistinguishable from a slot that
    never produced. The check is on the epoch component, which is the stem Windows resolves
    once a dot is present; a rendered-filename check was tried first and deleted as dead,
    because appending `-{sequence}` cannot turn a non-device into a device.
    """
    sink = write_bundle(tmp_path / "bundles", 1952)
    bundle = {
        "signed_report": {
            "report": {
                "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
                "epoch_id": epoch_id,
                "sequence": 1,
            }
        }
    }

    with pytest.raises(EpochProductionError, match="[Ww]indows device"):
        sink(bundle)

    assert not list(tmp_path.rglob("*.json"))


def test_bundles_for_the_same_epoch_on_two_chains_never_share_a_name(
    tmp_path: Path,
) -> None:
    """The 2026-08-19 loss: two networks published the SAME report identity — one asset,
    one epoch, one sequence — and the sinks rendered one name. The publication chain is
    the only thing that distinguishes them, and it comes from the manifest: the report's
    own asset_key names the chain the asset lives on, identical everywhere."""
    report = {
        "signed_report": {
            "report": {
                "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
                "epoch_id": "ustb-2026-08-19",
                "sequence": 1,
            }
        }
    }
    write_bundle(tmp_path / "bundles", 1952)(report)
    write_bundle(tmp_path / "bundles", 196)(report)

    names = sorted(path.name for path in (tmp_path / "bundles").glob("*.json"))
    assert names == [
        "eip155-1952-ustb-2026-08-19-1.json",
        "eip155-196-ustb-2026-08-19-1.json",
    ]


def test_a_bundle_needs_a_publication_chain_to_take_a_name(tmp_path: Path) -> None:
    sink = write_bundle(tmp_path / "bundles", 0)
    bundle = {
        "signed_report": {
            "report": {
                "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
                "epoch_id": "ustb-2026-08-19",
                "sequence": 1,
            }
        }
    }

    with pytest.raises(EpochProductionError, match="publication chain id"):
        sink(bundle)

    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("sequence", ["1", "../1", 0, -1, 1.0, True, None])
def test_a_bundle_sequence_must_be_a_positive_integer(
    tmp_path: Path, sequence: object
) -> None:
    """It is interpolated into the filename, so a string could carry a separator through."""
    sink = write_bundle(tmp_path / "bundles", 1952)
    bundle = {
        "signed_report": {
            "report": {
                "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
                "epoch_id": "ustb-2026-08-14",
                "sequence": sequence,
            }
        }
    }

    with pytest.raises(EpochProductionError, match="positive integer"):
        sink(bundle)

    assert not list(tmp_path.rglob("*.json"))


def test_a_report_whose_bundle_cannot_be_verified_is_never_published(
    tmp_path: Path,
) -> None:
    """Building a bundle is not the same claim as a reader being able to verify it.

    `create_bundle` snapshots the envelope and checks the fields it derives; signature
    verification lives in `verify_bundle`. So it will happily return a bundle whose signature
    does not check out against the key it carries — and the mutation harness caught that the
    earlier regression here could not tell the difference, because it verified the bundle
    itself after reading it back rather than proving the daemon had.

    Driven by a signer that publishes a *different* key's record than it signs with, so
    `create_bundle` returns happily and the verifier refuses. Precisely: it is refused because
    the published key record's `kid` does not match the envelope's (`signing.py`), not by a
    failed signature check — it never gets that far. So this proves the verifier is invoked
    before the sink, which is the mutant it exists to kill. It does not prove signature
    rejection, and it says nothing about the recovery path.
    """
    from touchstone.verify import VerificationError

    class MismatchedKeySigner:
        """Signs with one key, publishes another. Otherwise a real signer."""

        def __init__(self) -> None:
            self._signing = Ed25519Signer.from_seed(bytes(range(32)))
            self._published = Ed25519Signer.from_seed(bytes(range(32, 64)))

        @property
        def kid(self) -> str:
            return self._signing.kid

        def sign_report(self, report: object) -> dict:
            return self._signing.sign_report(report)

        def public_key_record(self) -> dict:
            return self._published.public_key_record()

    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    produce = make_producer(
        store=store,
        signer=MismatchedKeySigner(),
        next_sequence=lambda: backend.latest_sequence(asset_key_bytes(ASSET)) + 1,
        previous_state=lambda on: AssetState.UNVERIFIABLE,
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        bundle_sink=write_bundle(workspace.bundles, 1952),
        approval_ledger=historical_ledger_bytes(),
    )

    with pytest.raises(VerificationError):
        produce(RETRIEVED_AT)

    assert not backend.submissions, "an unverifiable report reached the chain"
    assert not list(workspace.bundles.glob("*.json")), (
        "an unverifiable bundle was persisted"
    )


def _pending(signed_report: dict):
    """The one attribute `require_verifying_bundle` reads off a pending operation."""

    class Operation:
        def __init__(self, report: dict) -> None:
            self.signed_report = report

    return Operation(signed_report)


def test_recovery_refuses_to_republish_a_report_with_no_bundle(tmp_path: Path) -> None:
    """A fresh slot cannot publish without a verifying bundle. Recovery could.

    `OperationsStore.resolve()` republishes stored signed bytes and never consults a bundle,
    so a pending operation written before bundles existed — or one whose file was deleted —
    would go on chain with nothing a reader could check it against, permanently, correctable
    only by a new report. An earlier docstring claimed publication and verifiability could not
    come apart; this is the path that disproved it.
    """
    guard = require_verifying_bundle(tmp_path / "bundles", 1952)
    operation = _pending(
        {
            "report": {
                "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
                "epoch_id": "ustb-2026-08-14",
                "sequence": 1,
            },
            "signature": "x",
        }
    )

    with pytest.raises(EpochProductionError, match="no readable verification bundle"):
        guard(operation)


def test_recovery_refuses_a_bundle_that_describes_a_different_report(
    tmp_path: Path,
) -> None:
    """The more dangerous failure, because everything about it looks correct.

    A bundle that verifies in isolation proves only that *some* report was signed properly.
    Republishing a different report behind it would hand a reader a valid bundle for the
    wrong thing.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store, service, backend, bundle_sink=write_bundle(workspace.bundles, 1952)
    )
    run(service, produce)

    guard = require_verifying_bundle(workspace.bundles, 1952)
    written = next(iter(workspace.bundles.glob("*.json")))
    genuine = json.loads(written.read_text(encoding="utf-8"))["signed_report"]
    # Same epoch and sequence, so it resolves to the same file. Different report.
    impostor = deepcopy(genuine)
    impostor["report"]["state"] = "STALE"

    guard(_pending(genuine))
    with pytest.raises(EpochProductionError, match="describes a different report"):
        guard(_pending(impostor))


def test_recovery_refuses_a_bundle_that_no_longer_verifies(tmp_path: Path) -> None:
    """Present is not the same as intact. A truncated or edited bundle must not pass."""
    from touchstone.verify import VerificationError

    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store, service, backend, bundle_sink=write_bundle(workspace.bundles, 1952)
    )
    run(service, produce)

    written = next(iter(workspace.bundles.glob("*.json")))
    bundle = json.loads(written.read_text(encoding="utf-8"))
    genuine = deepcopy(bundle["signed_report"])
    bundle["approval_ledger"] = '{"approved": [], "declined": []}'
    written.write_text(json.dumps(bundle), encoding="utf-8")

    guard = require_verifying_bundle(workspace.bundles, 1952)
    with pytest.raises(VerificationError):
        guard(_pending(genuine))


def test_recovery_reads_a_bundle_the_way_a_reader_would(tmp_path: Path) -> None:
    """A duplicate key is invisible to `json.loads` and fatal to the real verifier.

    Ordinary decoding keeps the last value, so a bundle edited to carry a duplicate top-level
    key parsed into a perfectly valid mapping and passed the guard — while an offline reader
    handed the same *file* refused it, because the project's own parser rejects duplicate
    keys, non-finite numbers, and inputs past its size and depth limits. The guard exists to
    answer whether a reader can verify this file, so reading it more permissively than the
    reader does defeats the point.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store, service, backend, bundle_sink=write_bundle(workspace.bundles, 1952)
    )
    run(service, produce)

    written = next(iter(workspace.bundles.glob("*.json")))
    text = written.read_text(encoding="utf-8")
    genuine = json.loads(text)["signed_report"]
    # A duplicate "version" key. Plain json.loads keeps the last one and sees a valid bundle.
    duplicated = text.replace(
        "{\n", '{\n  "version": "touchstone.verification-bundle.v4",\n', 1
    )
    assert duplicated.count('"version"') > text.count('"version"')
    written.write_text(duplicated, encoding="utf-8")

    guard = require_verifying_bundle(workspace.bundles, 1952)
    with pytest.raises(EpochProductionError, match="not strictly readable JSON"):
        guard(_pending(genuine))


def test_a_first_publication_does_not_claim_to_have_reconfirmed_anything(
    tmp_path: Path,
) -> None:
    """Sequence 1 has nothing behind it to reconfirm.

    The producer stamped `RECONFIRMED` on every slot, so USTB sequence 1 went onto two public
    chains asserting it had reconfirmed a state that had never been observed. Those bytes are
    signed and can only be superseded, never edited, which is why this pins the producer.

    The previous state cannot distinguish the two cases: an empty operations store reports
    UNVERIFIABLE, and so does a genuine reconfirmation of an asset that is still unverified.
    The sequence can — 1 is the first report for this asset on this registry.
    """
    store = seeded_store(tmp_path)
    backend = FakeBackend()
    service, workspace = built(tmp_path, backend)
    _, produce = producer(
        store, service, backend, bundle_sink=write_bundle(workspace.bundles, 1952)
    )

    run(service, produce)

    written = sorted(workspace.bundles.glob("*.json"))
    assert len(written) == 1, f"expected one bundle, got {written}"
    report = json.loads(written[0].read_text(encoding="utf-8"))["signed_report"]["report"]

    assert report["sequence"] == 1
    assert report["state_transition"]["event"] == "FIRST_OBSERVATION", (
        "a first publication reported "
        f"{report['state_transition']['event']!r}, which claims a history it does not have"
    )
