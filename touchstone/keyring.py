"""Role identities and the keys that hold them.

Four identities exist, and they are separate because each one fails differently.

**Deployer** deploys the registry, owns it, and is the only identity that can authorize,
revoke or rotate a publisher. Losing it means losing control of the deployment, so it never
appears in the publishing runtime — this module refuses to run as it.

**Publisher** signs registry writes. Its entire onchain authority is to append reports, so
a stolen publisher key can publish a false report but cannot revoke anyone, cannot rotate
authority, and cannot rewrite what is already there.

**Reporter** signs observation reports with Ed25519 and has no chain authority whatsoever.
Compromise here is different in kind: it forges the *content* rather than the *placement*,
and the registry cannot detect it. That is why the two keys are never the same secret.

**Operations** funds gas and runs the host. It signs nothing this project publishes, and is
recorded only so its address is attributable and so the publisher refuses to run as it.

Private keys never live in a manifest. They arrive from the environment, and the address or
key id each one derives is compared against the committed manifest before anything is
signed. A key that does not match the declared identity is a refusal, not a warning: the
alternative is publishing correctly-formed reports from an identity nobody authorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re

import rlp
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction as LegacyTransaction
from eth_account.signers.local import LocalAccount
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from web3 import Web3

from touchstone.deployment import DeploymentError, DeploymentManifest
from touchstone.signing import (
    Ed25519Signer,
    SIGNING_SEED_ENV,
    kid_for_public_key,
)


PUBLISHER_KEY_ENV = "TOUCHSTONE_PUBLISHER_PRIVATE_KEY"
_LOWER_HEX_32 = re.compile(r"[0-9a-f]{64}")


class IdentityError(RuntimeError):
    """Base class for identity failures."""


class MissingKeyMaterial(IdentityError):
    """A required key is not present in the environment."""


class IdentityMismatch(IdentityError):
    """Key material was found, but it is not the identity the manifest declares."""


class RoleConflict(IdentityError):
    """One secret is holding two roles that must fail independently."""


@dataclass(frozen=True, slots=True)
class PublisherKey:
    """The secp256k1 key that signs registry transactions, bound to a manifest."""

    account: LocalAccount
    address: str

    @classmethod
    def from_env(cls, manifest: DeploymentManifest) -> PublisherKey:
        """Load the publisher key and prove it is the identity the manifest declares."""
        encoded = os.environ.get(PUBLISHER_KEY_ENV)
        if encoded is None:
            raise MissingKeyMaterial(f"{PUBLISHER_KEY_ENV} is not set")
        return cls.from_hex(encoded, manifest)

    @classmethod
    def from_hex(cls, encoded: str, manifest: DeploymentManifest) -> PublisherKey:
        """Construct from explicit key material, checked against the manifest.

        The encoding is deliberately strict and unprefixed, matching the reporting seed:
        a key that differs only by ``0x`` or by case is a different string in every log,
        secret store and comparison, and normalising it here would hide that.
        """
        if not isinstance(encoded, str) or _LOWER_HEX_32.fullmatch(encoded) is None:
            raise IdentityMismatch(
                f"{PUBLISHER_KEY_ENV} must be exactly 64 lowercase hexadecimal characters"
            )
        account = Account.from_key(bytes.fromhex(encoded))
        address = Web3.to_checksum_address(account.address)
        # This one comparison carries the whole separation. A manifest cannot declare a
        # publisher that is also the deployer or the operations identity, so a key proven
        # to be the declared publisher is proven not to be either of those.
        if address != manifest.publisher_address:
            raise IdentityMismatch(
                f"publisher key derives {address}, but the manifest declares "
                f"{manifest.publisher_address}"
            )
        return cls(account=account, address=address)

    def sign_transaction(self, transaction: dict[str, object]) -> tuple[str, bytes]:
        """Sign locally and return the resulting hash and raw bytes.

        The hash is known before the transaction is broadcast, which is the whole reason
        for signing locally rather than handing an unlocked account to a remote node: the
        journal can record exactly what was sent, and a resend is always recognisable.
        """
        signed = self.account.sign_transaction(dict(transaction))
        return "0x" + signed.hash.hex().removeprefix("0x").lower(), bytes(
            signed.raw_transaction
        )


def decoded_transaction(raw: bytes) -> dict[str, object]:
    """Decode signed transaction bytes back into the fields they commit to.

    A journalled transaction is only safe to rebroadcast if what it actually says matches
    what it was recorded as meaning. Comparing the hash to the bytes proves they belong to
    each other and nothing more — edit both together and they still agree — so the fields
    are read out of the signature itself and checked against the deployment.

    The sender is recovered from the signature rather than taken on trust, because that is
    the one field no journal can assert.
    """
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("signed transaction bytes are required")
    try:
        sender = Web3.to_checksum_address(Account.recover_transaction(raw))
        # Both encodings must decode here, because the publisher signs both: a typed
        # EIP-1559 transaction where the chain quotes a base fee, and a legacy one where
        # it does not. Handling only the typed form left every legacy journal
        # unrecoverable after a restart — on exactly the chains that need recovery most.
        if raw[0] >= 0xC0:
            fields = _decoded_legacy(raw)
        else:
            if raw[0] != 0x02:
                # Only the two envelopes this project actually signs are accepted. A
                # type-4 transaction decodes perfectly well and carries an authorization
                # list that nothing here compares, so a journal could hold the expected
                # publication calldata *and* a delegation of the publisher's account —
                # and rebroadcasting it would execute both intents.
                raise ValueError(
                    f"transaction envelope type {raw[0]} is not one this publisher "
                    "signs; only EIP-155 legacy and EIP-1559 type 2 are recoverable"
                )
            fields = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
            if fields.get("accessList"):
                raise ValueError("a publication must carry no access list")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"signed transaction bytes do not decode: {error}") from error
    destination = fields.get("to")
    return {
        "chain_id": int(fields["chainId"]),
        "nonce": int(fields["nonce"]),
        "to": Web3.to_checksum_address(destination) if destination else None,
        "value": int(fields.get("value", 0)),
        "data": bytes(fields.get("data") or b""),
        "sender": sender,
    }


def _decoded_legacy(raw: bytes) -> dict[str, object]:
    """Decode an EIP-155 legacy transaction, whose chain id lives inside ``v``."""
    try:
        transaction = rlp.decode(raw, LegacyTransaction)
    except Exception as error:
        raise ValueError(f"signed transaction bytes do not decode: {error}") from error
    v = int(transaction.v)
    if v < 35:
        raise ValueError(
            "signed transaction is not replay-protected; it commits to no chain"
        )
    return {
        "chainId": (v - 35) // 2,
        "nonce": int(transaction.nonce),
        "to": bytes(transaction.to),
        "value": int(transaction.value),
        "data": bytes(transaction.data),
    }


@dataclass(frozen=True, slots=True)
class Identities:
    """The two keys a publishing run holds, and nothing else."""

    publisher: PublisherKey
    reporter: Ed25519Signer

    @property
    def reporter_kid(self) -> str:
        return self.reporter.kid


def assert_role_separation() -> None:
    """Refuse to run with one secret behind two roles.

    Distinctness is checked on the raw secret, not on the derived identifiers. Two
    different algorithms over one 32-byte secret produce two unrelated-looking public
    identities, so nothing downstream would notice that a single compromise takes both
    the ability to publish and the ability to sign what is published.

    Absence is not a violation: a run that only publishes holds no reporting seed, and a
    run that only signs holds no publisher key.
    """
    seed = os.environ.get(SIGNING_SEED_ENV)
    publisher_material = os.environ.get(PUBLISHER_KEY_ENV)
    if (
        seed is not None
        and publisher_material is not None
        and seed == publisher_material
    ):
        raise RoleConflict(
            "the reporting seed and the publisher key are the same secret; one "
            "compromise would forge both the report and its publication"
        )


def load_identities(manifest: DeploymentManifest) -> Identities:
    """Load both runtime keys from the environment and prove they are distinct."""
    seed = os.environ.get(SIGNING_SEED_ENV)
    if seed is None:
        raise MissingKeyMaterial(f"{SIGNING_SEED_ENV} is not set")
    if _LOWER_HEX_32.fullmatch(seed) is None:
        raise IdentityMismatch(
            f"{SIGNING_SEED_ENV} must be exactly 64 lowercase hexadecimal characters"
        )
    assert_role_separation()
    publisher = PublisherKey.from_env(manifest)
    reporter = Ed25519Signer.from_seed(bytes.fromhex(seed))
    if reporter.kid != manifest.active_key.kid:
        raise IdentityMismatch(
            f"{SIGNING_SEED_ENV} is {reporter.kid}, but the manifest's active reporting "
            f"key is {manifest.active_key.kid}"
        )
    return Identities(publisher=publisher, reporter=reporter)


def verification_keys(manifest: DeploymentManifest) -> dict[str, dict[str, object]]:
    """Published key records for every key whose signatures are still to be trusted.

    Superseded keys are included. Reports they signed were true when signed and their
    bundles are already published, so dropping them here would retroactively break
    verification of work that was never in question. Revoked keys are excluded, which is
    exactly what revocation means.

    A bundle carries its own key record, so an offline verifier never needs this mapping;
    it exists for the operator and for anything resolving a key id back to a state.
    """
    return {
        key.kid: {
            "algorithm": "Ed25519",
            "kid": key.kid,
            "public_key": key.public_key,
            "version": 1,
        }
        for key in manifest.reporting_keys
        if key.verifiable
    }


def rolled_over(
    manifest: DeploymentManifest, *, new_public_key: bytes, at: datetime
) -> DeploymentManifest:
    """Return the manifest that supersedes the active reporting key with a new one.

    Rollover is additive. The outgoing key stays listed as superseded with the instant it
    stopped signing, so every bundle it already signed remains verifiable and datable;
    only the selection of the key for *future* reports changes.

    The result is validated as a fresh manifest, so an invariant this function could
    break — two active keys, a kid that does not match its public key — cannot survive.
    """
    kid = kid_for_public_key(new_public_key)
    if manifest.key(kid) is not None:
        raise DeploymentError(f"reporting key {kid} is already listed")
    stamp = _stamp(at)
    value = manifest.to_mapping()
    keys = []
    for key in value["reporting_keys"]:
        if key["state"] == "active":
            key = {**key, "state": "superseded", "not_after": stamp}
        keys.append(key)
    keys.append({"kid": kid, "public_key": new_public_key.hex(), "state": "active"})
    value["reporting_keys"] = keys
    return DeploymentManifest.from_mapping(value)


def revoked(
    manifest: DeploymentManifest, *, kid: str, at: datetime
) -> DeploymentManifest:
    """Return the manifest that withdraws trust from a key that already stopped signing.

    Revoking the active key directly is refused. A deployment with no active key cannot
    sign anything, so a compromise is handled by rolling over first and revoking second —
    two steps that each leave the deployment in a state it can operate from.
    """
    key = manifest.key(kid)
    if key is None:
        raise DeploymentError(f"reporting key {kid} is not listed")
    if key.state == "active":
        raise DeploymentError(
            f"reporting key {kid} is active; roll over to a replacement first, then "
            "revoke this one"
        )
    if key.state == "revoked":
        raise DeploymentError(f"reporting key {kid} is already revoked")
    stamp = _stamp(at)
    value = manifest.to_mapping()
    value["reporting_keys"] = [
        {**entry, "state": "revoked", "not_after": stamp}
        if entry["kid"] == kid
        else entry
        for entry in value["reporting_keys"]
    ]
    return DeploymentManifest.from_mapping(value)


def _stamp(at: datetime) -> str:
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise DeploymentError("a key lifecycle instant must be timezone-aware")
    return at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
