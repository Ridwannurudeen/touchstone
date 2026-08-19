import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
import sys

from historical_pack import historical_controls, historical_ledger_bytes
from touchstone.approval import ledger_from_bytes
from touchstone.controls import AssetState
from touchstone.epoch import FixtureTransport, run_ustb_epoch_reports
from touchstone.evidence import EvidenceStore
from touchstone.evaluate import default_ustb_controls
from touchstone.policy import MANIFEST_VERSION, POLICIES, Policy, load_all
from touchstone.signing import Ed25519Signer
from touchstone.ustb_daemon import ProducedReports, make_producer
from touchstone.verify import verify_bundle

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from run_service import BatchService, Service  # noqa: E402
from test_publish import FakeBackend  # noqa: E402


FIXTURES = Path(__file__).parents[1] / "fixtures"
APPROVALS = Path(__file__).parents[1] / "data" / "compilations" / "APPROVALS.json"
CONFIRMED_AT = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
RETRIEVED_AT = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)


def _manifest(control_id: str) -> bytes:
    return (
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "policy_id": "freshness-only",
                "policy_version": 1,
                "asset_key": historical_controls()[0].asset_key,
                "title": "Freshness only",
                "consumer_question": "Is the issuer publication current?",
                "controls": [control_id],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        .encode("utf-8")
    )


def _policy(control_id: str, raw: bytes) -> Policy:
    controls = default_ustb_controls(ledger_from_bytes(APPROVALS.read_bytes()))
    return Policy(
        policy_id="freshness-only",
        version=1,
        asset_key=controls[0].asset_key,
        title="Freshness only",
        consumer_question="Is the issuer publication current?",
        control_ids=(control_id,),
        digest=hashlib.sha256(raw).hexdigest(),
    )


def test_one_capture_produces_asset_and_policy_reports_with_shared_evidence(
    tmp_path: Path,
) -> None:
    controls = default_ustb_controls(ledger_from_bytes(APPROVALS.read_bytes()))
    policies = load_all(POLICIES, approved=controls)
    store = EvidenceStore(tmp_path)
    run_ustb_epoch_reports(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
        controls=controls,
    )
    transport = FixtureTransport(FIXTURES, date(2026, 8, 14))
    epochs = run_ustb_epoch_reports(
        transport=transport,
        store=store,
        now=date(2026, 8, 14),
        retrieved_at=RETRIEVED_AT,
        controls=controls,
        policies=policies,
    )

    assert len(epochs) == 3
    assert transport.calls == [source.source_url for source in epochs[0].sources]
    from touchstone.report import evidence_references, evidence_root

    roots = {evidence_root(evidence_references(epoch)) for epoch in epochs}
    assert len(roots) == 1
    assert [len(epoch.evaluations) for epoch in epochs[1:]] == [4, 2]


def test_policy_producer_signs_and_verifies_each_report_once(tmp_path: Path) -> None:
    controls = historical_controls()
    policy_raw = _manifest("ustb-nav-date-freshness")
    policy = _policy("ustb-nav-date-freshness", policy_raw)
    store = EvidenceStore(tmp_path / "evidence")
    from touchstone.epoch import run_ustb_epoch

    run_ustb_epoch(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=store,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
        controls=controls,
    )
    bundles = []
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    produce = make_producer(
        store=store,
        signer=signer,
        next_sequence=lambda: 1,
        next_sequence_for=lambda _key: 1,
        previous_state=lambda _on: AssetState.UNVERIFIABLE,
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        approval_ledger=historical_ledger_bytes(),
        policies=(policy,),
        policy_manifests={(policy.policy_id, policy.version): policy_raw},
        bundle_sink=bundles.append,
    )

    result = produce(RETRIEVED_AT)

    assert isinstance(result, ProducedReports)
    assert [item["report"]["asset_key"] for item in result.reports] == [
        controls[0].asset_key,
        policy.key,
    ]
    assert len(bundles) == 2
    for bundle in bundles:
        verify_bundle(bundle)


def test_batch_service_publishes_each_key_from_one_capture(tmp_path: Path) -> None:
    from touchstone.controls import AssetState
    from touchstone.incidents import IncidentLog
    from touchstone.operations import OperationsStore
    from touchstone.publish import PublisherClient
    from touchstone.translog import TransparencyLog
    from touchstone.ustb_daemon import (
        asset_key_bytes,
        epoch_id_for,
        report_uri,
        require_verifying_bundle,
        write_bundle,
    )
    from touchstone.workspace import Workspace

    controls = historical_controls()
    policy_raw = _manifest("ustb-nav-date-freshness")
    policy = _policy("ustb-nav-date-freshness", policy_raw)
    evidence = EvidenceStore(tmp_path / "base" / "evidence")
    run_ustb_epoch_reports(
        transport=FixtureTransport(FIXTURES, date(2026, 8, 13)),
        store=evidence,
        now=date(2026, 8, 13),
        retrieved_at=CONFIRMED_AT,
        controls=controls,
    )

    backend = FakeBackend()
    base_workspace = Workspace(tmp_path / "base")
    policy_workspace = Workspace(tmp_path / "policy")
    services = tuple(
        Service(
            PublisherClient(
                backend,
                TransparencyLog(workspace.transparency_log),
                workspace.pending_journal,
            ),
            OperationsStore(workspace.operations),
            IncidentLog(workspace.incidents),
            asset_key=key,
            lock_path=workspace.lock,
            sleep=lambda _seconds: None,
            now=lambda: RETRIEVED_AT,
            before_publish=require_verifying_bundle(base_workspace.bundles),
        )
        for workspace, key in (
            (base_workspace, controls[0].asset_key),
            (policy_workspace, policy.key),
        )
    )
    service = BatchService(services)
    signer = Ed25519Signer.from_seed(bytes(range(32)))
    produce = make_producer(
        store=evidence,
        signer=signer,
        next_sequence=lambda: backend.latest_sequence(
            asset_key_bytes(controls[0].asset_key)
        )
        + 1,
        next_sequence_for=lambda key: backend.latest_sequence(asset_key_bytes(key)) + 1,
        previous_state=lambda _on: AssetState.UNVERIFIABLE,
        previous_state_for=lambda _key, _on: AssetState.UNVERIFIABLE,
        transport=FixtureTransport(FIXTURES, date(2026, 8, 14)),
        approval_ledger=historical_ledger_bytes(),
        policies=(policy,),
        policy_manifests={(policy.policy_id, policy.version): policy_raw},
        bundle_sink=write_bundle(base_workspace.bundles),
    )

    outcome = service.run_slot(
        RETRIEVED_AT,
        produce,
        report_uri=report_uri,
        epoch_of=epoch_id_for,
    )

    assert outcome.published
    assert len(backend.submissions) == 2
    assert backend.latest_sequence(asset_key_bytes(controls[0].asset_key)) == 1
    assert backend.latest_sequence(asset_key_bytes(policy.key)) == 1
    assert len(list(base_workspace.bundles.glob("*.json"))) == 2
