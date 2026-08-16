# G1 — Replacement testnet registry deployment

> # DRAFT — NOT EXECUTION AUTHORIZATION
>
> This document exists so that an owner can decide. It authorizes nothing by existing, and
> nothing in it may be executed until the owner has read it and said so explicitly, naming
> this document. The design of the replacement registry is approved **in principle**;
> execution authorization **has not been granted**.

## 1. Why there is a second deployment at all

A registry was deployed to X Layer testnet on 2026-08-15 at
`0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, block 38369203. It never published anything.

It is unusable because of a defect found afterwards and reproduced: a daemon restarted on a
day it had already served derives the same epoch, reads the correct next sequence, and the
registry accepts a **second signed report about the same day**. Two valid reports, and a
consumer reading the latest one sees whichever landed last.

Both cheaper remedies were rejected on review. Durable local state is a projection rather
than chain truth and is lost with the workspace. Inferring the epoch from the latest report's
URI fails because a correction can become the latest report and hide the earlier duplicate.
So the registry itself enforces it — `bytes32 epochKey` on `Report`,
`epochSequence[assetKey][epochKey]`, and `EpochAlreadyPublished` on a second publish. That is
an ABI change, and the deployed contract cannot be upgraded to it.

The old deployment is marked `deployment_state: superseded` in its manifest and is refused by
`SignedRegistryBackend` before any key is read. **It is not being replaced because it failed;
it is being replaced because it cannot enforce something it was never built to.**

## 2. Exactly what would be deployed

| | |
|---|---|
| Release commit | `e22cc7fdb6003f1afccee7957f20c86089938c3c` |
| Contract | `contracts/contracts/TouchstoneRegistry.sol` |
| Solidity | `0.8.24`, optimizer **enabled**, `runs: 200`, evm target `paris` |
| Creation bytecode | 6,303 bytes, sha256 `e1702c40bafef7a36ede227a32b19cf1904a78cd6cd70d00068a3643c4fa6926` |
| Runtime bytecode | 6,147 bytes, sha256 `9b7019b0b2e3ad4242ac99adc2c0542513425c13336341505aca9674ba23bca7` |
| Constructor argument | `expectedChainId = 1952` |
| npm lockfile | `contracts/package-lock.json`, sha256 begins `3c506269d6e7a735…` |
| Python project | `pyproject.toml`, sha256 begins `823c6a5e9d332600…` |

The runtime digest above is what the new manifest must record. It differs from the superseded
manifest's `7b0b36531a3d9234fb7d72a231b5582a8516f4d99c757fe4298be57d57dd6e2a`, which is the
old contract — and the difference is the whole point.

**No AssetGate is deployed by this gate.** The consumer contract must be pinned to a control
root, and the control set is expected to change when the NAV controls are recompiled to carry
`minimum_row_age_business_days`. Deploying a gate now would pin a root we intend to retire.

## 3. Network

| | |
|---|---|
| Network name | `xlayer-testnet` |
| Expected chain id | **1952** |
| Endpoint | `https://testrpc.xlayer.tech/terigon` |
| Deprecated chain | **195 must never be used** |

The chain id is bound to the network name in both `touchstone/deployment.py` and
`contracts/scripts/deploy.js`, so a manifest naming `xlayer-testnet` on any other chain is
refused. The deploy script additionally requires `TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=1952` —
a positive confirmation naming the chain, because a boolean flag can be satisfied by a stale
shell export.

**Not verified in preparing this document:** the live `eth_chainId` response. The endpoint is
unreachable from the environment this was written in. **The operator must confirm it returns
`0x7a0` (1952) immediately before deploying**, and abort if it does not.

## 4. Identities

All four are distinct, and the three EVM roles are enforced as distinct by the deploy script
before it sends anything.

| Role | Address | Held where |
|---|---|---|
| Deployer / registry owner | `0xAa1C01C6FcDcc268DbF93C861D26C44a27C35436` | Owner only. **Never on the publishing host** |
| Publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` | Publishing host |
| Operations | `0xF901f538d17ED060018F043bCAA1d7f0977Fa65D` | Custody only; sends nothing here |
| Reporting key (Ed25519) | `ed25519:394ee022b83de4783cd49f60cc4842b48d108f6a339b73272739490cb9a581fd` | Reporting host |

Public key material only. **No private key or seed appears in this document, and none may be
added to it.** See `docs/KEY-MANAGEMENT.md` for where each is held and why the deployer must
not reach the publishing host.

## 5. Spending

Two separate ceilings, because they bound different things and conflating them is how an
approval to deploy became an approval to spend an unstated amount.

| Ceiling | Variable | Bounds |
|---|---|---|
| Deployment total | `TOUCHSTONE_DEPLOY_MAX_SPEND_WEI` | The deployment **and** authorization transactions of this one command. Required off the local chain |
| Per publication | `TOUCHSTONE_MAX_FEE_WEI` | One publication's worth-case fee, recorded in the manifest as `max_fee_wei`. Currently `2000000000000000` |

The deployer's balance is checked against the deployment ceiling **before** the first send, so
a run that could not cover its own ceiling never starts and cannot strand a half-finished
deployment. The receipts are checked against it **after**, because a receipt is the only
honest measure of what was actually spent — that check cannot un-send anything, it makes an
overrun loud instead of silent, and the abort criteria in §9 take over.

**The owner sets the deployment ceiling.** This document does not propose a number; proposing
one would be the approval quietly making itself.

## 6. The command

Run from the repository root, on the **owner's** machine, with the deployer key present and
the publisher key absent.

```
TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=1952 \
TOUCHSTONE_DEPLOY_MAX_SPEND_WEI=<owner-approved ceiling in wei> \
TOUCHSTONE_DEPLOYER_PRIVATE_KEY=<from .env.deployer> \
TOUCHSTONE_NETWORK=xlayer-testnet \
TOUCHSTONE_RPC_URL=https://testrpc.xlayer.tech/terigon \
TOUCHSTONE_CONFIRMATIONS=3 \
TOUCHSTONE_MAX_FEE_WEI=2000000000000000 \
TOUCHSTONE_PUBLISHER_ADDRESS=0x86A100BDdF8754c95fec97BeC96dBFd64Be44710 \
TOUCHSTONE_OPERATIONS_ADDRESS=0xF901f538d17ED060018F043bCAA1d7f0977Fa65D \
TOUCHSTONE_REPORTER_PUBLIC_KEY=0959d043538a483024722ced848e52ae5dcaf56661e598dc850a9541028b9dba \
TOUCHSTONE_MANIFEST_OUT=deployments/xlayer-testnet-2.json \
npx hardhat run scripts/deploy.js --network xLayerTestnet
```

`npx hardhat` executes from `contracts/`, which is why `TOUCHSTONE_MANIFEST_OUT` is resolved
against the repository root rather than the working directory — a relative path resolved
against `contracts/` is what lost the first deployment's manifest.

### The destination preserves the superseded manifest

`deployments/xlayer-testnet.json` is **not** the destination and must not be. It records the
superseded deployment, and that record is the only evidence of a registry that exists on
chain. The script refuses a destination that already exists, so naming it would abort rather
than overwrite — but the correct destination is a new file, and the superseded manifest stays
exactly where it is.

## 7. Before the first transaction

Every one of these must hold. Any failure is an abort, not a retry.

- [ ] Working tree clean at `e22cc7fdb6003f1afccee7957f20c86089938c3c`, verified with `git status --porcelain` returning nothing.
- [ ] `python -m pytest -q` — full suite green.
- [ ] `python scripts/mutation_check.py` — every mutant killed.
- [ ] `npx hardhat test` — full contract suite green.
- [ ] `python -m pytest tests/test_e2e_local.py -q` — the managed local-chain loop green.
- [ ] `python -m ruff check .` — clean.
- [ ] `npx hardhat compile` from a clean `artifacts/`, and the creation and runtime digests match §2 **exactly**.
- [ ] `eth_chainId` at the endpoint returns `0x7a0`.
- [ ] Deployer balance ≥ the approved ceiling.
- [ ] Deployer nonce is what the operator expects; an unexpected nonce means something else has used this key.
- [ ] The three EVM addresses in §4 are distinct and are the ones the owner intends.
- [ ] `deployments/xlayer-testnet-2.json` does not exist.
- [ ] The owner has approved a specific maximum total spend, in writing, naming this document.

## 8. After deployment

- [ ] Both transactions have receipts with `status = 1` and at least 3 confirmations.
- [ ] Record the address, both transaction hashes, and the deployment block.
- [ ] `eth_getCode` at the address hashes to the §2 runtime digest.
- [ ] `expectedChainId()` returns 1952.
- [ ] `owner()` is the deployer in §4.
- [ ] `isPublisherAuthorized(publisher)` is true.
- [ ] `publisherIdentity(publisher)` is non-zero and equals the publisher — the lineage a rotation would carry.
- [ ] The emitted manifest validates against `deployments/manifest.schema.json`.
- [ ] The manifest records `deployment_state: "active"`.
- [ ] `python -m pytest tests/test_deployment_manifests.py -q` passes with the new manifest present.
- [ ] `python scripts/publish_epoch.py --manifest deployments/xlayer-testnet-2.json --preflight` succeeds. **This sends nothing.**
- [ ] The `.attempt.json` breadcrumb beside the manifest agrees with the final manifest; delete it only once they do.

## 9. Abort criteria

Stop at the first of these. **Never retry automatically** — a retry after a partial deployment
is how a second unrecorded registry gets created.

- Any digest, chain id, owner, authorization or lineage mismatch in §7 or §8.
- Either transaction reverted, or still pending beyond the operator's patience.
- An unexpected deployer nonce.
- Spend above the approved ceiling — the script raises after the fact; treat it as an abort.
- The manifest write failed, or the destination existed.
- Authorization failed after deployment succeeded.

## 10. If it goes wrong

A failed attempt is not erased. It is recorded, because a registry that exists on chain
exists whether or not anyone wrote it down — that is the lesson of 2026-08-15.

1. **Stop.** Do not re-run the command.
2. Keep the `.attempt.json` breadcrumb. It carries the address, transaction and block, and it
   is written the instant the registry exists — before authorization can fail.
3. Write a manifest for the failed attempt at its own destination with
   `deployment_state: "superseded"`, recording the reason, the transactions and the block.
4. If the registry deployed but authorization failed, the contract is live and owned. Prepare
   a **separately approved** publisher revocation if anything was authorized; do not leave an
   authorized publisher on a registry nobody intends to use.
5. Only then consider a fresh attempt, with a new destination and fresh owner approval.

## 11. What this gate does not do

- It does not publish anything. The first live epoch is a **separate** owner gate.
- It does not deploy `AssetGate`.
- It does not touch mainnet. Mainnet is unscheduled and conditional on a proven testnet loop.
- It does not authorize a paid model call, a public post, a submission, or a domain.
