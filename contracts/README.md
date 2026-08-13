# Touchstone contracts

This isolated Hardhat project contains the append-only `TouchstoneRegistry` and the
reference `AssetGate` consumer. The contracts hold no assets and expose no payable,
transfer, custody, or token functions.

## Asset keys

An asset key is `keccak256` of its canonical asset identifier string. Contract-backed
assets use `eip155:<decimal-chain-id>:<lowercase-0x-address>` so the offchain identity
is always the pair `(chain, address)`. For example:

```js
const assetKey = ethers.keccak256(
  ethers.toUtf8Bytes("eip155:1:0x0000000000000000000000000000000000000000"),
);
```

The registry treats the resulting `bytes32` as opaque. Canonicalization and validation
of the identifier string happen before publication, never inside the contract.

## Registry model

Every publication appends the next per-asset sequence to immutable history and updates
the latest-record pointer. A correction is another full report with a new sequence and
an explicit reference to a prior sequence; it never changes or deletes the referenced
report. Publisher rotation revokes the old key and authorizes the new key, while every
historical report retains its posting address. Successor keys inherit a stable publisher
identity, so a gate pinned to any key in that lineage continues to work after rotation.
That first identity assignment is permanent: reauthorizing a suspended key preserves
its lineage, and a key previously assigned to another lineage cannot be reused.

The registry uses the minimum chain-separation option from the roadmap: it stores an
immutable `expectedChainId` and compares it with `block.chainid` on every publish and
correction. This project does not add an EIP-712 signature layer because reports are
submitted directly by authorized publisher accounts.

## Gate freshness

`AssetGate` accepts a report only when both freshness limits are inclusive:

- `block.timestamp <= validUntil`
- `block.timestamp - observedAt <= maxObservationAge`

Future observation timestamps are rejected by the registry. A gate with
`requiredPublisher == address(0)` accepts any publisher that is currently authorized;
a fixed required publisher accepts a currently authorized posting key from the same
owner-managed rotation lineage. A zero required control-set root disables the root
check.

## Development

```text
npm ci
npx hardhat test
```

The reserved network name is `xLayerTestnet`. It is deliberately not configured in
`hardhat.config.js`; an RPC URL and deployment workflow are added only after endpoint
audit and explicit owner approval. No live-network deployment script is included.
