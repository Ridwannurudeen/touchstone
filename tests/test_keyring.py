"""Role separation and reporting-key rollover.

The property that matters for rollover is not that a new key can be installed — it is that
installing one does not retroactively invalidate anything the old key signed.
"""

from datetime import datetime, timezone

from eth_account import Account
import pytest

from touchstone.deployment import DeploymentError, DeploymentManifest
from touchstone.keyring import (
    PUBLISHER_KEY_ENV,
    IdentityMismatch,
    MissingKeyMaterial,
    PublisherKey,
    RoleConflict,
    assert_role_separation,
    load_identities,
    revoked,
    rolled_over,
    verification_keys,
)
from touchstone.signing import (
    SIGNING_SEED_ENV,
    Ed25519Signer,
    kid_for_public_key,
    verify_signed_report,
)


# Letter-bearing, so a case-insensitive comparison cannot pass by accident.
PUBLISHER_SECRET = "a1" * 32
DEPLOYER_SECRET = "b2" * 32
OPERATIONS_SECRET = "c3" * 32
REPORTER_SEED = bytes(range(32))
NEXT_SEED = bytes(range(1, 33))
AT = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def address(secret: str) -> str:
    return Account.from_key(bytes.fromhex(secret)).address


def manifest(**overrides: object) -> DeploymentManifest:
    signer = Ed25519Signer.from_seed(REPORTER_SEED)
    value: dict[str, object] = {
        "manifest_version": 1,
        "network": "hardhat-local",
        "chain_id": 31337,
        "rpc_url": "http://127.0.0.1:8545",
        "registry_address": "0x" + "ab" * 20,
        "registry_runtime_bytecode_sha256": "cd" * 32,
        "publisher_address": address(PUBLISHER_SECRET),
        "publisher_identity_address": address(PUBLISHER_SECRET),
        "deployer_address": address(DEPLOYER_SECRET),
        "operations_address": address(OPERATIONS_SECRET),
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
    return DeploymentManifest.from_mapping(
        {key: item for key, item in value.items() if item is not None}
    )


def test_the_publisher_key_must_derive_the_declared_address() -> None:
    loaded = PublisherKey.from_hex(PUBLISHER_SECRET, manifest())

    assert loaded.address == address(PUBLISHER_SECRET)
    with pytest.raises(IdentityMismatch, match="derives"):
        PublisherKey.from_hex(OPERATIONS_SECRET, manifest())


def test_the_publisher_key_encoding_is_strict() -> None:
    for bad in ("0x" + PUBLISHER_SECRET, PUBLISHER_SECRET.upper(), "11" * 31, "", 11):
        with pytest.raises(IdentityMismatch, match="64 lowercase"):
            PublisherKey.from_hex(bad, manifest())


def test_the_publisher_may_not_be_the_deployer_or_operations() -> None:
    """Separation is enforced once, in the manifest, and inherited by the loaded key.

    A manifest cannot declare a publisher that is also the deployer or the operations
    identity, so a key proven to be the declared publisher cannot be either of those. The
    two facts are asserted together here because the second depends on the first: if the
    manifest ever stopped refusing, the key check alone would let it through.
    """
    for conflicting in ("deployer_address", "operations_address"):
        with pytest.raises(DeploymentError, match="must differ|must be distinct"):
            manifest(**{conflicting: address(PUBLISHER_SECRET)})

    for foreign in (DEPLOYER_SECRET, OPERATIONS_SECRET):
        with pytest.raises(IdentityMismatch, match="derives"):
            PublisherKey.from_hex(foreign, manifest())

    # And the addresses cannot simply be left out to sidestep the comparison.
    for field in ("deployer_address", "operations_address"):
        with pytest.raises(DeploymentError, match="missing fields"):
            manifest(**{field: None})


def test_one_secret_cannot_hold_both_signing_roles(monkeypatch) -> None:
    """The check is on the raw secret: the derived identities look unrelated."""
    shared = REPORTER_SEED.hex()
    monkeypatch.setenv(SIGNING_SEED_ENV, shared)
    monkeypatch.setenv(PUBLISHER_KEY_ENV, shared)

    with pytest.raises(RoleConflict, match="same secret"):
        assert_role_separation()


def test_role_separation_is_silent_when_only_one_role_is_present(monkeypatch) -> None:
    monkeypatch.delenv(SIGNING_SEED_ENV, raising=False)
    monkeypatch.setenv(PUBLISHER_KEY_ENV, PUBLISHER_SECRET)

    assert_role_separation()


def test_loading_identities_requires_both_and_matches_the_active_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv(SIGNING_SEED_ENV, REPORTER_SEED.hex())
    monkeypatch.setenv(PUBLISHER_KEY_ENV, PUBLISHER_SECRET)

    identities = load_identities(manifest())

    assert identities.publisher.address == address(PUBLISHER_SECRET)
    assert identities.reporter_kid == Ed25519Signer.from_seed(REPORTER_SEED).kid

    monkeypatch.setenv(SIGNING_SEED_ENV, NEXT_SEED.hex())
    with pytest.raises(IdentityMismatch, match="active reporting key"):
        load_identities(manifest())

    monkeypatch.delenv(SIGNING_SEED_ENV)
    with pytest.raises(MissingKeyMaterial, match=SIGNING_SEED_ENV):
        load_identities(manifest())


def test_a_locally_signed_transaction_carries_its_hash_before_broadcast() -> None:
    """The hash is derived from the signed bytes, so a send is recognisable if repeated."""
    key = PublisherKey.from_hex(PUBLISHER_SECRET, manifest())

    transaction_hash, raw = key.sign_transaction(
        {
            "to": Account.from_key(bytes.fromhex(DEPLOYER_SECRET)).address,
            "value": 0,
            "gas": 21_000,
            "maxFeePerGas": 10**9,
            "maxPriorityFeePerGas": 10**8,
            "nonce": 0,
            "chainId": 31337,
        }
    )

    assert transaction_hash.startswith("0x") and len(transaction_hash) == 66
    assert transaction_hash == transaction_hash.lower()
    assert raw and isinstance(raw, bytes)
    assert key.sign_transaction(
        {
            "to": Account.from_key(bytes.fromhex(DEPLOYER_SECRET)).address,
            "value": 0,
            "gas": 21_000,
            "maxFeePerGas": 10**9,
            "maxPriorityFeePerGas": 10**8,
            "nonce": 0,
            "chainId": 31337,
        }
    ) == (transaction_hash, raw), "signing is deterministic for identical inputs"


def test_rollover_selects_the_new_key_and_keeps_the_old_one_verifiable() -> None:
    original = manifest()
    old_signer = Ed25519Signer.from_seed(REPORTER_SEED)
    new_signer = Ed25519Signer.from_seed(NEXT_SEED)
    # A report signed before the rollover. Nothing about it changes afterwards.
    signed_before = old_signer.sign_report({"asset": "ustb", "sequence": 1})

    rotated = rolled_over(
        original,
        new_public_key=bytes.fromhex(new_signer.public_key_record()["public_key"]),
        at=AT,
    )

    assert rotated.active_key.kid == new_signer.kid
    retired = rotated.key(old_signer.kid)
    assert retired.state == "superseded"
    assert retired.not_after == "2026-08-15T12:00:00Z"

    trusted = verification_keys(rotated)
    assert set(trusted) == {old_signer.kid, new_signer.kid}
    assert verify_signed_report(signed_before, trusted)["sequence"] == 1, (
        "a superseded key still verifies what it signed"
    )


def test_rollover_refuses_to_install_a_key_that_is_already_listed() -> None:
    with pytest.raises(DeploymentError, match="already listed"):
        rolled_over(
            manifest(),
            new_public_key=Ed25519Signer.from_seed(REPORTER_SEED)
            ._private_key.public_key()
            .public_bytes_raw(),
            at=AT,
        )


def test_revocation_withdraws_trust_and_is_not_a_way_to_disarm_a_deployment() -> None:
    original = manifest()
    old_kid = original.active_key.kid
    new_signer = Ed25519Signer.from_seed(NEXT_SEED)
    rotated = rolled_over(
        original,
        new_public_key=bytes.fromhex(new_signer.public_key_record()["public_key"]),
        at=AT,
    )

    # The active key cannot be revoked directly: that would leave nothing able to sign.
    with pytest.raises(DeploymentError, match="roll over to a replacement first"):
        revoked(rotated, kid=new_signer.kid, at=AT)

    withdrawn = revoked(rotated, kid=old_kid, at=AT)

    assert withdrawn.key(old_kid).state == "revoked"
    assert set(verification_keys(withdrawn)) == {new_signer.kid}
    with pytest.raises(DeploymentError, match="already revoked"):
        revoked(withdrawn, kid=old_kid, at=AT)
    with pytest.raises(DeploymentError, match="not listed"):
        revoked(withdrawn, kid="ed25519:" + "00" * 32, at=AT)


def test_a_lifecycle_instant_must_be_timezone_aware() -> None:
    with pytest.raises(DeploymentError, match="timezone-aware"):
        rolled_over(
            manifest(),
            new_public_key=bytes.fromhex(
                Ed25519Signer.from_seed(NEXT_SEED).public_key_record()["public_key"]
            ),
            at=datetime(2026, 8, 15, 12, 0, 0),
        )


def test_a_published_key_record_matches_what_the_signer_publishes() -> None:
    """The manifest and the bundle must describe a key identically or one of them lies."""
    signer = Ed25519Signer.from_seed(REPORTER_SEED)

    record = verification_keys(manifest())[signer.kid]

    assert record == signer.public_key_record()
    assert record["kid"] == kid_for_public_key(bytes.fromhex(record["public_key"]))
