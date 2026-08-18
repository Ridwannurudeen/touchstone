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
Disclosure freshness      CONFIRMED
NAV settlement            UNVERIFIABLE
```

A policy is a versioned subset of approved controls that cannot extend the approved set,
reinstate a declined control, alter a threshold, or be edited in place. `touchstone/policy.py`,
`data/policies/*.json`, 31 tests.

**Remainder:** policy metadata is not yet in the signed report, not committed in the bundle,
not published to any chain, and no consumer reads it. That is items B.1–B.3 below.

**Re-audit verification:** run the policy evaluation over the two retained captures and confirm
two different states from one evidence set; confirm `data/policies/` manifests are digest-
committed and predate the evaluation.

### Blocker 2 — "Public product is a dossier, not an application" · **OPEN**
Build `/judge`: one page, minimal JavaScript, that lets a judge complete the loop in two
minutes without a terminal — problem statement, film, live policy states, an interactive
policy check, evidence, AI provenance, explorer links, integration snippet, trust assumptions.
**Verification:** an independent person completes the loop unaided.

### Blocker 3 — "X Layer is a publication destination, not the centre" · **OPEN**
`PolicyGate` pinned to a frozen policy root, plus `GuardedAction` whose principal function
cannot execute unless the gate permits it. One permitted and one refused mainnet action.
Builder Code registration and ERC-8021 attribution on app-originated transactions.
**Verification:** two explorer transactions, one succeeding and one reverting with a reason;
attribution visible.

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

### Blocker 6 — "The onchain guarantee is narrower than the pitch" · **OPEN**
True and verified: `TouchstoneRegistry` contains no signature verification. The contract proves
*an authorized publisher posted these fields*, not that the status came from the signed report.

Two paths, and the sequencing matters:
1. **Immediately:** narrow every public claim to "publisher-authenticated onchain commitment
   with an offline-verifiable signed report." Never "trustless", never "onchain signature
   verification." This costs nothing and removes the overclaim today.
2. **Then:** registry v2 binding an EIP-712 report digest, parent digest and signer identity
   onchain. Design open — see §D.

### Blocker 7 — "Human approval is a mutable field" · **OPEN**
Approval currently records no approver identity, signature, timestamp or rationale. Make it an
EIP-712 signed artifact and show the signature beside the model proposal, so the division reads
end to end: AI proposed, deterministic code validated, **a named human** approved, deterministic
surveillance evaluated, X Layer enforced.
**Verification:** recover the approver address from a published approval.

### Blocker 8 — "Operational maturity is not production-grade" · **PARTIAL**

Eight sub-items:

| Sub-item | Status |
|---|---|
| Run observation continuously | **CLOSED** — `touchstone-observer@` live on the host since 2026-08-18 |
| Automate one complete publication without manual initiation | **OPEN** — every slot to date was hand-started |
| Place the owner key offline | **OWNER** |
| Publisher key in a separate secret store or host | **PARTIAL** — separate Unix identity and root-owned `0600` env; still the same host as the site |
| Two independent RPC providers for pre-publication reads | **OPEN** |
| Schedule from UTC, not human-entered local time | **CLOSED** — the daemon schedules from `datetime.now(timezone.utc)`; the 20-minute miss was a human reading a local clock, not the scheduler |
| Publish a measured operations window without exaggerating it | **OPEN** |
| Show completed, missed and corrected slot counts | **OPEN** |

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
| P0.1 public-truth rebuild | OPEN | same work as Blocker 5 |
| P0.2 judge application | OPEN | Blocker 2 |
| P0.3 policy profiles | PARTIAL | Blocker 1 |
| P0.4 mainnet consumer | OPEN | Blocker 3 |
| P0.5 make AI visible | **CLOSED** | `AI_USAGE.md` rewritten with measured outcomes and published at `/docs/ai` |
| P0.6 green public CI run | OPEN | claims ~1,849 tests; no judge will run them |
| P0.7 administrative eligibility | OWNER | X account, post URL, explorer links, form receipt, repo access |
| P1 Builder Code | OWNER + agent | |
| P1 external integration | OWNER | |
| P1 AI evaluation benchmark | OPEN | 30–50 adversarial cases: fabricated citations, wrong binding, injection, self-approval |
| P1 sign approvals | OPEN | Blocker 7 |
| P1 five-minute SDK kit | OPEN | |
| P2 second asset | OPEN | **FOBXX, not USDY** — regulator-filed evidence is a different source class; USDY has an unresolved arithmetic discrepancy and an unbounded archive |

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
