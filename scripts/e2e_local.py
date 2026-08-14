"""Run the complete Touchstone loop against a local Hardhat node only."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from web3 import Web3


ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.controls import AssetState, OperationalEvent  # noqa: E402
from touchstone.compiler import (  # noqa: E402
    CompilationStatus,
    DeterministicFixtureProvider,
    compile_evidence,
)
from touchstone.epoch import FixtureTransport, run_ustb_epoch  # noqa: E402
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.evaluate import default_ustb_controls  # noqa: E402
from touchstone.publish import PublisherClient, Web3RegistryBackend  # noqa: E402
from touchstone.report import (  # noqa: E402
    build_observation_report,
    evidence_references,
)
from touchstone.signing import Ed25519Signer  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402
from touchstone.verify import create_bundle, verify_bundle  # noqa: E402
from touchstone.sources import USTB_SOURCES  # noqa: E402


CONTRACTS = ROOT / "contracts"
RPC_URL = "http://127.0.0.1:8545"


def run_e2e(*, rpc_url: str = RPC_URL) -> dict[str, object]:
    """Deploy, evaluate, sign, verify, publish, age, correct, and read history."""
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise ConnectionError(f"no local Hardhat node at {rpc_url}")
    accounts = web3.eth.accounts
    if len(accounts) < 2:
        raise RuntimeError("local node must expose at least two unlocked accounts")
    owner, publisher = accounts[:2]
    registry_artifact = _artifact("TouchstoneRegistry")
    gate_artifact = _artifact("AssetGate")

    registry_factory = web3.eth.contract(
        abi=registry_artifact["abi"], bytecode=registry_artifact["bytecode"]
    )
    registry_receipt = web3.eth.wait_for_transaction_receipt(
        registry_factory.constructor(web3.eth.chain_id).transact({"from": owner})
    )
    registry = web3.eth.contract(
        address=registry_receipt.contractAddress, abi=registry_artifact["abi"]
    )
    web3.eth.wait_for_transaction_receipt(
        registry.functions.authorizePublisher(publisher).transact({"from": owner})
    )

    with tempfile.TemporaryDirectory(prefix="touchstone-e2e-") as temporary:
        workspace = Path(temporary)
        confirmed_at = datetime(2026, 8, 13, 14, 16, 17, tzinfo=timezone.utc)
        retrieved_at = datetime(2026, 8, 14, 17, 8, 12, tzinfo=timezone.utc)
        evidence_store = EvidenceStore(workspace / "evidence")
        run_ustb_epoch(
            transport=FixtureTransport(ROOT / "fixtures", date(2026, 8, 13)),
            store=evidence_store,
            now=date(2026, 8, 13),
            retrieved_at=confirmed_at,
        )
        epoch = run_ustb_epoch(
            transport=FixtureTransport(ROOT / "fixtures", date(2026, 8, 14)),
            store=evidence_store,
            now=date(2026, 8, 14),
            retrieved_at=retrieved_at,
        )
        controls = default_ustb_controls()
        compiler_provenance = _compile_provenance(
            controls, epoch, evidence_store, retrieved_at
        )
        signer = Ed25519Signer.from_seed(bytes(range(32)))
        report = build_observation_report(
            epoch,
            controls,
            epoch_id="ustb-fixture-2026-08-14",
            sequence=1,
            publisher_kid=signer.kid,
            compiler_provenance_digests=compiler_provenance,
        )
        signed = signer.sign_report(report)
        evidence_digests = evidence_references(epoch)
        bundle = create_bundle(
            signed,
            signer.public_key_record(),
            controls,
            evidence_digests,
        )
        verify_bundle(bundle)

        gate_factory = web3.eth.contract(
            abi=gate_artifact["abi"], bytecode=gate_artifact["bytecode"]
        )
        gate_receipt = web3.eth.wait_for_transaction_receipt(
            gate_factory.constructor(
                registry_receipt.contractAddress,
                1,
                86_400,
                publisher,
                bytes.fromhex(report["control_set_root"]),
            ).transact({"from": owner})
        )
        gate = web3.eth.contract(
            address=gate_receipt.contractAddress, abi=gate_artifact["abi"]
        )
        log = TransparencyLog(workspace / "transparency.jsonl")
        client = PublisherClient(
            Web3RegistryBackend(
                registry_receipt.contractAddress,
                publisher,
                rpc_url=rpc_url,
            ),
            log,
            workspace / "pending.json",
        )
        first = client.publish(
            signed,
            published_key=signer.public_key_record(),
            report_uri="urn:touchstone:ustb:fixture:1",
        )
        asset_key = Web3.keccak(text=report["asset_key"])
        allowed, reason = gate.functions.check(asset_key).call()
        if not allowed or reason != "allowed":
            raise AssertionError(f"AssetGate did not accept report: {reason}")

        current_timestamp = web3.eth.get_block("latest")["timestamp"]
        valid_until = int(
            datetime.fromisoformat(
                report["valid_until"].replace("Z", "+00:00")
            ).timestamp()
        )
        web3.provider.make_request(
            "evm_increaseTime", [valid_until - current_timestamp + 1]
        )
        web3.provider.make_request("evm_mine", [])
        allowed, reason = gate.functions.check(asset_key).call()
        if allowed or reason != "observation too old":
            raise AssertionError(f"AssetGate staleness mismatch: {reason}")

        correction = build_observation_report(
            epoch,
            controls,
            epoch_id="ustb-fixture-2026-08-14-correction",
            sequence=2,
            publisher_kid=signer.kid,
            compiler_provenance_digests=compiler_provenance,
            previous_state=AssetState.CONFIRMED,
            event=OperationalEvent.CORRECTION_PUBLISHED,
            correction_of=1,
        )
        signed_correction = signer.sign_report(correction)
        verify_bundle(
            create_bundle(
                signed_correction,
                signer.public_key_record(),
                controls,
                evidence_digests,
            )
        )
        second = client.publish_correction(
            signed_correction,
            published_key=signer.public_key_record(),
            report_uri="urn:touchstone:ustb:fixture:2",
        )
        historical = registry.functions.getReport(asset_key, 1).call()
        if historical[6] != 1 or historical[7] != "urn:touchstone:ustb:fixture:1":
            raise AssertionError("correction changed the prior report")
        if registry.functions.correctionTarget(asset_key, 2).call() != 1:
            raise AssertionError("correction target was not recorded")

        return {
            "asset_gate_after_age": reason,
            "asset_gate_initial": "allowed",
            "correction_transaction": second.transaction_hash,
            "first_transaction": first.transaction_hash,
            "historical_sequence": historical[6],
            "log_entries": len(log.verify()),
            "registry": registry_receipt.contractAddress,
        }


def _artifact(contract_name: str) -> dict[str, object]:
    path = (
        CONTRACTS
        / "artifacts"
        / "contracts"
        / f"{contract_name}.sol"
        / f"{contract_name}.json"
    )
    if not path.is_file():
        raise FileNotFoundError(
            f"missing {contract_name} artifact; run npm run compile in contracts"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _compile_provenance(controls, epoch, store, retrieved_at) -> list[str]:
    evidence_by_source = {
        source.source_id: source.evidence_sha256 for source in epoch.sources
    }
    digests = []
    for manifest in USTB_SOURCES:
        proposals = []
        for control in controls:
            if control.source_id == manifest.source_id:
                proposal = control.to_mapping()
                proposal["approval_state"] = "proposed"
                proposals.append(proposal)
        result = compile_evidence(
            DeterministicFixtureProvider(
                json.dumps({"controls": proposals}, separators=(",", ":"))
            ),
            evidence_sha256=evidence_by_source[manifest.source_id],
            source_manifest=manifest,
            store=store,
            retrieved_at=retrieved_at,
        )
        if any(
            outcome.status is not CompilationStatus.ACCEPTED
            for outcome in result.outcomes
        ):
            raise AssertionError(
                "fixture control compilation did not produce accepted provenance"
            )
        digests.append(result.compilation_sha256)
    return digests


def _compile_contracts() -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        raise RuntimeError("npx is not installed")
    subprocess.run([npx, "hardhat", "compile"], cwd=CONTRACTS, check=True)


def _start_node() -> tuple[subprocess.Popen[bytes], Path]:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        raise RuntimeError("npx is not installed")
    descriptor, name = tempfile.mkstemp(prefix="touchstone-hardhat-", suffix=".log")
    os.close(descriptor)
    output = Path(name)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        with output.open("wb") as stream:
            process = subprocess.Popen(
                [npx, "hardhat", "node", "--hostname", "127.0.0.1", "--port", "8545"],
                cwd=CONTRACTS,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    for _ in range(80):
        if process.poll() is not None:
            message = output.read_text(encoding="utf-8", errors="replace")
            output.unlink(missing_ok=True)
            raise RuntimeError(message)
        if Web3(Web3.HTTPProvider(RPC_URL)).is_connected():
            return process, output
        time.sleep(0.25)
    _stop_node(process)
    output.unlink(missing_ok=True)
    raise RuntimeError("Hardhat node did not become ready")


def _stop_node(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-running-node",
        action="store_true",
        help="require an already-running node instead of starting Hardhat",
    )
    args = parser.parse_args(argv)
    process = None
    output = None
    try:
        _compile_contracts()
        if (
            not args.use_running_node
            and not Web3(Web3.HTTPProvider(RPC_URL)).is_connected()
        ):
            process, output = _start_node()
        result = run_e2e()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as error:
        print(f"E2E FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if process is not None:
            _stop_node(process)
        if output is not None:
            output.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
