"""A manifest is the publisher's only description of where it is about to write.

These tests are about refusals. Anything a manifest fails to pin is something the endpoint
gets to decide unchallenged, so the interesting cases are the ones that must not load.
"""

from collections.abc import Iterator, Mapping
import json
from pathlib import Path

import pytest

from touchstone.deployment import (
    DeploymentError,
    DeploymentManifest,
    runtime_bytecode_sha256,
)
from touchstone.signing import Ed25519Signer, kid_for_public_key


# Lowercase and letter-bearing, so checksumming is observable rather than a no-op.
REGISTRY = "0x" + "ab" * 20
PUBLISHER = "0x" + "cd" * 20
IDENTITY = "0x" + "cd" * 20
DEPLOYER = "0x" + "ef" * 20
OPERATIONS = "0x" + "ba" * 20
PUBLIC_KEY = Ed25519Signer.from_seed(bytes(range(32))).public_key_record()["public_key"]
KID = kid_for_public_key(bytes.fromhex(PUBLIC_KEY))


def manifest(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "manifest_version": 1,
        "network": "hardhat-local",
        "chain_id": 31337,
        "rpc_url": "http://127.0.0.1:8545",
        "registry_address": REGISTRY,
        "registry_runtime_bytecode_sha256": "ab" * 32,
        "publisher_address": PUBLISHER,
        "publisher_identity_address": IDENTITY,
        "deployer_address": DEPLOYER,
        "operations_address": OPERATIONS,
        "confirmations": 1,
        "deployment_block": 7,
        "reporting_keys": [{"kid": KID, "public_key": PUBLIC_KEY, "state": "active"}],
    }
    value.update(overrides)
    return {key: item for key, item in value.items() if item is not ...}


def test_a_complete_manifest_loads_and_normalizes_addresses() -> None:
    loaded = DeploymentManifest.from_mapping(manifest())

    assert loaded.chain_id == 31337
    assert loaded.is_local
    assert loaded.registry_address.startswith("0x")
    assert loaded.registry_address != REGISTRY, "addresses are stored checksummed"
    assert loaded.active_key.kid == KID
    assert loaded.deployment_block == 7


def test_a_manifest_round_trips_through_its_on_disk_shape() -> None:
    loaded = DeploymentManifest.from_mapping(manifest())

    assert DeploymentManifest.from_mapping(loaded.to_mapping()) == loaded


@pytest.mark.parametrize(
    "field",
    [
        "manifest_version",
        "network",
        "chain_id",
        "rpc_url",
        "registry_address",
        "registry_runtime_bytecode_sha256",
        "publisher_address",
        "publisher_identity_address",
        "deployer_address",
        "operations_address",
        "confirmations",
        "reporting_keys",
    ],
)
def test_every_required_field_is_required(field: str) -> None:
    value = manifest()
    del value[field]

    with pytest.raises(DeploymentError, match="missing fields"):
        DeploymentManifest.from_mapping(value)


def test_a_role_address_may_not_be_left_unstated() -> None:
    """An unstated role address cannot be shown to be separate from the publisher.

    These were optional at first, which made the whole four-identity separation optional:
    a manifest that simply omitted the deployer and operations addresses passed every
    check while proving nothing about either.
    """
    for field in (
        "deployer_address",
        "operations_address",
        "publisher_identity_address",
    ):
        value = manifest()
        del value[field]
        with pytest.raises(DeploymentError, match=f"missing fields.*{field}"):
            DeploymentManifest.from_mapping(value)


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    """A misspelled field that is silently dropped is a pin that was never applied."""
    with pytest.raises(DeploymentError, match="unknown fields"):
        DeploymentManifest.from_mapping(manifest(registry_addres=REGISTRY))


def test_every_network_name_is_bound_to_one_chain_id() -> None:
    """A name not bound to a chain id proves nothing.

    Only the local chain was pinned at first, so any positive integer could be declared as
    xlayer-testnet — including 195, X Layer's *deprecated* testnet. Preflight would then
    confirm only that the endpoint agrees with an obsolete manifest, which is exactly the
    disagreement it exists to find.
    """
    with pytest.raises(DeploymentError, match="hardhat-local is chain 31337"):
        DeploymentManifest.from_mapping(manifest(chain_id=196))
    with pytest.raises(DeploymentError, match="xlayer-mainnet is chain 196"):
        DeploymentManifest.from_mapping(
            manifest(
                network="xlayer-mainnet",
                chain_id=31337,
                rpc_url="https://rpc.example",
                max_fee_wei=1,
            )
        )
    with pytest.raises(DeploymentError, match="xlayer-testnet is chain 1952"):
        DeploymentManifest.from_mapping(
            manifest(
                network="xlayer-testnet",
                chain_id=195,
                rpc_url="https://testrpc.xlayer.tech",
                max_fee_wei=1,
            )
        )


def test_a_public_endpoint_may_carry_a_path_but_never_a_secret() -> None:
    """X Layer's own testnet endpoint ends in /terigon, so a path cannot be refused.

    A query or fragment can be, and is: that is where an API key would sit in a file that
    gets committed. A key hidden in a path segment is indistinguishable from a required
    one, and this check does not pretend otherwise.
    """
    accepted = DeploymentManifest.from_mapping(
        manifest(
            network="xlayer-testnet",
            chain_id=1952,
            rpc_url="https://testrpc.xlayer.tech/terigon",
            max_fee_wei=1,
        )
    )
    assert accepted.rpc_url.endswith("/terigon")

    for url in (
        "https://rpc.example?apikey=secret",
        "https://rpc.example/v2#token",
    ):
        with pytest.raises(DeploymentError, match="query or fragment"):
            DeploymentManifest.from_mapping(
                manifest(
                    network="xlayer-testnet",
                    chain_id=1952,
                    rpc_url=url,
                    max_fee_wei=1,
                )
            )


def test_every_spelling_of_loopback_is_refused_for_a_public_network() -> None:
    """One literal comparison missed every alias.

    Each of these reaches this machine: the rest of 127.0.0.0/8, the IPv4-mapped IPv6
    form, and the numeric shorthands the platform resolver expands — 127.1, 2130706433 and
    0x7f000001 all mean 127.0.0.1.
    """
    for host in (
        "https://127.0.0.1",
        "https://127.0.0.2",
        "https://127.255.255.254",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
        "https://localhost",
        "https://LOCALHOST",
        "https://localhost.",
        "https://api.localhost",
        "https://127.1",
        "https://2130706433",
        "https://0x7f000001",
    ):
        with pytest.raises(DeploymentError):
            DeploymentManifest.from_mapping(
                manifest(
                    network="xlayer-mainnet",
                    chain_id=196,
                    rpc_url=host,
                    max_fee_wei=1,
                )
            )

    # And a real endpoint, path and all, still loads.
    accepted = DeploymentManifest.from_mapping(
        manifest(
            network="xlayer-testnet",
            chain_id=1952,
            rpc_url="https://testrpc.xlayer.tech/terigon",
            max_fee_wei=1,
        )
    )
    assert accepted.rpc_url == "https://testrpc.xlayer.tech/terigon"


def test_a_public_network_must_be_https_without_credentials() -> None:
    for url in (
        "http://rpc.example",
        "https://user:secret@rpc.example",
        "https://127.0.0.1:8545",
    ):
        with pytest.raises(DeploymentError):
            DeploymentManifest.from_mapping(
                manifest(
                    network="xlayer-mainnet",
                    chain_id=196,
                    rpc_url=url,
                    max_fee_wei=1,
                )
            )


def test_a_public_network_requires_a_fee_ceiling() -> None:
    with pytest.raises(DeploymentError, match="max_fee_wei is required"):
        DeploymentManifest.from_mapping(
            manifest(
                network="xlayer-testnet", chain_id=1952, rpc_url="https://rpc.example"
            )
        )


def test_the_local_rpc_url_is_loopback_only() -> None:
    for url in (
        "https://rpc.example",
        "http://localhost:8545@rpc.example",
        "http://127.0.0.1:8545/path",
        "http://127.0.0.1",
    ):
        with pytest.raises(DeploymentError, match="local loopback"):
            DeploymentManifest.from_mapping(manifest(rpc_url=url))


@pytest.mark.parametrize(
    "overrides",
    [
        {"publisher_address": DEPLOYER},
        {"operations_address": PUBLISHER},
        {"operations_address": DEPLOYER},
        {"registry_address": PUBLISHER},
    ],
)
def test_role_addresses_must_be_distinct(overrides: dict[str, str]) -> None:
    with pytest.raises(DeploymentError):
        DeploymentManifest.from_mapping(manifest(**overrides))


def test_the_bytecode_digest_must_be_a_lowercase_sha256() -> None:
    for bad in ("AB" * 32, "ab" * 31, "0x" + "ab" * 32, 1):
        with pytest.raises(DeploymentError, match="registry_runtime_bytecode_sha256"):
            DeploymentManifest.from_mapping(
                manifest(registry_runtime_bytecode_sha256=bad)
            )


def test_confirmations_must_be_positive_and_not_a_boolean() -> None:
    for bad in (0, -1, True, 1.0, "1"):
        with pytest.raises(DeploymentError, match="confirmations"):
            DeploymentManifest.from_mapping(manifest(confirmations=bad))


def test_a_reporting_kid_must_match_its_public_key() -> None:
    with pytest.raises(DeploymentError, match="does not match its public key"):
        DeploymentManifest.from_mapping(
            manifest(
                reporting_keys=[
                    {
                        "kid": "ed25519:" + "00" * 32,
                        "public_key": PUBLIC_KEY,
                        "state": "active",
                    }
                ]
            )
        )


def test_exactly_one_reporting_key_is_active() -> None:
    other = Ed25519Signer.from_seed(bytes(range(1, 33))).public_key_record()
    for keys in (
        [],
        [
            {"kid": KID, "public_key": PUBLIC_KEY, "state": "active"},
            {"kid": other["kid"], "public_key": other["public_key"], "state": "active"},
        ],
        [
            {
                "kid": KID,
                "public_key": PUBLIC_KEY,
                "state": "superseded",
                "not_after": "2026-08-15T00:00:00Z",
            }
        ],
    ):
        with pytest.raises(DeploymentError):
            DeploymentManifest.from_mapping(manifest(reporting_keys=keys))


def test_a_retired_key_must_record_when_it_stopped_signing() -> None:
    other = Ed25519Signer.from_seed(bytes(range(1, 33))).public_key_record()
    with pytest.raises(DeploymentError, match="without recording when"):
        DeploymentManifest.from_mapping(
            manifest(
                reporting_keys=[
                    {"kid": KID, "public_key": PUBLIC_KEY, "state": "active"},
                    {
                        "kid": other["kid"],
                        "public_key": other["public_key"],
                        "state": "superseded",
                    },
                ]
            )
        )


def test_an_active_key_cannot_carry_a_retirement_instant() -> None:
    with pytest.raises(DeploymentError, match="cannot carry not_after"):
        DeploymentManifest.from_mapping(
            manifest(
                reporting_keys=[
                    {
                        "kid": KID,
                        "public_key": PUBLIC_KEY,
                        "state": "active",
                        "not_after": "2026-08-15T00:00:00Z",
                    }
                ]
            )
        )


def test_a_key_cannot_be_listed_twice() -> None:
    with pytest.raises(DeploymentError, match="listed twice"):
        DeploymentManifest.from_mapping(
            manifest(
                reporting_keys=[
                    {"kid": KID, "public_key": PUBLIC_KEY, "state": "active"},
                    {
                        "kid": KID,
                        "public_key": PUBLIC_KEY,
                        "state": "revoked",
                        "not_after": "2026-08-15T00:00:00Z",
                    },
                ]
            )
        )


def test_a_template_cannot_be_used_as_a_deployment() -> None:
    with pytest.raises(DeploymentError, match="template"):
        DeploymentManifest.from_mapping(
            manifest(notes="TEMPLATE — replace every value before use")
        )


def test_the_committed_templates_are_templates_and_are_refused() -> None:
    """They must be well-formed enough to copy, and still impossible to publish from."""
    templates = sorted(
        (Path(__file__).parents[1] / "deployments").glob("*.template.json")
    )

    assert [path.name for path in templates] == [
        "xlayer-mainnet.template.json",
        "xlayer-testnet.template.json",
    ]
    for path in templates:
        value = json.loads(path.read_text(encoding="utf-8"))
        with pytest.raises(DeploymentError, match="template"):
            DeploymentManifest.from_mapping(value)
        # Everything except the marker must already be valid, so a copy only needs the
        # values replaced rather than the shape debugged.
        DeploymentManifest.from_mapping(
            {k: v for k, v in value.items() if k != "notes"}
        )


def test_loading_a_missing_or_malformed_file_is_a_deployment_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(DeploymentError, match="cannot read"):
        DeploymentManifest.load(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_bytes(b"{not json")
    with pytest.raises(DeploymentError, match="strict JSON"):
        DeploymentManifest.load(broken)


def test_a_manifest_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")

    assert DeploymentManifest.load(path).chain_id == 31337


def test_runtime_bytecode_must_exist_to_be_digested() -> None:
    assert len(runtime_bytecode_sha256(b"\x60\x80")) == 64
    with pytest.raises(DeploymentError, match="no runtime bytecode"):
        runtime_bytecode_sha256(b"")
    with pytest.raises(TypeError):
        runtime_bytecode_sha256("0x6080")


class _WithdrawingManifest(Mapping):
    """A mapping that presents a complete manifest and then withdraws a field.

    The schema was checked by iterating the caller's mapping twice, and the values were
    then read from it field by field all the way through validation. A mapping that
    answered the schema and then dropped `reporting_keys` was therefore validated as
    complete and built as a KeyError.
    """

    def __init__(self, value: dict[str, object]) -> None:
        self._value = value
        self.iterations = 0

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        return iter(self._value)

    def __getitem__(self, key: str) -> object:
        if key == "reporting_keys" and self.iterations >= 2:
            raise KeyError(key)
        return self._value[key]

    def __len__(self) -> int:
        return len(self._value)


def test_a_manifest_is_validated_and_built_from_one_reading() -> None:
    withdrawing = _WithdrawingManifest(manifest())

    loaded = DeploymentManifest.from_mapping(withdrawing)

    assert loaded.active_key.kid == KID
    assert loaded == DeploymentManifest.from_mapping(manifest())


class _HostileMapping(Mapping):
    """Traversal runs caller code, and caller code may raise anything at all."""

    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __getitem__(self, key: str) -> object:
        if key == "reporting_keys":
            raise RuntimeError("mapping changed during snapshot")
        return self._value[key]

    def __len__(self) -> int:
        return len(self._value)


def test_a_manifest_that_cannot_be_snapshotted_is_a_deployment_error() -> None:
    """The declared boundary is DeploymentError, so nothing else may come out of it."""
    with pytest.raises(DeploymentError):
        DeploymentManifest.from_mapping(_HostileMapping(manifest()))


class _UnrenderableFailure(Exception):
    """An exception that refuses to describe itself."""

    def __str__(self) -> str:
        raise KeyError("this exception cannot be rendered")


class _UnrenderableMapping(Mapping):
    """Fails traversal with an exception whose own string conversion raises."""

    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __getitem__(self, key: str) -> object:
        raise _UnrenderableFailure()

    def __len__(self) -> int:
        return len(self._value)


def test_a_failure_that_cannot_describe_itself_is_still_a_deployment_error() -> None:
    """The handler that contains arbitrary failures was itself running caller code.

    Interpolating the caught exception invokes its `__str__`, so one that raises escaped
    the very handler written to contain it — and a RuntimeError with an ordinary message
    does not exercise that, because rendering it succeeds.
    """
    with pytest.raises(DeploymentError):
        DeploymentManifest.from_mapping(_UnrenderableMapping(manifest()))
