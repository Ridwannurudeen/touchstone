"""Preflight refusals and the publishing entry point, against a scripted endpoint.

The happy path is proved end to end against a real chain by ``scripts/e2e_local.py``. What
a real chain cannot easily be made to do is lie, so the refusals are proved here against a
node that answers exactly what each test wants it to answer.

Every case is a way a correctly-signed transaction could still be wrong. The assertion in
each is the same: nothing is signed, and nothing is sent.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from eth_account import Account
import pytest
from web3 import Web3

from touchstone.deployment import DeploymentManifest
from touchstone.keyring import (
    PUBLISHER_KEY_ENV,
    PublisherKey,
    rolled_over,
    verification_keys,
)
from touchstone.publish import PreflightFailed, SignedRegistryBackend
from touchstone.signing import SIGNING_SEED_ENV, Ed25519Signer

import scripts.publish_epoch as publish_epoch


PUBLISHER_SECRET = "a1" * 32
DEPLOYER_SECRET = "b2" * 32
OPERATIONS_SECRET = "c3" * 32
STRANGER_SECRET = "d4" * 32
REPORTER_SEED = bytes(range(32))
RUNTIME_CODE = bytes.fromhex("60806040523480156100")
RUNTIME_SHA256 = hashlib.sha256(RUNTIME_CODE).hexdigest()
REGISTRY = Web3.to_checksum_address("0x" + "ab" * 20)
CHAIN_ID = 31337


def selector(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature)[:4].hex().removeprefix("0x")


SELECTORS = {
    "owner": selector("owner()"),
    "expectedChainId": selector("expectedChainId()"),
    "isPublisherAuthorized": selector("isPublisherAuthorized(address)"),
    "publisherIdentity": selector("publisherIdentity(address)"),
    "latestSequence": selector("latestSequence(bytes32)"),
}


def word(value: int) -> str:
    return f"0x{value & ((1 << 256) - 1):064x}"


def address_word(value: str) -> str:
    return "0x" + "00" * 12 + value[2:].lower()


class StubNode:
    """A JSON-RPC endpoint that answers whatever the test tells it to."""

    def __init__(self, **overrides: object) -> None:
        publisher = Account.from_key(bytes.fromhex(PUBLISHER_SECRET)).address
        deployer = Account.from_key(bytes.fromhex(DEPLOYER_SECRET)).address
        # Lineage defaults to the publisher itself, which is what authorizePublisher
        # records for a first authorization.
        self.answers: dict[str, object] = {
            "eth_chainId": hex(CHAIN_ID),
            "eth_blockNumber": hex(120),
            "eth_getCode": "0x" + RUNTIME_CODE.hex(),
            "eth_getBalance": hex(10**18),
            "owner": address_word(deployer),
            "expectedChainId": word(CHAIN_ID),
            "isPublisherAuthorized": word(1),
            "publisherIdentity": address_word(publisher),
            "latestSequence": word(0),
        }
        self.answers.update(overrides)
        self.calls: list[str] = []
        self._server: ThreadingHTTPServer | None = None

    def __enter__(self) -> StubNode:
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": node.answer(payload),
                    }
                ).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def answer(self, payload: dict[str, object]) -> object:
        method = payload["method"]
        if method != "eth_call":
            self.calls.append(method)
            return self.answers.get(method, "0x")
        data = payload["params"][0]["data"]
        for name, prefix in SELECTORS.items():
            if data.startswith(prefix):
                self.calls.append(name)
                return self.answers[name]
        raise AssertionError(f"unscripted eth_call: {data}")


def manifest_for(node: StubNode, **overrides: object) -> DeploymentManifest:
    signer = Ed25519Signer.from_seed(REPORTER_SEED)
    value: dict[str, object] = {
        "manifest_version": 1,
        "network": "hardhat-local",
        "chain_id": CHAIN_ID,
        "rpc_url": node.url,
        "registry_address": REGISTRY,
        "registry_runtime_bytecode_sha256": RUNTIME_SHA256,
        "publisher_address": Account.from_key(bytes.fromhex(PUBLISHER_SECRET)).address,
        "publisher_identity_address": Account.from_key(
            bytes.fromhex(PUBLISHER_SECRET)
        ).address,
        "deployer_address": Account.from_key(bytes.fromhex(DEPLOYER_SECRET)).address,
        "operations_address": Account.from_key(
            bytes.fromhex(OPERATIONS_SECRET)
        ).address,
        "confirmations": 1,
        "deployment_block": 3,
        "reporting_keys": [
            {
                "kid": signer.kid,
                "public_key": signer.public_key_record()["public_key"],
                "state": "active",
            }
        ],
    }
    value.update(overrides)
    return DeploymentManifest.from_mapping(value)


def backend_for(node: StubNode, **overrides: object) -> SignedRegistryBackend:
    manifest = manifest_for(node, **overrides)
    return SignedRegistryBackend(
        manifest, PublisherKey.from_hex(PUBLISHER_SECRET, manifest)
    )


def test_preflight_reports_what_the_chain_answered() -> None:
    with StubNode() as node:
        report = backend_for(node).preflight()

    assert report.chain_id == CHAIN_ID
    assert report.registry_runtime_bytecode_sha256 == RUNTIME_SHA256
    assert report.registry_expected_chain_id == CHAIN_ID
    assert report.publisher_authorized is True
    assert report.publisher_balance_wei == 10**18
    assert (
        report.registry_owner
        == Account.from_key(bytes.fromhex(DEPLOYER_SECRET)).address
    )


def test_an_endpoint_on_another_chain_is_refused() -> None:
    """The commonest silent failure: an endpoint that fails over to a different network."""
    with StubNode(eth_chainId=hex(196)) as node:
        with pytest.raises(PreflightFailed, match="endpoint reports chain 196"):
            backend_for(node).preflight()
        assert "eth_call" not in node.calls
        assert "owner" not in node.calls


def test_an_address_holding_no_contract_is_refused() -> None:
    with StubNode(eth_getCode="0x") as node:
        with pytest.raises(PreflightFailed, match="no contract is deployed"):
            backend_for(node).preflight()


def test_different_runtime_bytecode_is_refused() -> None:
    """The address may be right and still hold a contract this release never tested."""
    other = bytes.fromhex("60806040523480156101")
    with StubNode(eth_getCode="0x" + other.hex()) as node:
        with pytest.raises(PreflightFailed, match="holds runtime bytecode"):
            backend_for(node).preflight()


def test_a_registry_built_for_another_chain_is_refused() -> None:
    with StubNode(expectedChainId=word(196)) as node:
        with pytest.raises(PreflightFailed, match="constructed for chain 196"):
            backend_for(node).preflight()


def test_an_unauthorized_publisher_is_refused() -> None:
    with StubNode(isPublisherAuthorized=word(0)) as node:
        with pytest.raises(PreflightFailed, match="not an authorized publisher"):
            backend_for(node).preflight()


def test_publishing_as_the_owner_is_refused() -> None:
    """An unattended process must not hold the authority to revoke and rotate."""
    publisher = Account.from_key(bytes.fromhex(PUBLISHER_SECRET)).address
    with StubNode(owner=address_word(publisher)) as node:
        with pytest.raises(PreflightFailed, match="is the registry owner"):
            backend_for(node).preflight()


def test_an_owner_that_is_not_the_declared_deployer_is_refused() -> None:
    """Ownership moving without the manifest saying so is a change nobody recorded."""
    stranger = Account.from_key(bytes.fromhex(STRANGER_SECRET)).address
    with StubNode(owner=address_word(stranger)) as node:
        with pytest.raises(PreflightFailed, match="registry owner is"):
            backend_for(node).preflight()


def test_a_publisher_from_another_lineage_is_refused() -> None:
    """Authorization says an owner call let this address publish. Lineage says it is the
    same publishing identity the manifest was written for.

    An owner who calls authorizePublisher(B) instead of rotatePublisher(A, B) creates a
    second, unrelated lineage. It reads as authorized, and no consumer gated on
    isPublisherFor would accept it.
    """
    stranger = Account.from_key(bytes.fromhex(STRANGER_SECRET)).address
    with StubNode(publisherIdentity=address_word(stranger)) as node:
        with pytest.raises(PreflightFailed, match="belongs to publisher lineage"):
            backend_for(node).preflight()


def test_a_publisher_with_no_gas_is_refused() -> None:
    with StubNode(eth_getBalance="0x0") as node:
        with pytest.raises(PreflightFailed, match="holds no gas"):
            backend_for(node).preflight()


def test_the_registry_is_not_read_through_an_unverified_endpoint() -> None:
    """A sequence read from the wrong chain decides which sequence gets published next."""
    with StubNode(eth_chainId=hex(196)) as node:
        with pytest.raises(PreflightFailed):
            backend_for(node).latest_sequence(b"\x00" * 32)
        assert "latestSequence" not in node.calls


def test_a_key_that_is_not_the_declared_publisher_cannot_build_a_backend() -> None:
    with StubNode() as node:
        manifest = manifest_for(node)
        deployer_as_publisher = Account.from_key(bytes.fromhex(DEPLOYER_SECRET)).address
        foreign = PublisherKey.from_hex(
            DEPLOYER_SECRET,
            manifest_for(
                node,
                publisher_address=deployer_as_publisher,
                publisher_identity_address=deployer_as_publisher,
                deployer_address=Account.from_key(
                    bytes.fromhex(STRANGER_SECRET)
                ).address,
            ),
        )
        with pytest.raises(PreflightFailed, match="is not the manifest's"):
            SignedRegistryBackend(manifest, foreign)


def _manifest_file(node: StubNode, tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(manifest_for(node, **overrides).to_mapping()), encoding="utf-8"
    )
    return path


def test_the_cli_can_preflight_without_signing_anything(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv(PUBLISHER_KEY_ENV, PUBLISHER_SECRET)
    monkeypatch.delenv(SIGNING_SEED_ENV, raising=False)
    with StubNode() as node:
        code = publish_epoch.main(
            ["--manifest", str(_manifest_file(node, tmp_path)), "--preflight"]
        )
        sent = [call for call in node.calls if "send" in call.lower()]

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["published"] is False
    assert result["publisher_authorized"] is True
    assert sent == [], "a preflight must never broadcast"


def test_the_cli_refuses_to_publish_under_an_untrusted_reporting_key(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A revoked or unknown key must not reach the chain, however well-formed its report."""
    monkeypatch.setenv(PUBLISHER_KEY_ENV, PUBLISHER_SECRET)
    stranger = Ed25519Signer.from_seed(bytes(range(1, 33)))
    report = tmp_path / "signed.json"
    report.write_text(
        json.dumps(stranger.sign_report({"asset_key": "x", "sequence": 1})),
        encoding="utf-8",
    )
    with StubNode() as node:
        code = publish_epoch.main(
            [
                "--manifest",
                str(_manifest_file(node, tmp_path)),
                "--signed-report",
                str(report),
                "--report-uri",
                "urn:touchstone:test:1",
                "--transparency-log",
                str(tmp_path / "log.jsonl"),
                "--pending",
                str(tmp_path / "pending.json"),
            ]
        )
        sent = [call for call in node.calls if "send" in call.lower()]

    assert code == 1
    assert "active reporting key" in capsys.readouterr().err
    assert sent == []


def test_a_superseded_key_verifies_history_but_cannot_publish_anew(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Rollover has to actually change which key can publish, or it changed nothing.

    The retired key stays published so that what it already signed keeps verifying. That
    is a statement about the past. Letting it place a *new* report would leave a retired
    key still able to write to the registry, which is the thing rolling over exists to end.
    A date check would not substitute: a compromised key can backdate its own report.
    """
    monkeypatch.setenv(PUBLISHER_KEY_ENV, PUBLISHER_SECRET)
    retired = Ed25519Signer.from_seed(REPORTER_SEED)
    successor = Ed25519Signer.from_seed(bytes(range(1, 33)))
    report = tmp_path / "signed.json"
    report.write_text(
        json.dumps(retired.sign_report({"asset_key": "x", "sequence": 1})),
        encoding="utf-8",
    )
    with StubNode() as node:
        rotated = rolled_over(
            manifest_for(node),
            new_public_key=bytes.fromhex(successor.public_key_record()["public_key"]),
            at=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        )
        path = tmp_path / "rotated.json"
        path.write_text(json.dumps(rotated.to_mapping()), encoding="utf-8")

        # It is still trusted for verification...
        assert retired.kid in verification_keys(rotated)
        assert rotated.key(retired.kid).state == "superseded"

        # ...and it can no longer publish.
        code = publish_epoch.main(
            [
                "--manifest",
                str(path),
                "--signed-report",
                str(report),
                "--report-uri",
                "urn:touchstone:test:1",
                "--transparency-log",
                str(tmp_path / "log.jsonl"),
                "--pending",
                str(tmp_path / "pending.json"),
            ]
        )
        sent = [call for call in node.calls if "send" in call.lower()]

    assert code == 1
    error = capsys.readouterr().err
    assert "active reporting key" in error
    assert "superseded" in error
    assert sent == []


def test_the_cli_requires_everything_publishing_needs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        publish_epoch.main(["--manifest", str(tmp_path / "absent.json")])


def test_the_cli_reports_a_refusal_rather_than_raising(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv(PUBLISHER_KEY_ENV, PUBLISHER_SECRET)
    with StubNode(eth_chainId=hex(196)) as node:
        code = publish_epoch.main(
            ["--manifest", str(_manifest_file(node, tmp_path)), "--preflight"]
        )

    assert code == 1
    assert "PUBLISH FAIL" in capsys.readouterr().err
