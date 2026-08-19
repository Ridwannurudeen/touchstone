# Response to the external audit — build plan and traceability

The owner commissioned an external judge-perspective audit. This document exists so that a
**re-audit can check the auditor's own findings off one by one**, rather than reading a new
roadmap and trying to map it back.

Every finding the audit raised appears below with the same name the auditor gave it. Nothing is
quietly dropped: items we decline to build are listed as declined, with a reason.

**Scope note.** This was drafted under a three-day deadline and re-scoped on 2026-08-19 when
the owner directed that the deadline not constrain the work. Items previously cut for time are
sequenced rather than dropped.

**Counted:** 9 numbered blockers, 7 Priority-0 items, 5 Priority-1 items, 2 Priority-2 items,
and 8 operational sub-items under Blocker 8 — 31 distinct findings, plus three incidental
defects the audit noted in passing.

---

## Status update — 2026-08-19 evening

The second external re-audit examined `fb7806e` and scored 8.0/10; five commits landed after
its snapshot. Deltas against its verdicts, each verifiable from chain or repo:

| Its verdict | Now |
|---|---|
| "No truthful live accepted policy; both panels UNVERIFIABLE" | **CLOSED.** First CONFIRMED states published 2026-08-19: asset + both policies, both chains. NAV 11.18208300 — refused on the 18th, confirmed on the 19th. Bundles retained under `site2/data/`. |
| "Registry V2 not deployed; SDK addresses null" | **CLOSED.** Deployed both chains (testnet blk 38699818, mainnet blk 68389940), manifests committed, SDK addresses filled. **V2 holds no publications yet** — first v2 publication planned at the next epoch window. |
| "Demonstration ends only in refusal" | **CLOSED.** GuardedAction permit/refuse pairs on chain, both networks: testnet `0x5b6e65b9…`(1)/`0xfc9bcc47…`(0), mainnet `0x8b4b6c85…`(1)/`0x2b106907…`(0). |
| "AssetGateV2 does not enforce nonzero control-set root" | **CLOSED.** Contract-level `InvalidControlSetRoot`; tested. |
| "Approval digest never checked by the consumer" | **CLOSED.** `expectedApprovalDigest` immutable pin, `approval mismatch` refusal; tested. |
| "GuardedAction imports the concrete V1 gate" | **CLOSED.** Depends on `ITouchstoneGate`; works against either generation. |
| "main unprotected" | **CLOSED.** Required status check `required`, no force-push, no deletion. |
| "GitHub About overclaims 'signed onchain attestation'" | **CLOSED.** Narrowed to publisher-authenticated commitment + offline-verifiable report. |
| "Dossier lists one report; four reports lack public bundles" | **CLOSED with one stated gap.** All 11 reports listed; 9 bundles public and verified; the 2 testnet policy bundle *files* were overwritten by same-named mainnet ones (their signed reports remain in the transparency logs) — stated on the dossier, not hidden. |
| "Homepage count, OPERATIONS header, submission draft, judge 'Interactive' label stale" | **CLOSED**, this commit. |
| "Approvals ledger unsigned legacy data" | **OPEN — owner.** Signing exists; a new dated approval release signed by the owner's key is required. Backdating is refused on principle. |
| "Every publication hand-started; publisher disabled" | **OPEN — owner** (`DEPLOY-SERVICE.md` §3c). Workspace migration is done, so enabling is safe when decided. |
| "No external consumer / Builder Code / rebrand" | **OPEN — owner.** |
| "Truth gate scope insufficient" | **PARTIAL.** OPERATIONS.md added to the scan this commit; chain-snapshot-in-CI still open. |

## Status key

| | |
|---|---|
| **CLOSED** | built and verifiable now; the verification column says how |
| **PARTIAL** | substantively built, with a named remainder |
| **OPEN** | not started |
| **OWNER** | needs a decision or an account the agent does not hold |
| **DECLINED** | we judge the recommendation wrong on the merits, not merely unaffordable |

---

## A. The nine blockers

### Blocker 1 — "Proves skepticism better than usefulness" · **PARTIAL**

The audit's central finding, and the one everything else orbits: every report ends
`UNVERIFIABLE`, so the only question answered is one nobody asked.

**Built (commit `fbb0d2c`).** Policy profiles. Against the exact evidence that produced the
published `UNVERIFIABLE`:

```
[all approved controls]   UNVERIFIABLE
Disclosure freshness      UNVERIFIABLE
NAV settlement            UNVERIFIABLE
```

A policy is a versioned subset of approved controls that cannot extend the approved set,
reinstate a declined control, alter a threshold, or be edited in place. `touchstone/policy.py`,
`data/policies/*.json`, 31 tests.

**Local mechanics complete:** schema v5 carries policy identity and manifest digest, bundle
verification binds the policy before signature verification, and the publisher/service batch
path uses one policy key and workspace per policy. One retained capture now produces three
policy-scoped evaluations with one shared evidence root. All three correctly abstain for the
checked-in evidence; no retained current-policy `CONFIRMED` result, signed three-report bundle,
or live policy-key publication is claimed. The five historical v1 reports remain legacy and
unchanged.
**Re-audit verification:** run the policy evaluation over the retained capture and confirm three
policy-scoped results share the evidence root; confirm `data/policies/` manifests are
digest-committed and predate the evaluation, then inspect the v5 policy metadata in the bundle.

### Blocker 2 — "Public product is a dossier, not an application" · **PARTIAL**
`/judge` is live at https://touchstone.gudman.xyz/judge: one page, minimal JavaScript, that lets a judge complete the retained loop in two
minutes without a terminal — problem statement, film, live policy states, an interactive
policy check, evidence, AI provenance, explorer links, integration snippet, trust assumptions.
**Verification:** the served file matches the tested local SHA-256 and returns HTTP 200; an independent person completing the loop unaided remains open.

### Blocker 3 — "X Layer is a publication destination, not the centre" · **PARTIAL**
`PolicyGate` pinned to a frozen policy root, plus `GuardedAction` whose principal function
cannot execute unless the gate permits it. The local contract suite proves one permitted and
one refused action; the SDK carries ERC-8021 attribution bytes when an owner-registered code
is supplied. No live mainnet pair or Builder Code registration is claimed.
**Verification:** local tests are green; two explorer transactions and attribution visibility
remain owner-gated evidence.

### Blocker 4 — "No external consumer or market proof" · **OWNER**
Minimum acceptable: another X Layer project calling the gate, a public integration PR, a signed
design-partner statement, or an SDK transaction from a wallet that is not ours. Agent ships the
integration kit (Solidity interface, TS client, addresses, policy ids, example, indexer,
fixtures); the counterparty is the owner's to find.

### Blocker 5 — "Public facts contradict one another" · **PARTIAL**
Three instances found and fixed this session: `site2/dossier.html` claiming one report;
`docs/DEMO-RUNBOOK.md` §4 claiming the dossier was unbuilt and the gate never deployed while §3
said both were live; `AI_USAGE.md` describing only coding assistants.

**Then a fourth round, found by Codex.** It asserted contradictions remained without citing
one, so I checked every occurrence: the status page was listed `not_deployed` while `/status` is
live, and "live explorer link" and "mainnet addresses" were listed as not prepared while both
exist. Fixed — and the pattern is now four for four.

**Hand-sweeping has missed something every single time it has been tried.** The real fix is
mechanical: generate `project-state.json` from manifests, chain reads, transparency logs, the
approval ledger and the bundles; render every public surface from it; and add a CI check that
fails on a deployed contract called undeployed, a report count disagreeing with chain state, or
a known stale phrase.
**Verification:** delete a fact from a page and watch CI fail.

### Blocker 6 — "The onchain guarantee is narrower than the pitch" · **PARTIAL**
True and verified for v1: `TouchstoneRegistry` contains no signature verification. The legacy
contract proves *an authorized publisher posted these fields*, not that the status came from the
signed report. `TouchstoneRegistryV2` now binds an EIP-712 report digest, policy roots, parent
digest and signer identity in local Hardhat tests; it is not deployed or publicly claimed.

Two paths, and the sequencing matters:
1. **Immediately:** narrow every public claim to "publisher-authenticated onchain commitment
   with an offline-verifiable signed report." Never "trustless", never "onchain signature
   verification." This costs nothing and removes the overclaim today.
2. **Then:** deploy and independently verify registry v2 under owner authorization. The contract and Python codec are built; deployment remains a live owner action.
   onchain. Design open — see §D.

### Blocker 7 — "Human approval is a mutable field" · **PARTIAL**
Approvals now carry an EIP-712 signed artifact with approver identity, timestamp, decision, reason code, control digest and compilation digest. The verifier recovers the approver and binds the proposal exactly; the public judge renders the retained signature metadata. Existing published approvals are legacy and no fabricated replacement was written.
**Verification:** local recovery/tamper tests pass; recovering an approval from a new live publication remains owner-gated.

### Blocker 8 — "Operational maturity is not production-grade" · **PARTIAL**

Eight sub-items:

| Sub-item | Status |
|---|---|
| Run observation continuously | **CLOSED** — `touchstone-observer@xlayer-mainnet` is active and records all three USTB sources every 15 minutes |
| Automate one complete publication without manual initiation | **OPEN** — every slot to date was hand-started |
| Place the owner key offline | **OWNER** |
| Publisher key in a separate secret store or host | **PARTIAL** — separate Unix identity and unit exist, but the required `/etc/touchstone/xlayer-mainnet.env` is absent; no separate publisher host or secret store is configured |
| Two independent RPC providers for pre-publication reads | **PARTIAL** — fail-closed quorum boundary implemented and tested; two production endpoints are not configured |
| Schedule from UTC, not human-entered local time | **CLOSED** — the daemon schedules from `datetime.now(timezone.utc)`; the 20-minute miss was a human reading a local clock, not the scheduler |
| Publish a measured operations window without exaggerating it | **CLOSED locally** — `scripts/build_operations_metrics.py` produced `docs/OPERATIONS-METRICS-2026-08-19.json`; it records the exact window and leaves continuity open |
| Show completed, missed and corrected slot counts | **CLOSED locally** — recorded per workspace in `docs/OPERATIONS-METRICS-2026-08-19.json` without joining hash chains |

### Blocker 9 — "The brand is a material risk" · **OWNER**
The project's own `docs/BRAND-CLEARANCE.md` found an existing `touchstone-verify` product with a
near-identical verification architecture, a live RWA vault using the same name and metaphor, and
relevant trademarks. Contracts and deployment history can remain as legacy infrastructure.
**Do not replace one unchecked name with another invented in a rush.**

---

## B. What must be built to finish Blocker 1

1. **Policy metadata in the signed report** — policy id, version and manifest digest, so the
   signature covers which policy produced the state.
2. **Bundle commitment** — the offline verifier must check the policy digest before the
   signature, so a bundle cannot be re-labelled with a different policy.
3. **Chain publication per policy key** — one report per policy per epoch under
   `<asset>#policy:<id>:<version>`. No registry change: the contract is keyed by an opaque
   `bytes32` and enforces sequence, epoch uniqueness, corrections and lineage per key.
4. **A consumer that reads a policy key** — the existing `AssetGate` already takes a `bytes32`
   and needs no modification to gate on a policy.

---

## C. Remaining audit items

| Item | Status | Note |
|---|---|---|
| P0.1 retained demonstration set | **PARTIAL** | One unique retained v4 bundle is mirrored across two static sites; five unique retained bundles and a committed v5 bundle are not present |
| P0.2 judge application | **PARTIAL** | `/judge` is live with retained replay and a refusal-oriented path; unaided external completion and live v2/gate transactions remain open |
| P0.3 multi-policy proof | **PARTIAL** | One retained capture produces three policy-scoped evaluations sharing one evidence root; all abstain, and no current signed three-report bundle or live policy-key publication is retained |
| P0.4 mainnet consumer | **PARTIAL** | `GuardedAction` and the policy-key consumer path are built and tested locally; no live mainnet permitted/refused pair is claimed |
| P0.5 make AI visible | **CLOSED** | `AI_USAGE.md` rewritten with measured outcomes and published at `/docs/ai` |
| P0.6 green public CI run | OPEN | local suite is 1,958 passed / 1 skipped; a public Actions run remains owner evidence |
| P0.7 administrative eligibility | OWNER | X account, post URL, explorer links, form receipt, repo access |
| P1 Builder Code | OWNER + agent | |
| P1 external integration | OWNER | |
| P1 AI evaluation benchmark | **CLOSED locally** | 40 fixed compiler-boundary cases; 8 accepted, 6 abstained, 26 rejected, 100% hostile rejection; see `docs/AI-BENCHMARK.md` |
| P1 sign approvals | **PARTIAL** | EIP-712 signed artifacts recover a named approver and bind the exact control and compilation digests; the retained approval ledger and published approvals remain legacy/unsigned |
| P1 five-minute SDK kit | **PARTIAL** | `sdk/` includes TypeScript clients, Solidity interface, policies, canonical correction-aware indexer, attribution suffix path and fixture; an independent timed integration remains unproven |
| P2 second asset | **PARTIAL** | FOBXX SEC discovery/N-MFP3 normalizer, descriptor and hostile fixture tests ship; no live epoch/publication or daily issuer feed is claimed |

---

## D. Registry v2 — joint recommendation

My objection was that two signatures over one report means two things that can disagree, and if
they do, which is the report? Codex's design dissolves it, and we now agree:

**They are not two signatures over the same claim.** Ed25519 keeps signing the report — that is
the artifact, and the offline bundle depends on it. EIP-712/secp256k1 signs a *separate
attestation* whose subject is `{reportDigest, policyId, policyRoot, controlSetRoot,
evidenceRoot, publisher, validUntil}`. One says "this is the report"; the other says "this
report digest is what I put on chain". They cannot contradict each other because they assert
different things, and the second is checkable by an EVM contract, which Ed25519 is not.

This also delivers the benefit that argued for the change: a relayer can pay gas without
becoming the reporting authority, because the attestation names the signer.

**The five already-published reports stay as legacy v1**, unchanged, on the existing contract,
with a compatibility read path. Optional `legacy_bridge` entries may link an old report id to a
bundle digest **only where that digest can be proven from retained artifacts** — and they are
marked legacy and not policy-bound, because presenting a reconstruction as an original is the
one thing this project must never do.

**Sequencing:** report schema → bundle commitment and approver identity → registry v2 →
consumer. The registry cannot be designed before the fields it must store are fixed.

---

## D2. Where Codex and I disagreed, and how it resolved

Recording these because a plan both parties merely nodded at is worth less than one where the
disagreements are visible.

**1. Does `AssetGate` need modifying for policies? — No. I was right.**
Codex's plan allocated 1.5–2 days to "update `AssetGate` to require exact `policyId` +
`controlSetRoot`". `AssetGate.check(bytes32 assetKey)` passes its argument straight to
`registry.getLatestReport(assetKey)` and never interprets it
(`contracts/contracts/AssetGate.sol:47-48`). A policy key *is* a `bytes32`. The only thing that
must change is the constructor's `requiredControlSetRoot`, which is deploy-time configuration —
one gate per policy, pinned to that policy's root. Codex's own earlier memo said exactly this
before its plan contradicted it. **Work dropped.**

**2. Do the public docs still contradict themselves? — Yes. Codex was right.**
I had marked Blocker 5 partial and believed the remaining `not_deployed` strings were
definitional or true. Codex asserted contradictions remained without citing one, so I checked
every occurrence. Three were false: the status page listed as `not_deployed` while
`/status` is live, and "live explorer link" and "mainnet addresses" listed as not prepared
while both exist. **Fixed.**

That is the **fourth** consecutive sweep in which hand-checking missed something. It is the
strongest possible argument for keeping Blocker 5 open until public claims are generated
mechanically, and for not trusting my own "I checked it" on this class of defect.

---

## E. What I would decline to build, on the merits

- **Chasing the 200,000 USDT Launch Grant.** It needs 10M USDT of genuine OKX DEX interface
  volume. Adding a swap widget to look integrated produces no legitimate volume and distorts
  the product. The audit says this too.
- **Weakening any confirmation window to manufacture a `CONFIRMED`.** Policy profiles make this
  unnecessary; doing it anyway would destroy the property the project exists for.
- **Approving duplicate controls to raise the count.** Five were declined for good reasons,
  two of them explicitly as redundant.
- **Rushing USDY to claim a second asset.** Its own research found unresolved field definitions
  and a 260 MB unbounded archive.
