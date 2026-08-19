from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from touchstone.deployment import (
    DeploymentError,
    DeploymentManifest,
    RegistryV2DeploymentManifest,
    load_deployment_manifest,
)


ROOT = Path(__file__).parents[1]
SCHEMA = json.loads(
    (ROOT / "deployments" / "manifest.schema.json").read_text(encoding="utf-8")
)
PUBLIC_KEY = "aa" * 32
KID = f"ed25519:{sha256(bytes.fromhex(PUBLIC_KEY)).hexdigest()}"


def manifest_v2(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "manifest_version": 2,
        "registry_version": 2,
        "network": "hardhat-local",
        "chain_id": 31337,
        "rpc_url": "http://127.0.0.1:8545",
        "registry_address": "0x1111111111111111111111111111111111111111",
        "registry_runtime_bytecode_sha256": "11" * 32,
        "legacy_registry_address": "0x2222222222222222222222222222222222222222",
        "legacy_registry_runtime_bytecode_sha256": "22" * 32,
        "owner_address": "0x3333333333333333333333333333333333333333",
        "relayer_address": "0x6666666666666666666666666666666666666666",
        "publisher_address": "0x4444444444444444444444444444444444444444",
        "publisher_identity_address": "0x4444444444444444444444444444444444444444",
        "deployer_address": "0x3333333333333333333333333333333333333333",
        "operations_address": "0x5555555555555555555555555555555555555555",
        "confirmations": 1,
        "deployment_block": 1,
        "deployment_state": "active",
        "deployment_transaction": f"0x{'ab' * 32}",
        "authorization_transaction": f"0x{'cd' * 32}",
        "reporting_keys": [{"kid": KID, "public_key": PUBLIC_KEY, "state": "active"}],
    }
    value.update(overrides)
    return value


def test_registry_v2_manifest_round_trips_and_dispatches(tmp_path: Path) -> None:
    expected = RegistryV2DeploymentManifest.from_mapping(manifest_v2())
    assert RegistryV2DeploymentManifest.from_mapping(expected.to_mapping()) == expected
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(manifest_v2()), encoding="utf-8")
    loaded = load_deployment_manifest(path)
    assert isinstance(loaded, RegistryV2DeploymentManifest)
    assert loaded.owner_address == loaded.deployer_address
    assert loaded.legacy_registry_address != loaded.registry_address


def test_v1_loader_and_dispatch_remain_backward_compatible(tmp_path: Path) -> None:
    source = ROOT / "deployments" / "xlayer-mainnet.json"
    direct = DeploymentManifest.load(source)
    dispatched = load_deployment_manifest(source)
    assert type(dispatched) is DeploymentManifest
    assert dispatched == direct

    path = tmp_path / "v2.json"
    path.write_text(json.dumps(manifest_v2()), encoding="utf-8")
    with pytest.raises(DeploymentError, match="unknown fields"):
        DeploymentManifest.load(path)


def test_v2_loader_allows_owner_and_publisher_rotation() -> None:
    rotated = RegistryV2DeploymentManifest.from_mapping(
        manifest_v2(
            owner_address="0x7777777777777777777777777777777777777777",
            publisher_address="0x8888888888888888888888888888888888888888",
        )
    )

    assert rotated.owner_address != rotated.deployer_address
    assert rotated.publisher_identity_address != rotated.publisher_address


def test_v2_loader_refuses_crossed_roles() -> None:
    with pytest.raises(DeploymentError, match="owner_address must differ"):
        RegistryV2DeploymentManifest.from_mapping(
            manifest_v2(owner_address="0x4444444444444444444444444444444444444444")
        )
    with pytest.raises(DeploymentError, match="legacy_registry_address must differ"):
        RegistryV2DeploymentManifest.from_mapping(
            manifest_v2(
                legacy_registry_address="0x4444444444444444444444444444444444444444"
            )
        )
    with pytest.raises(DeploymentError, match="relayer_address must differ"):
        RegistryV2DeploymentManifest.from_mapping(
            manifest_v2(relayer_address="0x5555555555555555555555555555555555555555")
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("registry_version", 1, "registry_version must be 2"),
        (
            "legacy_registry_runtime_bytecode_sha256",
            "AA" * 32,
            "lowercase SHA-256",
        ),
        ("deployment_transaction", "0xAB", "lowercase 32-byte"),
        ("authorization_transaction", "ab" * 32, "lowercase 32-byte"),
    ],
)
def test_v2_loader_refuses_invalid_provenance_fields(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(DeploymentError, match=message):
        RegistryV2DeploymentManifest.from_mapping(manifest_v2(**{field: value}))


def test_schema_accepts_v1_and_v2_but_rejects_mixed_shapes() -> None:
    v1 = json.loads(
        (ROOT / "deployments" / "xlayer-mainnet.json").read_text(encoding="utf-8")
    )
    assert not list(Draft202012Validator(SCHEMA).iter_errors(v1))
    assert not list(Draft202012Validator(SCHEMA).iter_errors(manifest_v2()))

    mixed = dict(v1)
    mixed["owner_address"] = mixed["deployer_address"]
    assert list(Draft202012Validator(SCHEMA).iter_errors(mixed))

    missing = manifest_v2()
    del missing["legacy_registry_address"]
    assert list(Draft202012Validator(SCHEMA).iter_errors(missing))
