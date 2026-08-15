"""A child process that publishes, and can be killed at a chosen moment.

Run as a subprocess by ``test_service_restart.py``. It exists because a restart test that
never leaves the parent process is not a restart test: in-process state that Python happens
to keep alive proves nothing about what survives on disk.

The registry it publishes to is a JSON file, so two processes see the same chain. Signing
is real — the same ``PublisherKey`` production uses — so the bytes a killed process left in
the journal are the bytes the next process finds there.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.deployment import DeploymentManifest  # noqa: E402
from touchstone.incidents import IncidentLog  # noqa: E402
from touchstone.keyring import PublisherKey  # noqa: E402
from touchstone.operations import OperationsStore  # noqa: E402
from touchstone.publish import (  # noqa: E402
    CONFIRMED,
    MISSING,
    ChainReport,
    DeploymentIdentity,
    PreparedTransaction,
    PublisherClient,
)
from touchstone.signing import canonical_json_bytes  # noqa: E402
from touchstone.translog import TransparencyLog  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from run_service import Service  # type: ignore # noqa: E402


PUBLISHER_SECRET = "a1" * 32
REPORT_URI = "urn:touchstone:restart:1"


class FileChainBackend:
    """A registry whose whole state is one JSON file, so it survives a process."""

    def __init__(self, manifest: DeploymentManifest, path: Path) -> None:
        self.manifest = manifest
        self.path = path
        self.key = PublisherKey.from_hex(PUBLISHER_SECRET, manifest)

    # ---------------------------------------------------------------- chain file
    def _load(self) -> dict:
        if not self.path.exists():
            return {"reports": {}, "receipts": {}, "intents": {}, "nonce": 0}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, chain: dict) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(chain), encoding="utf-8")
        os.replace(temporary, self.path)

    # ------------------------------------------------------------------ protocol
    def revalidate(self) -> None:
        return None

    def identity(self) -> DeploymentIdentity:
        return DeploymentIdentity(
            chain_id=self.manifest.chain_id,
            registry_address=self.manifest.registry_address,
            publisher_address=self.manifest.publisher_address,
        )

    def publisher_lineage(self, address: str) -> str:
        return self.manifest.publisher_identity_address

    def calldata(self, asset_key, report, report_uri, correction_of) -> bytes:
        return canonical_json_bytes(
            {
                "asset_key": asset_key.hex(),
                "correction_of": correction_of,
                "report_uri": report_uri,
                "sequence": report["sequence"],
            }
        )

    def latest_sequence(self, asset_key: bytes) -> int:
        return len(self._load()["reports"].get(asset_key.hex(), []))

    def get_report(self, asset_key: bytes, sequence: int) -> ChainReport:
        stored = self._load()["reports"][asset_key.hex()][sequence - 1]
        return ChainReport(**stored)

    def prepare(self, asset_key, report, report_uri, correction_of):
        chain = self._load()
        nonce = chain["nonce"]
        transaction_hash, raw = self.key.sign_transaction(
            {
                "to": self.manifest.registry_address,
                "value": 0,
                "gas": 200_000,
                "maxFeePerGas": 10**9,
                "maxPriorityFeePerGas": 10**8,
                "nonce": nonce,
                "chainId": self.manifest.chain_id,
                "data": self.calldata(asset_key, report, report_uri, correction_of),
            }
        )
        chain["intents"][transaction_hash] = {
            "asset_key": asset_key.hex(),
            "correction_of": correction_of,
            "report": dict(report),
            "report_uri": report_uri,
        }
        self._save(chain)
        return PreparedTransaction(
            transaction_hash=transaction_hash, raw=raw, nonce=nonce
        )

    def broadcast(self, prepared: PreparedTransaction) -> str:
        chain = self._load()
        if prepared.transaction_hash in chain["receipts"]:
            return prepared.transaction_hash
        intent = chain["intents"][prepared.transaction_hash]
        report = intent["report"]
        reports = chain["reports"].setdefault(intent["asset_key"], [])
        reports.append(
            {
                "control_set_root": report["control_set_root"],
                "evidence_root": report["evidence_root"],
                "status": 0,
                "observed_at": 1_786_630_577,
                "valid_until": 1_786_665_599,
                "publisher": self.manifest.publisher_address,
                "sequence": report["sequence"],
                "report_uri": intent["report_uri"],
            }
        )
        chain["receipts"][prepared.transaction_hash] = {
            "blockHash": "0x" + "aa" * 32,
            "blockNumber": len(chain["receipts"]) + 1,
            "gasUsed": 200_000,
            "status": 1,
        }
        chain["nonce"] = prepared.nonce + 1
        self._save(chain)
        return prepared.transaction_hash

    def receipt_state(self, transaction_hash: str):
        receipt = self._load()["receipts"].get(transaction_hash)
        return (CONFIRMED, receipt) if receipt is not None else (MISSING, None)

    def get_receipt(self, transaction_hash: str):
        state, receipt = self.receipt_state(transaction_hash)
        return receipt if state == CONFIRMED else None

    def wait_for_receipt(self, transaction_hash: str, timeout: float):
        del timeout
        state, receipt = self.receipt_state(transaction_hash)
        if state != CONFIRMED:
            raise RuntimeError("no receipt")
        return receipt

    def find_receipt(self, asset_key: bytes, sequence: int, correction_of):
        del correction_of
        chain = self._load()
        for transaction_hash, intent in chain["intents"].items():
            if (
                intent["asset_key"] == asset_key.hex()
                and intent["report"]["sequence"] == sequence
                and transaction_hash in chain["receipts"]
            ):
                return transaction_hash, chain["receipts"][transaction_hash]
        return None


def build(workspace: Path) -> tuple[Service, FileChainBackend]:
    manifest = DeploymentManifest.load(workspace / "manifest.json")
    backend = FileChainBackend(manifest, workspace / "chain.json")
    client = PublisherClient(
        backend,
        TransparencyLog(workspace / "transparency.jsonl"),
        workspace / "pending.json",
    )
    service = Service(
        client,
        OperationsStore(workspace / "operations"),
        IncidentLog(workspace / "incidents.jsonl"),
        asset_key=json.loads(
            (workspace / "signed_report.json").read_text(encoding="utf-8")
        )["report"]["asset_key"],
    )
    return service, backend


def main() -> int:
    mode = sys.argv[1]
    workspace = Path(sys.argv[2])
    service, backend = build(workspace)
    signed_report = json.loads(
        (workspace / "signed_report.json").read_text(encoding="utf-8")
    )
    scheduled_for = datetime.now(timezone.utc)

    if mode == "die-before-broadcast":
        # The durable operation and the publisher's journal are both on disk; the wire
        # never saw the transaction. Dying here is the crash a restart must survive
        # without publishing twice and without abandoning the publication.
        service.operations.begin_operation(
            signed_report,
            report_uri=REPORT_URI,
            correction_of=None,
            scheduled_for=scheduled_for,
        )
        backend.broadcast = lambda prepared: os._exit(9)
        service.operations.resolve(service.client)
        return 0

    if mode == "die-after-broadcast":
        # The transaction really landed. The process dies before the operation is
        # cleared, so the next start finds a publication that is already settled.
        service.operations.begin_operation(
            signed_report,
            report_uri=REPORT_URI,
            correction_of=None,
            scheduled_for=scheduled_for,
        )
        broadcast = backend.broadcast

        def broadcast_then_die(prepared):
            broadcast(prepared)
            os._exit(9)

        backend.broadcast = broadcast_then_die
        service.operations.resolve(service.client)
        return 0

    if mode == "die-after-finalize":
        # The publisher finished: the chain accepted it, the transparency log recorded it,
        # and the publisher cleared its own journal. Only *this service's* operation is
        # still on disk. That is the gap between the two layers, and it is the one the
        # earlier crash modes never reached.
        service.operations.begin_operation(
            signed_report,
            report_uri=REPORT_URI,
            correction_of=None,
            scheduled_for=scheduled_for,
        )
        service.operations.save_state = lambda *a, **k: os._exit(9)
        service.operations.resolve(service.client)
        return 0

    if mode == "die-in-slot-after-finalize":
        # The same crash, reached through Service.run_slot rather than around it, so the
        # slot path itself is exercised.
        service.operations.save_state = lambda *a, **k: os._exit(9)
        service.run_slot(
            scheduled_for,
            lambda at: signed_report,
            report_uri=lambda report: REPORT_URI,
        )
        return 0

    if mode == "slot":
        outcome = service.run_slot(
            scheduled_for,
            lambda at: signed_report,
            report_uri=lambda report: REPORT_URI,
        )
        print(json.dumps({"published": outcome.published, "detail": outcome.detail}))
        return 0

    if mode == "resolve":
        outcome = service.resolve_startup()
        print(
            json.dumps(
                {
                    "resolved": outcome is not None,
                    "sequences": _published_sequences(backend, signed_report),
                    "operation_cleared": service.operations.load_operation() is None,
                    "log_entries": len(service.client.transparency_log.verify()),
                    "state_sequence": _state_sequence(service, signed_report),
                    "open_incidents": len(service.incidents.open_incidents()),
                }
            )
        )
        return 0

    raise SystemExit(f"unknown mode: {mode}")


def _state_sequence(service, signed_report: dict) -> int | None:
    state = service.operations.load_state(signed_report["report"]["asset_key"])
    return None if state is None else state.sequence


def _published_sequences(backend: FileChainBackend, signed_report: dict) -> list[int]:
    from web3 import Web3

    asset_key = bytes(Web3.keccak(text=signed_report["report"]["asset_key"]))
    chain = backend._load()
    return [
        entry["sequence"] for entry in chain["reports"].get(asset_key.hex(), [])
    ]


if __name__ == "__main__":
    raise SystemExit(main())
