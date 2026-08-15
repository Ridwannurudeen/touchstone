"""The pinned description of one deployment, and the only source of publishing targets.

Everything the publisher needs to reach a chain lives in a committed manifest: endpoint,
chain id, registry address, the runtime bytecode that address is expected to hold, the
publishing identity, the confirmation depth, the fee ceiling, and the reporting keys.

Nothing here has a default. A missing field is a refusal, never a fallback, because a
fallback is how a report reaches the wrong chain: an endpoint that quietly answers for a
different network, an address that used to hold the registry, a publisher key that no
longer carries authority. Each of those is a silent failure unless something declares what
was expected and compares.

The manifest is a declaration, not evidence. It says what should be true. The publisher
verifies each claim against the endpoint before it signs anything, and refuses on any
disagreement — see ``touchstone.publish.SignedRegistryBackend.preflight``.

Secrets are deliberately absent. A manifest is committed to the repository and carries
only public identifiers; private keys arrive from the environment and are checked against
the addresses declared here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from web3 import Web3

from touchstone.signing import kid_for_public_key, strict_json_loads


MANIFEST_VERSION = 1

# Each network name is bound to exactly one chain id. Leaving public ids to be declared
# per manifest was wrong: preflight then proves only that the endpoint agrees with
# whatever the manifest says, which is no help when the manifest itself names a chain that
# is no longer the network it claims to be. X Layer's deprecated testnet on chain 195 was
# accepted as "xlayer-testnet" under that rule.
#
# Values verified 2026-08-15: chainlist.org/chain/1952 (testnet, currently at
# https://testrpc.xlayer.tech/terigon; the older chain 195 is deprecated) and
# chainid.network/chain/196 (mainnet).
LOCAL_NETWORK = "hardhat-local"
LOCAL_CHAIN_ID = 31337
NETWORK_CHAIN_IDS = {
    LOCAL_NETWORK: LOCAL_CHAIN_ID,
    "xlayer-testnet": 1952,
    "xlayer-mainnet": 196,
}
NETWORKS = frozenset(NETWORK_CHAIN_IDS)
KEY_STATES = frozenset({"active", "superseded", "revoked"})

# A template is a shape to copy, never a target to publish to. Half-filled templates are
# the obvious failure — someone replaces the address and forgets the bytecode digest — so
# the marker makes a template unusable as a manifest until it is deliberately removed.
TEMPLATE_MARKER = "TEMPLATE"

_MANIFEST_FIELDS = frozenset(
    {
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
        "max_fee_wei",
        "deployment_block",
        "reporting_keys",
        "notes",
    }
)
_REQUIRED_MANIFEST_FIELDS = frozenset(
    {
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
    }
)
_KEY_FIELDS = frozenset({"kid", "public_key", "state", "not_after"})
_REQUIRED_KEY_FIELDS = frozenset({"kid", "public_key", "state"})
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class DeploymentError(ValueError):
    """A manifest does not describe a deployment this publisher will act on."""


@dataclass(frozen=True, slots=True)
class ReportingKey:
    """One published Ed25519 reporting key and its lifecycle state."""

    kid: str
    public_key: str
    state: str
    not_after: str | None

    @property
    def verifiable(self) -> bool:
        """Whether signatures by this key are still to be trusted.

        A superseded key stays trusted: it signed reports that remain true, and the
        bundles carrying them are already published. Revocation is the opposite claim —
        that the key may have signed something its holder did not intend — so it removes
        trust retroactively.
        """
        return self.state in {"active", "superseded"}


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    """A validated deployment target. Construct through ``load`` or ``from_mapping``."""

    manifest_version: int
    network: str
    chain_id: int
    rpc_url: str
    registry_address: str
    registry_runtime_bytecode_sha256: str
    publisher_address: str
    publisher_identity_address: str
    deployer_address: str
    operations_address: str
    confirmations: int
    max_fee_wei: int | None
    deployment_block: int
    reporting_keys: tuple[ReportingKey, ...]
    notes: str | None

    @property
    def is_local(self) -> bool:
        return self.network == LOCAL_NETWORK

    @property
    def active_key(self) -> ReportingKey:
        """The one key that signs new reports."""
        return next(key for key in self.reporting_keys if key.state == "active")

    def key(self, kid: str) -> ReportingKey | None:
        return next((key for key in self.reporting_keys if key.kid == kid), None)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> DeploymentManifest:
        """Read and validate a manifest file."""
        location = Path(path)
        try:
            raw = location.read_bytes()
        except OSError as error:
            raise DeploymentError(
                f"cannot read deployment manifest: {error}"
            ) from error
        try:
            value = strict_json_loads(raw)
        except (TypeError, ValueError) as error:
            raise DeploymentError(
                f"deployment manifest {location} is not strict JSON: {error}"
            ) from error
        return cls.from_mapping(value)

    @classmethod
    def from_mapping(cls, value: object) -> DeploymentManifest:
        """Validate a decoded manifest, rejecting anything unrecognised."""
        if not isinstance(value, Mapping):
            raise DeploymentError("deployment manifest must be an object")
        unknown = set(value) - _MANIFEST_FIELDS
        if unknown:
            raise DeploymentError(
                f"deployment manifest has unknown fields: {sorted(unknown)}"
            )
        missing = _REQUIRED_MANIFEST_FIELDS - set(value)
        if missing:
            raise DeploymentError(
                f"deployment manifest is missing fields: {sorted(missing)}"
            )
        if value["manifest_version"] != MANIFEST_VERSION:
            raise DeploymentError("deployment manifest version is not supported")

        network = value["network"]
        if network not in NETWORKS:
            raise DeploymentError(f"unsupported network: {network!r}")
        chain_id = _positive_int(value["chain_id"], "chain_id")
        if chain_id != NETWORK_CHAIN_IDS[network]:
            raise DeploymentError(
                f"{network} is chain {NETWORK_CHAIN_IDS[network]}, manifest declares "
                f"{chain_id}"
            )

        rpc_url = value["rpc_url"]
        if network == LOCAL_NETWORK:
            validate_local_rpc_url(rpc_url)
        else:
            _validate_remote_rpc_url(rpc_url)

        registry_address = _address(value["registry_address"], "registry_address")
        publisher_address = _address(value["publisher_address"], "publisher_address")
        identity_address = _address(
            value["publisher_identity_address"], "publisher_identity_address"
        )
        deployer_address = _address(value["deployer_address"], "deployer_address")
        operations_address = _address(value["operations_address"], "operations_address")
        # Every one of these is required rather than optional. An absent role address is
        # not a relaxed check, it is an unprovable one: nothing can then show that the
        # publisher is not also the identity funding it or the identity that owns the
        # registry, and "we did not say" reads identically to "they are separate".
        if deployer_address == publisher_address:
            raise DeploymentError(
                "publisher_address must differ from deployer_address; the identity that "
                "owns the registry must not be the identity that runs unattended"
            )
        if operations_address in {publisher_address, deployer_address}:
            raise DeploymentError(
                "operations_address must differ from the publisher and deployer; it "
                "funds them and must not also be able to act as them"
            )
        if registry_address in {
            publisher_address,
            deployer_address,
            operations_address,
        }:
            raise DeploymentError("no role address may be the registry itself")
        # Lineage is not a fourth role — it is which publishing identity the registry
        # recorded, and for a first authorization it equals the publisher. What it must
        # never be is one of the other roles, which would mean the deployer or the
        # funding identity had once been authorized to publish.
        if identity_address in {deployer_address, operations_address}:
            raise DeploymentError(
                "publisher_identity_address must not be the deployer or the operations "
                "identity; neither may ever have been an authorized publisher"
            )

        bytecode_hash = value["registry_runtime_bytecode_sha256"]
        if (
            not isinstance(bytecode_hash, str)
            or _DIGEST.fullmatch(bytecode_hash) is None
        ):
            raise DeploymentError(
                "registry_runtime_bytecode_sha256 must be a lowercase SHA-256 digest"
            )

        confirmations = _positive_int(value["confirmations"], "confirmations")
        max_fee_wei = (
            _positive_int(value["max_fee_wei"], "max_fee_wei")
            if value.get("max_fee_wei") is not None
            else None
        )
        if max_fee_wei is None and network != LOCAL_NETWORK:
            raise DeploymentError(
                "max_fee_wei is required off the local chain; an unbounded fee ceiling "
                "is an owner decision that must be written down"
            )
        deployment_block = value.get("deployment_block", 0)
        if type(deployment_block) is not int or deployment_block < 0:
            raise DeploymentError("deployment_block must be a non-negative integer")

        notes = value.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise DeploymentError("notes must be text")
        if isinstance(notes, str) and notes.startswith(TEMPLATE_MARKER):
            raise DeploymentError(
                "this is a deployment template, not a deployment; every value must be "
                "replaced with one from an actual deployment and the marker removed"
            )

        return cls(
            manifest_version=MANIFEST_VERSION,
            network=network,
            chain_id=chain_id,
            rpc_url=rpc_url,
            registry_address=registry_address,
            registry_runtime_bytecode_sha256=bytecode_hash,
            publisher_address=publisher_address,
            publisher_identity_address=identity_address,
            deployer_address=deployer_address,
            operations_address=operations_address,
            confirmations=confirmations,
            max_fee_wei=max_fee_wei,
            deployment_block=deployment_block,
            reporting_keys=_reporting_keys(value["reporting_keys"]),
            notes=notes,
        )

    def to_mapping(self) -> dict[str, object]:
        """Render back to the committed on-disk shape, omitting absent optionals."""
        value: dict[str, object] = {
            "manifest_version": self.manifest_version,
            "network": self.network,
            "chain_id": self.chain_id,
            "rpc_url": self.rpc_url,
            "registry_address": self.registry_address,
            "registry_runtime_bytecode_sha256": self.registry_runtime_bytecode_sha256,
            "publisher_address": self.publisher_address,
            "publisher_identity_address": self.publisher_identity_address,
            "deployer_address": self.deployer_address,
            "operations_address": self.operations_address,
            "confirmations": self.confirmations,
            "deployment_block": self.deployment_block,
            "reporting_keys": [
                {
                    key_field: getattr(key, key_field)
                    for key_field in ("kid", "public_key", "state", "not_after")
                    if getattr(key, key_field) is not None
                }
                for key in self.reporting_keys
            ],
        }
        if self.max_fee_wei is not None:
            value["max_fee_wei"] = self.max_fee_wei
        if self.notes is not None:
            value["notes"] = self.notes
        return value


def runtime_bytecode_sha256(code: bytes) -> str:
    """Digest deployed runtime bytecode the one way this project compares it."""
    if not isinstance(code, bytes):
        raise TypeError("code must be bytes")
    if not code:
        raise DeploymentError("there is no runtime bytecode to digest")
    return hashlib.sha256(code).hexdigest()


def validate_local_rpc_url(value: object) -> None:
    """Accept only a loopback HTTP endpoint with no credentials, path, query or fragment."""
    if not isinstance(value, str):
        raise DeploymentError("rpc_url must identify the local loopback host")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise DeploymentError(
            "rpc_url must identify the local loopback host"
        ) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DeploymentError("rpc_url must identify the local loopback host")


def _validate_remote_rpc_url(value: object) -> None:
    """A public network is reached over HTTPS, without credentials in the URL.

    Userinfo credentials and a query or fragment are refused. Both are places an API key
    is conventionally carried, and a manifest is committed, so a key there would be in the
    repository and in every log line that echoes the endpoint.

    A **path** is allowed, because a path is not evidence of a secret: X Layer's own
    testnet endpoint is ``https://testrpc.xlayer.tech/terigon``. This is the honest limit
    of the check — a provider that puts a key in the path is indistinguishable from a
    required path segment, so this refuses what it can prove and does not pretend to
    catch the rest.
    """
    if not isinstance(value, str):
        raise DeploymentError("rpc_url must be an HTTPS endpoint")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as error:
        raise DeploymentError("rpc_url must be an HTTPS endpoint") from error
    if parsed.scheme != "https" or not parsed.hostname:
        raise DeploymentError("rpc_url must be an HTTPS endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise DeploymentError("rpc_url must not carry credentials")
    if parsed.query or parsed.fragment:
        raise DeploymentError(
            "rpc_url must not carry a query or fragment; an API key belongs in the "
            "environment, not in a committed manifest"
        )
    if _is_loopback(parsed.hostname):
        raise DeploymentError("a public network cannot be served from loopback")


def _is_loopback(hostname: str | None) -> bool:
    """Whether a hostname resolves to this machine by any of its spellings.

    Comparing against two literal strings missed every alias: the whole 127.0.0.0/8 block,
    the IPv6 form, and a fully-qualified "localhost." with its trailing root dot. Each of
    them reaches the same place, so each must be refused for a network claiming to be
    public.
    """
    if not hostname:
        return True
    name = hostname.strip().lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    bare = name.strip("[]")
    if bare in {"::1", "0:0:0:0:0:0:0:1"}:
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def _reporting_keys(value: object) -> tuple[ReportingKey, ...]:
    if not isinstance(value, list) or not value:
        raise DeploymentError("reporting_keys must be a nonempty array")
    keys: list[ReportingKey] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            raise DeploymentError("each reporting key must be an object")
        unknown = set(entry) - _KEY_FIELDS
        if unknown:
            raise DeploymentError(
                f"reporting key has unknown fields: {sorted(unknown)}"
            )
        missing = _REQUIRED_KEY_FIELDS - set(entry)
        if missing:
            raise DeploymentError(f"reporting key is missing fields: {sorted(missing)}")
        public_key = entry["public_key"]
        if not isinstance(public_key, str) or _DIGEST.fullmatch(public_key) is None:
            raise DeploymentError(
                "reporting key public_key must be 32 lowercase hexadecimal bytes"
            )
        kid = entry["kid"]
        if kid != kid_for_public_key(bytes.fromhex(public_key)):
            raise DeploymentError(
                f"reporting key kid does not match its public key: {kid}"
            )
        if kid in seen:
            raise DeploymentError(f"reporting key {kid} is listed twice")
        seen.add(kid)
        state = entry["state"]
        if state not in KEY_STATES:
            raise DeploymentError(f"reporting key state is invalid: {state!r}")
        not_after = entry.get("not_after")
        if state == "active":
            if not_after is not None:
                raise DeploymentError("an active reporting key cannot carry not_after")
        elif not_after is None:
            raise DeploymentError(
                f"reporting key {kid} is {state} without recording when it stopped signing"
            )
        else:
            _validate_timestamp(not_after, kid)
        keys.append(
            ReportingKey(
                kid=kid, public_key=public_key, state=state, not_after=not_after
            )
        )
    active = [key for key in keys if key.state == "active"]
    if len(active) != 1:
        raise DeploymentError(
            f"exactly one reporting key must be active, found {len(active)}"
        )
    return tuple(keys)


def _validate_timestamp(value: object, context: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeploymentError(f"{context} not_after must be a normalized UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise DeploymentError(
            f"{context} not_after must be a normalized UTC timestamp"
        ) from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise DeploymentError(f"{context} not_after must be a normalized UTC timestamp")


def _address(value: object, field: str) -> str:
    if not isinstance(value, str) or _ADDRESS.fullmatch(value) is None:
        raise DeploymentError(f"{field} must be a 20-byte hexadecimal address")
    if int(value, 16) == 0:
        raise DeploymentError(f"{field} must not be the zero address")
    return Web3.to_checksum_address(value)


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise DeploymentError(f"{field} must be a positive integer")
    return value
