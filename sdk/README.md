# Touchstone SDK

The SDK is the small external-consumer surface for Touchstone policy gates and Registry v2
events. The checked-in deployment table carries the live addresses for both chains — the
legacy registries and, since 2026-08-19, the Registry v2 deployments the owner approved —
so a consumer reads real contracts out of the box.

## Five-minute integration

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
import { indexPublished } from "@touchstone/sdk";

const events = await indexPublished(
  new JsonRpcProvider(process.env.RPC_URL!, 1952),
  process.env.REGISTRY_V2_ADDRESS!,
  0,
  "latest"
);
```

The result includes both `Published` and `Corrected` events in canonical log order.
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

After the owner registers a Builder Code, pass it to `GuardedActionClient.execute([code])`.
The SDK appends the canonical ERC-8021 schema-0 suffix to the action calldata. The package
does not ship an unregistered code or claim that registration has happened.
