# Touchstone SDK

The SDK is the small external-consumer surface for Touchstone policy gates and Registry v2
events. The checked-in deployment table carries the live addresses for both chains — the
legacy registries and, since 2026-08-19, the Registry v2 deployments the owner approved —
so a consumer reads real contracts out of the box.

## Five-minute integration

```sh
npm install @touchstone/sdk ethers
```

Or from a checkout of this repository:

```sh
cd sdk
npm ci
npm run build
npm test
```

Read a policy gate and execute only when it permits:

```ts
import { JsonRpcProvider, Wallet } from "ethers";
import {
  AssetGateClient,
  DEPLOYMENTS,
  GuardedActionClient,
  POLICIES,
} from "@touchstone/sdk";

const provider = new JsonRpcProvider(process.env.RPC_URL, 1952);
const wallet = new Wallet(process.env.PRIVATE_KEY!, provider);
const gate = new AssetGateClient(
  process.env.GATE_ADDRESS!,
  POLICIES.disclosureFreshness.registryKey,
  wallet
);
const action = new GuardedActionClient(
  process.env.GUARDED_ACTION_ADDRESS!,
  wallet
);

const decision = await gate.check();
if (decision.allowed) await action.execute();
```

The example at `examples/check-and-act.ts` uses the same path with explicit environment
variables. `AssetGateClient.demand()` separately exercises the gate's state-changing demand
method and rejects a known refusal before submitting. Only `GuardedActionClient.execute()`
executes the guarded action.

## Registry event indexing

```ts
import { JsonRpcProvider } from "ethers";
import { DEPLOYMENTS, indexPublished } from "@touchstone/sdk";

const deployment = DEPLOYMENTS.xlayerMainnet;
const events = await indexPublished(
  new JsonRpcProvider("https://rpc.xlayer.tech", deployment.chainId),
  deployment.v2RegistryAddress!,
  deployment.v2RegistryDeploymentBlock!,
  "latest"
);
```

The indexer reads logs in windows of 100 blocks because the public X Layer RPC rejects
wider `eth_getLogs` ranges; pass `{ blockRange }` as a fifth argument for a provider that
allows more. Start from `v2RegistryDeploymentBlock` (or a checkpoint you persist) rather
than block 0. The result includes both `Published` and `Corrected` events in canonical log
order.
Corrections carry `kind: "corrected"` and their non-null `correctedSequence`; consumers
must process both kinds so a correction cannot leave cached permissive state behind.

Use `policyRegistryKey(assetKey, policyId, version)` rather than hand-hashing policy keys.
The included policy ids are `disclosure-freshness:1` and `nav-settlement:1`; they are the
policy keys produced by this repository's committed manifests.

The SDK does not embed private keys, submit owner actions, or treat legacy v1 reports as v2
attestations. The Solidity interface in `solidity/ITouchstoneGuard.sol` is the minimum
dependency for a consumer contract that wants to read a gate directly.

## Registry v2 policy mapping

Use `registryAssetKey(fullReportAssetKey)` for the onchain asset key, including the complete
`#policy:<id>:<version>` suffix. Use `policyIdDigest(id, version)` for the onchain policy id
and `policyDigestRoot(report.policy.policy_digest)` for the policy root. The shared vector in
`fixtures/registry-v2-policy-vector.json` locks these derivations to the Python publisher.

## ERC-8021 attribution

Pass a registered Builder Code to `GuardedActionClient.execute([code])`. The SDK appends the
canonical ERC-8021 schema-0 suffix to the action calldata. Touchstone's registered code is
`f0axgs7smtk2nfa7`; its first attributed
[mainnet admission execution](https://web3.okx.com/explorer/xlayer/tx/0xb48cf6182b7bf87df78817401c7fefc2e8a319b341b96e572552775361fa9a1e)
is public. External applications must register and pass their own code rather than reusing
Touchstone's attribution.
