from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from touchstone.signing import canonical_json_bytes, strict_json_loads


ROOT = Path(__file__).parents[1]
SOURCE_BUNDLE = (
    ROOT / "site2" / "data" / "eip155-196-ustb-2026-08-22-6.json"
)
TX_HASH = "0xf99f42b6703d7369bf8927038f95a1a44cf2d6761a991c404f2ee2480757f1a2"
BLOCK_HASH = "0xfa8691c3423ed79b4ee1d4128e283c77d3c3701af96846586df23d5d7d06f941"
BLOCK_NUMBER = 68_599_159


def _module():
    spec = importlib.util.spec_from_file_location(
        "sync_publications", ROOT / "scripts" / "sync_publications.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(bundle: dict[str, object]) -> dict[str, object]:
    signed = bundle["signed_report"]
    assert isinstance(signed, dict)
    report = signed["report"]
    assert isinstance(report, dict)
    entry: dict[str, object] = {
        "index": 1,
        "prev_entry_hash": None,
        "publication": {
            "receipt": {
                "block_hash": BLOCK_HASH,
                "block_number": BLOCK_NUMBER,
                "effective_gas_price": 20_000_001,
                "gas_used": 239_876,
                "status": 1,
            },
            "transaction_hash": TX_HASH,
        },
        "report_sha256": hashlib.sha256(canonical_json_bytes(report)).hexdigest(),
        "signed_report": signed,
        "supersedes": None,
        "version": "touchstone.transparency-entry.v1",
    }
    entry["entry_hash"] = hashlib.sha256(canonical_json_bytes(entry)).hexdigest()
    return entry


def _source(tmp_path: Path) -> tuple[Path, bytes]:
    raw = SOURCE_BUNDLE.read_bytes()
    bundle = strict_json_loads(raw)
    assert isinstance(bundle, dict)
    workspace = tmp_path / "source" / "ustb"
    source_bundle = workspace / "bundles" / SOURCE_BUNDLE.name
    source_bundle.parent.mkdir(parents=True)
    source_bundle.write_bytes(raw)
    (workspace / "transparency.jsonl").write_bytes(
        canonical_json_bytes(_entry(bundle)) + b"\n"
    )
    return workspace.parent, raw


def _documents(tmp_path: Path) -> tuple[Path, Path, Path]:
    retained = tmp_path / "retained"
    retained.mkdir()
    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps({"reports_published": 0, "confirmed_reports": 0, "reports": []})
        + "\n",
        encoding="utf-8",
    )
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "counts": {
                    "reports_published": "0",
                    "confirmed_reports": "0",
                    "bundles_downloadable": "0",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return retained, stats, facts


class FakeQuorum:
    def __init__(
        self,
        chain_id: int,
        receipt: dict[str, object] | None,
        transaction: dict[str, object] | None,
        transaction_error: Exception | None = None,
    ) -> None:
        self.chain_id = chain_id
        self.receipt = receipt
        self.transaction = transaction
        self.transaction_error = transaction_error

    def call(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(self.chain_id)
        assert params == [TX_HASH]
        if method == "eth_getTransactionReceipt":
            return self.receipt
        assert method == "eth_getTransactionByHash"
        if self.transaction_error is not None:
            raise self.transaction_error
        return self.transaction


def _factory(
    module,
    *,
    block_hash: str = BLOCK_HASH,
    transaction_to: str = "0x1111111111111111111111111111111111111111",
    transaction_error: Exception | None = None,
):
    bundle = strict_json_loads(SOURCE_BUNDLE.read_bytes())
    assert isinstance(bundle, dict)
    signed = bundle["signed_report"]
    assert isinstance(signed, dict)
    report = signed["report"]
    assert isinstance(report, dict)
    common = (
        module.asset_key_bytes(report["asset_key"]),
        bytes.fromhex(report["control_set_root"]),
        bytes.fromhex(report["evidence_root"]),
        module.epoch_key_bytes(report["epoch_id"]),
        module._STATUS_NAMES.index(report["state"]),
        module._unix_timestamp(report["observed_at"], "observed_at"),
        module._unix_timestamp(report["valid_until"], "valid_until"),
        report["sequence"],
        "ipfs://report",
    )
    calldata = "0x" + (
        module._PUBLISH_SELECTOR + module.Web3().codec.encode(module._PUBLISH_TYPES, common)
    ).hex()
    receipt = {
        "transactionHash": TX_HASH,
        "blockHash": block_hash,
        "blockNumber": hex(BLOCK_NUMBER),
        "effectiveGasPrice": hex(20_000_001),
        "gasUsed": hex(239_876),
        "status": "0x1",
    }
    transaction = {"hash": TX_HASH, "to": transaction_to, "input": calldata}

    def build(endpoints: tuple[str, ...]):
        assert len(endpoints) == 2
        if endpoints[0] == "https://mainnet-one.example":
            return FakeQuorum(196, receipt, transaction, transaction_error)
        return FakeQuorum(1952, None, None)

    networks = (
        module.Network(
            "X Layer mainnet",
            196,
            "0x1111111111111111111111111111111111111111",
            ("https://mainnet-one.example", "https://mainnet-two.example"),
        ),
        module.Network(
            "X Layer testnet",
            1952,
            "0x2222222222222222222222222222222222222222",
            ("https://testnet-one.example", "https://testnet-two.example"),
        ),
    )
    return networks, build


def test_check_reports_the_retained_bundle_with_no_publication_row(
    tmp_path: Path,
) -> None:
    sync = _module()
    retained, stats, _ = _documents(tmp_path)
    (retained / SOURCE_BUNDLE.name).write_bytes(SOURCE_BUNDLE.read_bytes())

    with pytest.raises(sync.PublicationSyncError, match=SOURCE_BUNDLE.name):
        sync.check_retained_publications(retained, stats)


def test_sync_derives_row_and_counts_while_retaining_exact_bytes(tmp_path: Path) -> None:
    sync = _module()
    source, raw = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    networks, factory = _factory(sync)

    rows = sync.sync_publications(
        source=source,
        bundles=retained,
        stats=stats,
        facts=facts,
        networks=networks,
        quorum_factory=factory,
    )

    assert rows == [
        {
            "chain_id": 196,
            "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
            "note": "xlayer-mainnet-v1",
            "epoch_id": "ustb-2026-08-22",
            "sequence": 6,
            "state": "CONFIRMED",
            "policy": None,
            "correction_of": None,
            "transaction_hash": TX_HASH,
            "block": BLOCK_NUMBER,
            "observed_at": "2026-08-22T03:01:26.624074Z",
        }
    ]
    assert (retained / SOURCE_BUNDLE.name).read_bytes() == raw
    stats_document = json.loads(stats.read_text(encoding="utf-8"))
    assert stats_document["reports"] == rows
    assert stats_document["reports_published"] == 1
    assert stats_document["confirmed_reports"] == 1
    assert json.loads(facts.read_text(encoding="utf-8"))["counts"] == {
        "reports_published": "1",
        "confirmed_reports": "1",
        "bundles_downloadable": "1",
    }


def test_sync_is_idempotent_and_never_rewrites_an_existing_row(tmp_path: Path) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    networks, factory = _factory(sync)
    first = sync.sync_publications(
        source=source,
        bundles=retained,
        stats=stats,
        facts=facts,
        networks=networks,
        quorum_factory=factory,
    )
    before = stats.read_bytes()

    assert (
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=lambda endpoints: pytest.fail(
                f"idempotent sync queried {endpoints}"
            ),
        )
        == []
    )
    assert first
    assert stats.read_bytes() == before


def test_existing_report_under_legacy_name_is_not_duplicated(tmp_path: Path) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    networks, factory = _factory(sync)
    sync.sync_publications(
        source=source,
        bundles=retained,
        stats=stats,
        facts=facts,
        networks=networks,
        quorum_factory=factory,
    )
    canonical = retained / SOURCE_BUNDLE.name
    legacy = retained / "ustb-2026-08-22-6.json"
    canonical.rename(legacy)

    assert (
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=lambda endpoints: pytest.fail(
                f"existing publication queried {endpoints}"
            ),
        )
        == []
    )
    assert not canonical.exists()
    assert legacy.exists()


def test_conflicting_existing_row_refuses_without_copying_bundle(
    tmp_path: Path,
) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    conflict = {
        "chain_id": 196,
        "asset_key": "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e",
        "note": "xlayer-mainnet-v1",
        "epoch_id": "ustb-2026-08-22",
        "sequence": 6,
        "state": "STALE",
        "policy": None,
        "correction_of": None,
        "transaction_hash": TX_HASH,
        "block": BLOCK_NUMBER,
        "observed_at": "2026-08-22T03:01:26.624074Z",
    }
    stats.write_text(
        json.dumps(
            {"reports_published": 1, "confirmed_reports": 0, "reports": [conflict]}
        )
        + "\n",
        encoding="utf-8",
    )
    networks, factory = _factory(sync)

    with pytest.raises(sync.PublicationSyncError, match="refuses to alter existing row"):
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=factory,
        )

    assert list(retained.iterdir()) == []


def test_receipt_mismatch_refuses_before_any_file_changes(tmp_path: Path) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    before_stats = stats.read_bytes()
    before_facts = facts.read_bytes()
    networks, factory = _factory(sync, block_hash="0x" + "00" * 32)

    with pytest.raises(sync.PublicationSyncError, match="receipt"):
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=factory,
        )

    assert list(retained.iterdir()) == []
    assert stats.read_bytes() == before_stats
    assert facts.read_bytes() == before_facts


def test_unrelated_successful_transaction_is_not_accepted(tmp_path: Path) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    networks, factory = _factory(
        sync, transaction_to="0x3333333333333333333333333333333333333333"
    )

    with pytest.raises(sync.PublicationSyncError, match="wrong destination"):
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=factory,
        )

    assert list(retained.iterdir()) == []


def test_transaction_quorum_failure_has_a_sync_error(tmp_path: Path) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    retained, stats, facts = _documents(tmp_path)
    networks, factory = _factory(
        sync, transaction_error=RuntimeError("provider disagreement")
    )

    with pytest.raises(sync.PublicationSyncError, match="dual-RPC transaction check"):
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=factory,
        )


def test_cli_reports_a_concurrent_sync_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sync = _module()

    def unavailable(path: Path):
        del path
        raise sync.LockUnavailable("another live process holds the sync lock")

    monkeypatch.setattr(sync, "exclusive_lock", unavailable)

    assert sync.main(["--from", str(tmp_path)]) == 1
    assert "PUBLICATION SYNC FAIL: another live process" in capsys.readouterr().err


def test_replacement_failure_restores_every_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = _module()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_bytes(b"first-old")
    second.write_bytes(b"second-old")
    replace = sync.os.replace
    calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        replace(source, target)

    monkeypatch.setattr(sync.os, "replace", fail_second)

    with pytest.raises(OSError, match="injected replacement failure"):
        sync._replace_all(((first, b"first-new"), (second, b"second-new")))

    assert first.read_bytes() == b"first-old"
    assert second.read_bytes() == b"second-old"


def test_unverified_source_bundle_is_never_retained(tmp_path: Path) -> None:
    sync = _module()
    source, _ = _source(tmp_path)
    source_bundle = next(source.rglob("*.json"))
    source_bundle.write_bytes(source_bundle.read_bytes()[:-1] + b"x")
    retained, stats, facts = _documents(tmp_path)
    before_stats = stats.read_bytes()
    networks, factory = _factory(sync)

    with pytest.raises(sync.PublicationSyncError, match="does not verify offline"):
        sync.sync_publications(
            source=source,
            bundles=retained,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=factory,
        )

    assert list(retained.iterdir()) == []
    assert stats.read_bytes() == before_stats
