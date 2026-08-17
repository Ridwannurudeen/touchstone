"""Run the complete Touchstone loop against a local Hardhat node only."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError


ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.approval import ledger_bytes, ledger_from_bytes  # noqa: E402
from touchstone.controls import AssetState, OperationalEvent  # noqa: E402
from touchstone.deployment import (  # noqa: E402
    DeploymentManifest,
    runtime_bytecode_sha256,
)
from touchstone.epoch import FixtureTransport, run_ustb_epoch  # noqa: E402
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.evaluate import default_ustb_controls  # noqa: E402
from touchstone.keyring import PublisherKey  # noqa: E402
from touchstone.publish import (  # noqa: E402
    PublisherClient,
    SignedRegistryBackend,
    epoch_key_bytes,
)
from touchstone.report import (  # noqa: E402
    build_observation_report,
    evidence_references,
)
from touchstone.signing import Ed25519Signer  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402
from touchstone.verify import create_bundle, verify_bundle  # noqa: E402


CONTRACTS = ROOT / "contracts"
RPC_URL = "http://127.0.0.1:8545"

# Hardhat's published development mnemonic. Its keys are known to everyone, which is the
# point: this run signs its own transactions locally, exactly as production does, without
# a secret existing anywhere. Deriving the key rather than pasting one means the run fails
# loudly if the node ever stops using these accounts, instead of signing as a stranger.
DEV_MNEMONIC = "test test test test test test test test test test test junk"
PUBLISHER_ACCOUNT_INDEX = 1


def run_e2e(*, rpc_url: str = RPC_URL) -> dict[str, object]:
    """Deploy, evaluate, sign, verify, publish, age, correct, and read history."""
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise ConnectionError(f"no local Hardhat node at {rpc_url}")
    accounts = web3.eth.accounts
    if len(accounts) < 3:
        raise RuntimeError("local node must expose at least three accounts")
    owner, publisher, operations = accounts[:3]
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
        # One read of the approval ledger for the whole rehearsal, threaded everywhere it is
        # needed, matching what the unattended service does. This script read it 31 times
        # across a full run, and two of those reads disagreeing is precisely the defect the
        # rehearsal exists to catch rather than to demonstrate.
        ledger = ledger_bytes()
        controls = default_ustb_controls(ledger_from_bytes(ledger))
        run_ustb_epoch(
            transport=FixtureTransport(ROOT / "fixtures", date(2026, 8, 13)),
            store=evidence_store,
            now=date(2026, 8, 13),
            retrieved_at=confirmed_at,
            controls=controls,
        )
        epoch = run_ustb_epoch(
            transport=FixtureTransport(ROOT / "fixtures", date(2026, 8, 14)),
            store=evidence_store,
            now=date(2026, 8, 14),
            retrieved_at=retrieved_at,
            controls=controls,
        )
        signer = Ed25519Signer.from_seed(bytes(range(32)))
        report = build_observation_report(
            epoch,
            controls,
            epoch_id="ustb-fixture-2026-08-14",
            sequence=1,
            publisher_kid=signer.kid,
            approval_ledger=ledger,
        )
        signed = signer.sign_report(report)
        evidence_digests = evidence_references(epoch)
        bundle = create_bundle(
            signed,
            signer.public_key_record(),
            controls,
            evidence_digests,
            approval_ledger=ledger,
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
        manifest = _local_manifest(
            web3,
            registry=registry,
            rpc_url=rpc_url,
            registry_address=registry_receipt.contractAddress,
            deployment_block=registry_receipt.blockNumber,
            deployer=owner,
            publisher=publisher,
            operations=operations,
            reporter_public_key=signer.public_key_record()["public_key"],
        )
        backend = SignedRegistryBackend(
            manifest, PublisherKey.from_hex(_publisher_private_key(publisher), manifest)
        )
        preflight = backend.preflight()
        client = PublisherClient(backend, log, workspace / "pending.json")
        first = client.publish(signed, report_uri="urn:touchstone:ustb:fixture:1")
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
            # The epoch it corrects, not a new one. A correction restates one day; naming
            # it separately would make it a second report about that day wearing the one
            # word the registry allows to follow an existing publication, and the registry
            # now refuses it outright.
            epoch_id="ustb-fixture-2026-08-14",
            sequence=2,
            publisher_kid=signer.kid,
            previous_state=AssetState.CONFIRMED,
            event=OperationalEvent.CORRECTION_PUBLISHED,
            correction_of=1,
            approval_ledger=ledger,
        )
        signed_correction = signer.sign_report(correction)
        verify_bundle(
            create_bundle(
                signed_correction,
                signer.public_key_record(),
                controls,
                evidence_digests,
                approval_ledger=ledger,
            )
        )
        second = client.publish_correction(
            signed_correction, report_uri="urn:touchstone:ustb:fixture:2"
        )
        historical = registry.functions.getReport(asset_key, 1).call()
        if historical[7] != 1 or historical[8] != "urn:touchstone:ustb:fixture:1":
            raise AssertionError("correction changed the prior report")
        if registry.functions.correctionTarget(asset_key, 2).call() != 1:
            raise AssertionError("correction target was not recorded")
        epoch_key = epoch_key_bytes(report["epoch_id"])
        if registry.functions.epochSequence(asset_key, epoch_key).call() != 1:
            raise AssertionError("the epoch no longer names its first publication")
        # The one thing the registry must refuse outright: a second report about the day
        # this loop already published, offered with a correct next sequence.
        try:
            registry.functions.publish(
                asset_key,
                bytes.fromhex(report["control_set_root"]),
                bytes.fromhex(report["evidence_root"]),
                epoch_key,
                0,
                int(
                    datetime.fromisoformat(
                        report["observed_at"].replace("Z", "+00:00")
                    ).timestamp()
                ),
                valid_until,
                3,
                "urn:touchstone:ustb:fixture:3",
            ).call({"from": publisher})
        except ContractLogicError as error:
            if "EpochAlreadyPublished" not in str(error):
                raise AssertionError(f"unexpected refusal: {error}") from error
        else:
            raise AssertionError("the registry accepted a second report for one epoch")

        return {
            "asset_gate_after_age": reason,
            "asset_gate_initial": "allowed",
            "correction_transaction": second.transaction_hash,
            "first_transaction": first.transaction_hash,
            "historical_sequence": historical[7],
            "log_entries": len(log.verify()),
            "publisher_authorized": preflight.publisher_authorized,
            "publisher_identity": preflight.publisher_identity,
            "registry": registry_receipt.contractAddress,
            "registry_runtime_bytecode_sha256": (
                preflight.registry_runtime_bytecode_sha256
            ),
        }


def _publisher_private_key(expected_address: str) -> str:
    """Derive the local publisher key and refuse to sign as anyone else."""
    Account.enable_unaudited_hdwallet_features()
    account = Account.from_mnemonic(
        DEV_MNEMONIC, account_path=f"m/44'/60'/0'/0/{PUBLISHER_ACCOUNT_INDEX}"
    )
    if Web3.to_checksum_address(account.address) != Web3.to_checksum_address(
        expected_address
    ):
        raise AssertionError(
            f"derived {account.address} but the node's publisher account is "
            f"{expected_address}"
        )
    return account.key.hex()


def _local_manifest(
    web3: Web3,
    *,
    registry,
    rpc_url: str,
    registry_address: str,
    deployment_block: int,
    deployer: str,
    publisher: str,
    operations: str,
    reporter_public_key: str,
) -> DeploymentManifest:
    """Describe the deployment this run just made, the way an operator's file would.

    The bytecode digest is read back from the chain rather than computed from the build
    artifact, so the manifest describes what is actually deployed — which is the claim the
    publisher's preflight goes on to verify.
    """
    return DeploymentManifest.from_mapping(
        {
            "manifest_version": 1,
            "network": "hardhat-local",
            "chain_id": web3.eth.chain_id,
            "rpc_url": rpc_url,
            "registry_address": registry_address,
            "registry_runtime_bytecode_sha256": runtime_bytecode_sha256(
                bytes(web3.eth.get_code(registry_address))
            ),
            "publisher_address": publisher,
            "publisher_identity_address": registry.functions.publisherIdentity(
                publisher
            ).call(),
            "deployer_address": deployer,
            "operations_address": operations,
            "confirmations": 1,
            "deployment_state": "active",
            "deployment_block": deployment_block,
            "reporting_keys": [
                {
                    "kid": f"ed25519:{hashlib.sha256(bytes.fromhex(reporter_public_key)).hexdigest()}",
                    "public_key": reporter_public_key,
                    "state": "active",
                }
            ],
        }
    )


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


def _compile_contracts() -> None:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        raise RuntimeError("npx is not installed")
    subprocess.run([npx, "hardhat", "compile"], cwd=CONTRACTS, check=True)


def _discard_log(path: Path) -> None:
    """Delete the node log without ever failing the run that produced it.

    On Windows the terminated node's handle can outlive ``taskkill``, so an immediate
    unlink raises ``PermissionError``. Losing a temporary log matters far less than
    losing a completed E2E result, so this retries briefly and then gives up — but it
    says so on stderr, because the abandoned log holds Hardhat's development account
    keys and silent accumulation would hide that.
    """
    for _ in range(20):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1)
    print(f"warning: left Hardhat node log at {path}", file=sys.stderr)


def _free_port() -> int:
    """Reserve an ephemeral loopback port so a developer's own node is never reused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _start_node_on_free_port() -> tuple[subprocess.Popen[bytes], Path, int]:
    """Start the node, retrying on a fresh port if the chosen one was taken.

    ``_free_port`` must release the port before Hardhat can bind it, so another
    process can claim it in between. That loses the port, not the run.
    """
    last_error: RuntimeError | None = None
    for _ in range(3):
        port = _free_port()
        try:
            process, output = _start_node(port)
        except RuntimeError as error:
            last_error = error
            continue
        return process, output, port
    raise RuntimeError(f"Hardhat node could not be started: {last_error}")


def _start_node(port: int) -> tuple[subprocess.Popen[bytes], Path]:
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
                [
                    npx,
                    "hardhat",
                    "node",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=CONTRACTS,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
    except Exception:
        _discard_log(output)
        raise
    # Readiness must come from this process, not from whoever answers on the port.
    # _free_port releases the port before Hardhat binds it, so a foreign JSON-RPC
    # server can hold it; that server would answer is_connected() happily and the
    # loop would silently run against someone else's chain.
    announcement = f"JSON-RPC server at http://127.0.0.1:{port}/"
    for _ in range(240):
        if process.poll() is not None:
            message = output.read_text(encoding="utf-8", errors="replace")
            _discard_log(output)
            raise RuntimeError(message)
        if announcement in output.read_text(encoding="utf-8", errors="replace"):
            if Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}")).is_connected():
                return process, output
            _stop_node(process)
            _discard_log(output)
            raise RuntimeError(f"Hardhat announced {port} but the port is unreachable")
        time.sleep(0.25)
    _stop_node(process)
    _discard_log(output)
    raise RuntimeError("Hardhat node did not become ready")


def run_managed_e2e() -> dict[str, object]:
    """Compile, start a clock-pinned private node, run the loop, and stop the node.

    The node is always this run's own, on an ephemeral port, so the result never
    depends on whether a node happens to be running or what clock it started with.
    """
    _compile_contracts()
    process, output, port = _start_node_on_free_port()
    try:
        return run_e2e(rpc_url=f"http://127.0.0.1:{port}")
    finally:
        _stop_node(process)
        _discard_log(output)


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
    try:
        if args.use_running_node:
            _compile_contracts()
            result = run_e2e()
        else:
            result = run_managed_e2e()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as error:
        print(f"E2E FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
