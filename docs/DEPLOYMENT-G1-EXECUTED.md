# G1 — executed 2026-08-17

The replacement testnet registry is live. This is the record of what was approved, what was
sent, and what was verified afterwards. It also records one deviation from the packet, because
a record that only lists successes is not a record.

> Transaction hashes below are written **without the `0x` prefix**. A repository secret-scanning
> hook matches `0x` followed by 64 hex characters, which is the shape of a private key and also
> the shape of a transaction hash. Prefix them with `0x` to use them.

## What the owner approved

The packet required an approval naming three values. All three were displayed to the owner and
approved together on 2026-08-17:

| | |
|---|---|
| Packet commit | `16ca3bdb4287cf974fdd65dea62df67ce713f9a9` |
| Packet blob sha256 | `046ab1706bd0e23a6edbe8240ad7e9c9d026f58d14989a5c56a0c8a7b29070c1` |
| Spend ceiling | `1000000000000000` wei (0.001 OKB) |

The approval was scoped to this deployment. It did not cover the USTB canary epoch, the
`AssetGate`, or anything on mainnet.

An earlier revision of the packet recorded this rule as *waived* on the strength of a general
"approval for the everything downstream work". That was withdrawn before execution: the rule
exists to stop a packet drifting after approval while the executing agent holds the pen, so the
agent is the one party who cannot waive it. The approval above was obtained specifically.

## What was deployed

| | |
|---|---|
| Registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` |
| Chain | 1952 (X Layer testnet) |
| Deployment block | 38489602 |
| Deployment tx | `c5fbaa03ff0fab74f5ec15c6718ffaf8ea37359d8f55b09ab84f112ec79950d6` |
| Authorization tx | `8a14bd93873742f5819dbf664fef7afc5622b8182b7079aa399448bff22e9cef` |
| Release commit | `bcbd8b40828935888191b16f09f3c5d383e83108` |
| Manifest | `deployments/xlayer-testnet-2.json` |

The registry landed at the address preflight predicted from the deployer and nonce 3, and its
on-chain runtime hashes to `cecada9e4caefaa153ea321d5831b053ad8750ffe58a4ac0ee61b81ba4dbc561` —
the **as-deployed** digest §2 of the packet computed in advance by splicing the owner and chain
id into the template at `immutableReferences` ids 467 and 469. That was the most fragile claim
in the packet, and it held exactly.

## Spend

| | |
|---|---|
| Approved ceiling | `1000000000000000` wei |
| Worst case the script proved before sending | `999999999007486` wei at 1,821,613 gas × 548,964,022 wei |
| **Actually spent** | **`28981261449063` wei** ≈ 0.0000290 OKB |

2.9% of the approved ceiling. The ceiling was set 27.67× above the expected spend deliberately,
so fee movement between approval and execution could not cause a spurious abort; it cost
nothing to leave that headroom unused.

## Verified after deployment

Both receipts `status = 1`, well past three confirmations, both sent by the deployer. On chain:
`owner` is the deployer, `expectedChainId` is 1952, the publisher is authorized, its identity
maps to itself, and the deployer is **not** authorized as a publisher. The manifest validates
against `deployments/manifest.schema.json`, loads through `DeploymentManifest`, and declares
`deployment_state: active`. The journal recorded all seven stages in order —
`prepared → broadcast → deploying → deployed → broadcast → authorizing → authorized`.

`publish_epoch.py --preflight` returns `published: false` against the new registry with the
publisher authorized and the runtime digest matching. Nothing had been published to it at
the time of that deployment. The first publication came later, on 2026-08-17 at 16:49 UTC
under its own separate owner authorisation: USTB sequence 1, state `UNVERIFIABLE`, block
38526525. This document records the deployment, not that run; see `docs/CANARY-G1B.md` §9.

The superseded registry `0xc9d58e…D30d` was not touched and its manifest still reads
`deployment_state: superseded`. A full scan of every 100-block range from its deployment block
to head found exactly one log — the `PublisherAuthorized` from its own setup — confirming there
was never any history to migrate.

### The defect this release fixed, fired as predicted

`tests/test_deployment_manifests.py` globbed `deployments/*.json` and excluded only
`manifest.schema.json`, so it collected `xlayer-testnet-2.json.attempt.json` — the JSONL
journal — as a manifest. It was fixed in the release commit specifically because it would fire
here, minutes after a live registry existed, looking like a malformed manifest rather than a
test collecting a file that was never one. With the real manifest and journal both present the
module now returns 18 passed.

## Deviation: the key separation was not real

**§4 of the packet says the deployer key must never sit on the publishing host. On this
execution it did.** `.env.deployer` and `.env.testnet` are both on the same laptop, so
`TOUCHSTONE_DEPLOYER_PRIVATE_KEY` and `TOUCHSTONE_PUBLISHER_PRIVATE_KEY` were on one machine at
the same time. The deployment shell loaded only the deployer key and the preflight shell
unset it before loading the publisher key, but that is process hygiene, not host separation.

This is disclosed rather than presented as satisfied. On a testnet with 0.0000290 OKB at stake
the exposure is negligible, but the packet asserts a control that does not currently exist, and
**a mainnet deployment must not proceed until it does** — two hosts, not two shells.

## Not done, and still gated

- The USTB canary epoch — approved in principle, not yet run.
- `AssetGate` — must be pinned to a control root, and the NAV controls are due for
  recompilation first.
- Mainnet — nothing. Chain 196 requires the digest-bound approval with no waiver, and the key
  separation above made real.
