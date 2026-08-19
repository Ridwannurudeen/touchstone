import hashlib

import pytest
from eth_account import Account
from web3 import Web3

from touchstone.deployment import RegistryV2DeploymentManifest
from touchstone.keyring import PublisherKey, decoded_transaction
from touchstone.publish_v2 import (
    RegistryV2Backend,
    RegistryV2FeeCeilingExceeded,
    RegistryV2PreflightFailed,
    RegistryV2PublicationError,
    RegistryV2ReconciliationFailed,
    RelayerKey,
)
from touchstone.signing import Ed25519Signer, canonical_json_bytes


PUBLISHER_SECRET = "11" * 32
RELAYER_SECRET = "22" * 32
PUBLISHER = Web3.to_checksum_address(Account.from_key(PUBLISHER_SECRET).address)
RELAYER = Web3.to_checksum_address(Account.from_key(RELAYER_SECRET).address)
OWNER = Web3.to_checksum_address("0x" + "33" * 20)
OPERATIONS = Web3.to_checksum_address("0x" + "44" * 20)
LEGACY = Web3.to_checksum_address("0x" + "55" * 20)
REGISTRY = Web3.to_checksum_address("0x" + "66" * 20)
LEGACY_CODE = b"legacy-runtime"
V2_CODE = b"v2-runtime"


class FakeCall:
    def __init__(self, value: object) -> None:
        self.value = value

    def call(self) -> object:
        return self.value


class FakeFunctions:
    def __init__(self, eth: "FakeEth") -> None:
        self.eth = eth

    def expectedChainId(self) -> FakeCall:
        return FakeCall(self.eth.chain_id)

    def legacyRegistry(self) -> FakeCall:
        return FakeCall(self.eth.legacy_registry)

    def owner(self) -> FakeCall:
        return FakeCall(self.eth.owner)

    def isPublisherAuthorized(self, publisher: str) -> FakeCall:
        return FakeCall(self.eth.authorized and publisher == PUBLISHER)

    def publisherIdentity(self, publisher: str) -> FakeCall:
        assert publisher == PUBLISHER
        return FakeCall(PUBLISHER)

    def latestSequence(self, asset_key: bytes) -> FakeCall:
        return FakeCall(self.eth.latest.get(bytes(asset_key), 0))

    def epochSequence(self, asset_key: bytes, epoch_key: bytes) -> FakeCall:
        return FakeCall(self.eth.epochs.get((bytes(asset_key), bytes(epoch_key)), 0))

    def getReport(self, asset_key: bytes, sequence: int) -> FakeCall:
        return FakeCall(self.eth.reports[(bytes(asset_key), sequence)])

    def correctionTarget(self, asset_key: bytes, sequence: int) -> FakeCall:
        return FakeCall(self.eth.corrections.get((bytes(asset_key), sequence), 0))


class FakeContract:
    def __init__(self, eth: "FakeEth") -> None:
        self.functions = FakeFunctions(eth)


class FakeEth:
    def __init__(self) -> None:
        self.chain_id = 31337
        self.block_number = 10
        self.legacy_registry = LEGACY
        self.owner = OWNER
        self.authorized = True
        self.balance = 10**18
        self.nonce = 7
        self.estimate = 100_000
        self.base_fee = 10
        self.max_priority_fee = 2
        self.gas_price = 20
        self.latest: dict[bytes, int] = {}
        self.epochs: dict[tuple[bytes, bytes], int] = {}
        self.reports: dict[tuple[bytes, int], tuple[object, ...]] = {}
        self.corrections: dict[tuple[bytes, int], int] = {}

    def contract(self, *, address: str, abi: object) -> FakeContract:
        assert address == REGISTRY
        assert abi
        return FakeContract(self)

    def get_code(self, address: str) -> bytes:
        return V2_CODE if address == REGISTRY else LEGACY_CODE

    def get_balance(self, address: str) -> int:
        assert address in {PUBLISHER, RELAYER}
        return self.balance

    def estimate_gas(self, transaction: dict[str, object]) -> int:
        assert transaction["from"] in {PUBLISHER, RELAYER}
        assert transaction["to"] == REGISTRY
        return self.estimate

    def get_transaction_count(self, address: str, state: str) -> int:
        assert address in {PUBLISHER, RELAYER}
        assert state == "pending"
        return self.nonce

    def get_block(self, block: str) -> dict[str, int]:
        assert block == "latest"
        return {"baseFeePerGas": self.base_fee}


class FakeWeb3:
    def __init__(self) -> None:
        self.eth = FakeEth()


class CountingRelayer:
    def __init__(self) -> None:
        self.key = RelayerKey.from_hex(RELAYER_SECRET)
        self.address = self.key.address
        self.signatures = 0

    def sign_transaction(self, transaction: dict[str, object]) -> tuple[str, bytes]:
        self.signatures += 1
        return self.key.sign_transaction(transaction)


@pytest.fixture
def reporter() -> Ed25519Signer:
    return Ed25519Signer.from_seed(b"\x77" * 32)


@pytest.fixture
def deployment(reporter: Ed25519Signer) -> RegistryV2DeploymentManifest:
    key = reporter.public_key_record()
    return RegistryV2DeploymentManifest.from_mapping(
        {
            "manifest_version": 2,
            "registry_version": 2,
            "network": "hardhat-local",
            "chain_id": 31337,
            "rpc_url": "http://127.0.0.1:8545",
            "registry_address": REGISTRY,
            "registry_runtime_bytecode_sha256": hashlib.sha256(V2_CODE).hexdigest(),
            "legacy_registry_address": LEGACY,
            "legacy_registry_runtime_bytecode_sha256": hashlib.sha256(
                LEGACY_CODE
            ).hexdigest(),
            "owner_address": OWNER,
            "relayer_address": RELAYER,
            "publisher_address": PUBLISHER,
            "publisher_identity_address": PUBLISHER,
            "deployer_address": OWNER,
            "operations_address": OPERATIONS,
            "confirmations": 1,
            "max_fee_wei": 10**15,
            "deployment_block": 1,
            "deployment_state": "active",
            "deployment_transaction": "0x" + "aa" * 32,
            "authorization_transaction": "0x" + "bb" * 32,
            "reporting_keys": [
                {
                    "kid": key["kid"],
                    "public_key": key["public_key"],
                    "state": "active",
                }
            ],
        }
    )


@pytest.fixture
def publisher_key(deployment: RegistryV2DeploymentManifest) -> PublisherKey:
    return PublisherKey.from_hex(PUBLISHER_SECRET, deployment)


@pytest.fixture
def report() -> dict[str, object]:
    return {
        "asset_key": "eip155:1:0x" + "ab" * 20 + "#policy:nav-settlement:2",
        "control_set_root": "66" * 32,
        "epoch_id": "2026-08-19",
        "evidence_root": "77" * 32,
        "observed_at": "2026-08-19T10:00:00Z",
        "policy": {
            "control_ids": ["nav"],
            "policy_digest": "55" * 32,
            "policy_id": "nav-settlement",
            "policy_version": 2,
        },
        "sequence": 1,
        "state": "UNVERIFIABLE",
        "valid_until": "2026-08-20T10:00:00Z",
    }


def backend(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    web3: FakeWeb3,
    relayer: CountingRelayer | None = None,
) -> RegistryV2Backend:
    return RegistryV2Backend(
        deployment,
        publisher_key,
        relayer_key=relayer or CountingRelayer(),
        web3=web3,
    )


def test_preflight_verifies_both_deployments_and_roles(
    deployment: RegistryV2DeploymentManifest, publisher_key: PublisherKey
) -> None:
    web3 = FakeWeb3()
    relayer = CountingRelayer()

    checked = backend(deployment, publisher_key, web3, relayer).preflight()

    assert checked.chain_id == 31337
    assert checked.registry_address == REGISTRY
    assert checked.legacy_registry_address == LEGACY
    assert checked.owner_address == OWNER
    assert checked.publisher_address == PUBLISHER
    assert checked.relayer_address == RELAYER


def test_backend_requires_the_manifest_bound_dedicated_relayer(
    deployment: RegistryV2DeploymentManifest, publisher_key: PublisherKey
) -> None:
    with pytest.raises(RegistryV2PreflightFailed, match="dedicated relayer"):
        RegistryV2Backend(deployment, publisher_key, web3=FakeWeb3())

    wrong = RelayerKey.from_hex("23" * 32)
    with pytest.raises(RegistryV2PreflightFailed, match="manifest relayer"):
        RegistryV2Backend(
            deployment, publisher_key, relayer_key=wrong, web3=FakeWeb3()
        )


def test_pending_nonce_is_read_from_quorum(
    deployment: RegistryV2DeploymentManifest, publisher_key: PublisherKey
) -> None:
    instance = backend(deployment, publisher_key, FakeWeb3())

    class NonceQuorum:
        def call(self, method: str, params: list[object]) -> str:
            assert method == "eth_getTransactionCount"
            assert params == [RELAYER, "pending"]
            return "0x2a"

    instance.quorum = NonceQuorum()

    assert instance._pending_nonce() == 42


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("chain_id", 1, "chain"),
        ("legacy_registry", REGISTRY, "legacyRegistry"),
        ("owner", OPERATIONS, "owner"),
        ("authorized", False, "not authorized"),
    ],
)
def test_preflight_refuses_chain_and_registry_drift(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    field: str,
    value: object,
    message: str,
) -> None:
    web3 = FakeWeb3()
    setattr(web3.eth, field, value)

    with pytest.raises(RegistryV2PreflightFailed, match=message):
        backend(deployment, publisher_key, web3).preflight()


def test_prepare_verifies_report_and_signs_one_relayer_transaction(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
) -> None:
    web3 = FakeWeb3()
    relayer = CountingRelayer()

    prepared = backend(deployment, publisher_key, web3, relayer).prepare(
        reporter.sign_report(report), report_uri="ipfs://report"
    )

    assert relayer.signatures == 1
    assert prepared.correction_of == 0
    assert prepared.nonce == 7
    assert prepared.gas == 125_000
    assert prepared.maximum_fee_wei == 2_750_000
    decoded = decoded_transaction(prepared.raw)
    assert decoded["sender"] == RELAYER
    assert decoded["to"] == REGISTRY
    assert (
        decoded["data"][:4]
        == Web3.keccak(
            text=(
                "publish((bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,"
                "uint8,uint64,uint64,address,uint64,bytes32,string),bytes)"
            )
        )[:4]
    )


def test_prepare_correction_binds_parent_and_target(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
) -> None:
    web3 = FakeWeb3()
    asset_key = bytes(Web3.keccak(text=str(report["asset_key"])))
    epoch_key = bytes(Web3.keccak(text=str(report["epoch_id"])))
    previous_digest = bytes.fromhex("99" * 32)
    previous = (
        previous_digest,
        bytes.fromhex("44" * 32),
        bytes.fromhex("55" * 32),
        bytes.fromhex("66" * 32),
        bytes.fromhex("77" * 32),
        epoch_key,
        3,
        1,
        2,
        PUBLISHER,
        1,
        bytes(32),
        "ipfs://old",
    )
    web3.eth.latest[asset_key] = 1
    web3.eth.reports[(asset_key, 1)] = previous
    report["sequence"] = 2
    report["correction_of"] = 1

    prepared = backend(deployment, publisher_key, web3).prepare(
        reporter.sign_report(report),
        report_uri="ipfs://correction",
        correction_of=1,
    )

    assert prepared.attestation["parent_digest"] == previous_digest.hex()
    assert prepared.attestation["correction_of"] == 1
    assert (
        decoded_transaction(prepared.raw)["data"][:4]
        == Web3.keccak(
            text=(
                "publishCorrection(uint64,(bytes32,bytes32,bytes32,bytes32,bytes32,"
                "bytes32,bytes32,uint8,uint64,uint64,address,uint64,bytes32,string),bytes)"
            )
        )[:4]
    )


def test_prepare_refuses_invalid_ed25519_report(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
) -> None:
    signed = reporter.sign_report(report)
    signed["signature"] = "00" * 64

    with pytest.raises(RegistryV2PublicationError, match="Ed25519"):
        backend(deployment, publisher_key, FakeWeb3()).prepare(
            signed, report_uri="ipfs://report"
        )


def test_prepare_refuses_a_superseded_reporting_key_for_new_work(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
) -> None:
    successor = Ed25519Signer.from_seed(b"\x88" * 32).public_key_record()
    rotated = deployment.to_mapping()
    rotated["reporting_keys"] = [
        {
                "kid": deployment.active_key.kid,
                "public_key": deployment.active_key.public_key,
                "state": "superseded",
                "not_after": "2026-08-19T00:00:00Z",
            },
        {
            "kid": successor["kid"],
            "public_key": successor["public_key"],
            "state": "active",
        },
    ]
    manifest = RegistryV2DeploymentManifest.from_mapping(rotated)

    with pytest.raises(RegistryV2PublicationError, match="active reporting key"):
        backend(manifest, publisher_key, FakeWeb3()).prepare(
            reporter.sign_report(report), report_uri="ipfs://report"
        )


@pytest.mark.parametrize("as_text", [False, True])
def test_prepare_accepts_strict_serialized_report_envelopes(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
    as_text: bool,
) -> None:
    encoded = canonical_json_bytes(reporter.sign_report(report))
    envelope: bytes | str = encoded.decode("utf-8") if as_text else encoded

    prepared = backend(deployment, publisher_key, FakeWeb3()).prepare(
        envelope, report_uri="ipfs://report"
    )

    assert prepared.attestation["sequence"] == 1


def test_prepare_refuses_fee_above_manifest_ceiling(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
) -> None:
    web3 = FakeWeb3()
    web3.eth.base_fee = 10**12

    with pytest.raises(RegistryV2FeeCeilingExceeded, match="ceiling"):
        backend(deployment, publisher_key, web3).prepare(
            reporter.sign_report(report), report_uri="ipfs://report"
        )


def test_reconcile_compares_every_report_field_and_correction(
    deployment: RegistryV2DeploymentManifest,
    publisher_key: PublisherKey,
    reporter: Ed25519Signer,
    report: dict[str, object],
) -> None:
    web3 = FakeWeb3()
    instance = backend(deployment, publisher_key, web3)
    prepared = instance.prepare(
        reporter.sign_report(report), report_uri="ipfs://report"
    )
    asset_key = bytes.fromhex(str(prepared.attestation["asset_key"]))
    web3.eth.latest[asset_key] = 1
    web3.eth.reports[(asset_key, 1)] = prepared.report_input[1:]

    reconciled = instance.reconcile(prepared.attestation)

    assert reconciled.report_digest == prepared.attestation["report_digest"]
    assert reconciled.report_uri == "ipfs://report"
    assert reconciled.correction_of == 0

    stored = list(web3.eth.reports[(asset_key, 1)])
    stored[6] = 0
    web3.eth.reports[(asset_key, 1)] = tuple(stored)
    with pytest.raises(RegistryV2ReconciliationFailed, match="does not match"):
        instance.reconcile(prepared.attestation)
