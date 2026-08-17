"""Publisher rotation against a real chain, not a fake.

The rotation rules were proved twice already and neither proof reached the chain. The
contract suite proves `rotatePublisher` carries lineage, and `tests/test_publish.py` proves a
rotated-out signer cannot send and that a rotated publisher still reconciles what its
predecessor published — but that one drives a `FakeBackend`, so what it establishes is that
the client behaves correctly given a registry that answers the way the tests say it does.

The gap is the join between them: whether `preflight()`, reading a real deployed registry over
JSON-RPC, reaches the same conclusions. That is where the identity actually lives, and a fake
that agreed with the client would hide a disagreement between the client and the contract.

So this deploys the registry on a managed Hardhat node, rotates the publisher on it, and asks
the production `SignedRegistryBackend` what it sees — before the rotation and after.

The contracts are compiled here rather than assumed: `contracts/artifacts/` is gitignored, so
on a clean checkout the build output does not exist and a test that read it would fail for a
reason that has nothing to do with rotation.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib

from eth_account import Account
import pytest
from web3 import Web3

from touchstone.deployment import DeploymentManifest, runtime_bytecode_sha256
from touchstone.keyring import PublisherKey
from touchstone.publish import PreflightFailed, SignedRegistryBackend

from scripts.e2e_local import (
    DEV_MNEMONIC,
    _artifact,
    _compile_contracts,
    _discard_log,
    _start_node_on_free_port,
    _stop_node,
)

# Hardhat's deterministic accounts. 1 is the publisher the rest of the harness uses; 3 is
# unused elsewhere, so rotating onto it cannot collide with another test's expectations.
OUTGOING_INDEX = 1
INCOMING_INDEX = 3
REPORTER_PUBLIC_KEY = "ab" * 32


@pytest.fixture(scope="module")
def compiled() -> None:
    """Build the contracts once for this module."""
    _compile_contracts()


@pytest.fixture
def chain(compiled: None) -> Iterator[str]:
    """A private node on an ephemeral port, started and stopped by this test.

    Its own node, never a shared one: a rotation permanently changes registry state, so a
    test that reused someone else's chain would leave the publisher rotated for whatever ran
    next.
    """
    process, log, port = _start_node_on_free_port()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        _stop_node(process)
        _discard_log(log)


def _account(index: int):
    Account.enable_unaudited_hdwallet_features()
    return Account.from_mnemonic(DEV_MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}")


def _manifest(
    web3: Web3,
    *,
    registry,
    rpc_url: str,
    publisher: str,
    deployer: str,
    operations: str,
) -> DeploymentManifest:
    """The operator's file for whichever publisher is current.

    `publisher_identity_address` is read from the registry rather than assumed, because that
    is the field rotation changes the meaning of: after a rotation the incoming address is
    authorized under the *outgoing* identity, and a manifest that assumed identity equals
    address would describe a deployment that does not exist.
    """
    return DeploymentManifest.from_mapping(
        {
            "manifest_version": 1,
            "network": "hardhat-local",
            "chain_id": web3.eth.chain_id,
            "rpc_url": rpc_url,
            "registry_address": registry.address,
            "registry_runtime_bytecode_sha256": runtime_bytecode_sha256(
                bytes(web3.eth.get_code(registry.address))
            ),
            "publisher_address": publisher,
            "publisher_identity_address": registry.functions.publisherIdentity(
                publisher
            ).call(),
            "deployer_address": deployer,
            "operations_address": operations,
            "confirmations": 1,
            "deployment_state": "active",
            "deployment_block": web3.eth.block_number,
            "reporting_keys": [
                {
                    "kid": "ed25519:"
                    + hashlib.sha256(bytes.fromhex(REPORTER_PUBLIC_KEY)).hexdigest(),
                    "public_key": REPORTER_PUBLIC_KEY,
                    "state": "active",
                }
            ],
        }
    )


def _backend(manifest: DeploymentManifest, key_hex: str) -> SignedRegistryBackend:
    return SignedRegistryBackend(manifest, PublisherKey.from_hex(key_hex, manifest))


def _deployed_registry(web3: Web3, owner: str):
    artifact = _artifact("TouchstoneRegistry")
    factory = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = web3.eth.wait_for_transaction_receipt(
        factory.constructor(web3.eth.chain_id).transact({"from": owner})
    )
    return web3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])


def _confirm(web3: Web3, transaction_hash) -> None:
    receipt = web3.eth.wait_for_transaction_receipt(transaction_hash)
    assert receipt.status == 1, "a setup transaction reverted"


@pytest.mark.local_e2e
def test_a_rotation_on_a_real_registry_moves_authority_and_keeps_lineage(
    chain: str,
) -> None:
    """One rotation, and every party's view of it read from the chain.

    Four things have to hold together, and only the last two need a live registry:

    * the outgoing publisher stops being authorized;
    * the incoming one starts;
    * the incoming one inherits the outgoing *identity* rather than becoming its own, which
      is what a consumer gated on `isPublisherFor` follows;
    * and `preflight()` agrees with all of it — refusing the retired publisher's manifest and
      accepting the successor's.

    The third is the one worth the node. `authorizePublisher(B)` and `rotatePublisher(A, B)`
    both leave B able to publish, and they differ only in the lineage recorded, so a test that
    checked authorization alone would pass against the wrong call.
    """
    web3 = Web3(Web3.HTTPProvider(chain))
    assert web3.is_connected(), "the managed node did not answer"
    accounts = web3.eth.accounts
    owner, operations = accounts[0], accounts[2]
    outgoing = _account(OUTGOING_INDEX)
    incoming = _account(INCOMING_INDEX)
    assert Web3.to_checksum_address(outgoing.address) == accounts[OUTGOING_INDEX]
    assert Web3.to_checksum_address(incoming.address) == accounts[INCOMING_INDEX]

    registry = _deployed_registry(web3, owner)
    _confirm(
        web3,
        registry.functions.authorizePublisher(outgoing.address).transact(
            {"from": owner}
        ),
    )

    before = _manifest(
        web3,
        registry=registry,
        rpc_url=chain,
        publisher=outgoing.address,
        deployer=owner,
        operations=operations,
    )
    # Passes today, and the same object is used again after the rotation to show that what
    # changed is the chain's answer rather than the file.
    #
    # This assertion does not prove the lineage check by itself, and it would be easy to
    # read as though it did: `_manifest` reads `publisher_identity_address` back from the
    # registry, so a manifest built this way can never disagree with the registry it was
    # built from. What it establishes is that every other preflight input is good. The
    # lineage check is proved by the second test, which changes that one field by hand.
    assert _backend(before, outgoing.key.hex()).preflight().publisher_authorized

    _confirm(
        web3,
        registry.functions.rotatePublisher(outgoing.address, incoming.address).transact(
            {"from": owner}
        ),
    )

    assert not registry.functions.isPublisherAuthorized(outgoing.address).call()
    assert registry.functions.isPublisherAuthorized(incoming.address).call()
    assert registry.functions.publisherIdentity(incoming.address).call() == (
        Web3.to_checksum_address(outgoing.address)
    ), "rotation did not carry the outgoing identity onto the incoming publisher"

    # The retired publisher's own manifest, unchanged, now fails against the same chain.
    with pytest.raises(PreflightFailed, match="not an authorized publisher"):
        _backend(before, outgoing.key.hex()).preflight()

    after = _manifest(
        web3,
        registry=registry,
        rpc_url=chain,
        publisher=incoming.address,
        deployer=owner,
        operations=operations,
    )
    assert _backend(after, incoming.key.hex()).preflight().publisher_authorized


@pytest.mark.local_e2e
def test_a_successor_manifest_claiming_its_own_identity_is_refused(chain: str) -> None:
    """Lineage is checked, not merely recorded.

    After a rotation the incoming publisher is authorized under the outgoing identity. A
    manifest naming the incoming address as its own identity therefore describes a publisher
    the registry does not have — the shape an owner produces by calling
    `authorizePublisher(B)` instead of `rotatePublisher(A, B)`, which reads as authorized and
    which no consumer gated on `isPublisherFor` would accept. Preflight has to refuse it,
    against a real registry rather than a fake that was told what to say.
    """
    web3 = Web3(Web3.HTTPProvider(chain))
    accounts = web3.eth.accounts
    owner, operations = accounts[0], accounts[2]
    outgoing = _account(OUTGOING_INDEX)
    incoming = _account(INCOMING_INDEX)

    registry = _deployed_registry(web3, owner)
    _confirm(
        web3,
        registry.functions.authorizePublisher(outgoing.address).transact(
            {"from": owner}
        ),
    )
    _confirm(
        web3,
        registry.functions.rotatePublisher(outgoing.address, incoming.address).transact(
            {"from": owner}
        ),
    )

    honest = _manifest(
        web3,
        registry=registry,
        rpc_url=chain,
        publisher=incoming.address,
        deployer=owner,
        operations=operations,
    )
    # The positive control, and it is the whole reason this test means anything. Without it a
    # bare `raises(PreflightFailed)` passes when preflight refuses for a reason that has
    # nothing to do with lineage — a bytecode digest that does not match, an RPC that answers
    # for another chain — so the test would report lineage coverage while proving only that
    # something, somewhere, was wrong. Proving the honest manifest passes first pins every
    # other input as good, which leaves the identity field as the only thing that changed.
    assert _backend(honest, incoming.key.hex()).preflight().publisher_authorized

    claimed_own_identity = DeploymentManifest.from_mapping(
        {
            **honest.to_mapping(),
            "publisher_identity_address": Web3.to_checksum_address(incoming.address),
        }
    )

    # Matched, not merely raised: the refusal has to be the lineage one.
    with pytest.raises(PreflightFailed, match="lineage"):
        _backend(claimed_own_identity, incoming.key.hex()).preflight()
