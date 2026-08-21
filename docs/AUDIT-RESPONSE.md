# Response to the external audit — build plan and traceability

The owner commissioned an external judge-perspective audit. This document exists so that a
**re-audit can check the auditor's own findings off one by one**, rather than reading a new
roadmap and trying to map it back.

Every finding the audit raised appears below with the same name the auditor gave it. Nothing is
quietly dropped: items we decline to build are listed as declined, with a reason.

> **Chronological audit record.** Older sections preserve what was true at their review
> commit; they are not current status. The newest repository facts live in `README.md`,
> `docs/OPERATIONS.md`, `docs/LIMITATIONS.md` and `docs/SUBMISSION-DRAFT.md`.

**Scope note.** This was drafted under a three-day deadline and re-scoped on 2026-08-19 when
the owner directed that the deadline not constrain the work. Items previously cut for time are
sequenced rather than dropped.

**Counted:** 9 numbered blockers, 7 Priority-0 items, 5 Priority-1 items, 2 Priority-2 items,
and 8 operational sub-items under Blocker 8 — 31 distinct findings, plus three incidental
defects the audit noted in passing.

---

## Status update — 2026-08-20, the fifth external review

The fifth review arrived as a hardening package pinned to `a432886` (three documents; its
patch scripts were reviewed as a specification and never executed — every claim below was
re-verified against the repository and the live chains before anything changed). Deltas:

| Its finding | Now |
|---|---|
| "Stale-phrase checks must be case-insensitive" | **CLOSED — and it was worse than claimed.** The scan lowercased the document but compared each phrase as recorded, so five of the eight recorded phrases could never match anything. The comparison now lowers the phrase at the moment of use, and a test walks every recorded phrase through a document containing it verbatim. The repaired gate found the public record clean. |
| "Truth gate must scan Operations, Limitations and the submission draft" | **CLOSED.** LIMITATIONS.md and SUBMISSION-DRAFT.md joined the default scan set; OPERATIONS.md and the SDK README were already in it. |
| "Docs must reflect the a432886 deployment state" | **CLOSED.** README, OPERATIONS, LIMITATIONS and SUBMISSION-DRAFT all still described 2026-08-18 — five reports all `UNVERIFIABLE`, publisher disabled, V2 undeployed, repository private, quorum unconfigured. Corrected against `site2/_data/facts.json`, live chain reads, and a live check of the production host's unit state and quorum variable. The suite count was itself off by one (1,977 then, 1,978 with the new gate test). |
| "Add SDK and Terminal tests to required CI" | **CLOSED.** New `sdk` job: `npm test` with an exact-count guard (15 — `node --test` exits 0 when its glob matches nothing, the same failure mode the hardhat count guards against) plus `node --check` on the Terminal. The aggregate now waits on eight jobs; `assert_ci_gates.py` enforces that it must. |
| "Terminal: no dynamic innerHTML" | **CLOSED.** Every panel renders through DOM builders; chain-sourced strings (reportURI, refusal reasons, revert details, dropped-file names) enter as text nodes. Zero `innerHTML` remains in `app.js`. |
| "Pin reads to one explicit block header" | **CLOSED.** One `eth_getBlockByNumber` pins each panel; registry and gate reads pass that block tag and the panel prints the block. |
| "Use chain block time for expiry, not the browser clock" | **CLOSED.** Expiry is judged against the pinned header's own timestamp. |
| "Compare configured RPC responses" | **CLOSED.** Pinned reads ask every configured endpoint and require byte-identical answers; disagreement is refused, and a lone responder is labelled "(only responder)" rather than blurred into agreement. Unpinned simulation keeps ordered failover — hosts on different heads disagree without either lying. |
| "Verify GuardedAction immutables and admission/gate binding before simulation or execution" | **CLOSED.** Both paths read the target's bindings back from the chain at a pinned block and compare them to config pins that were themselves read from both chains (all four GuardedActions' `gate()`/`assetKey()`, `admissionOf(freshness)`); mismatch aborts before any calldata is built. |
| "Abort if the visible action changes during preflight" | **CLOSED.** A selection snapshot taken at click is re-checked after every await; a changed selection aborts with nothing sent. |
| "Optional ERC-8021 Builder Code without inventing a code" | **CLOSED and exercised.** Owner-registered code `f0axgs7smtk2nfa7` is configured in the live Terminal. Mainnet transaction `0xb48cf6182b7bf87df78817401c7fefc2e8a319b341b96e572552775361fa9a1e` carries the exact schema-0 suffix, succeeded against the admission controller, and displays the code on the X Layer explorer. The byte layout is `sdk/src/attribution.ts`'s, pinned by the SDK suite's canonical vector and an independent browser implementation. |
| "Expand browser verification" | **CLOSED for: versioned schema (fail-closed on unknown versions), complete nested canonical/report equality both directions (proven against all 18 retained bundles), policy identity, ledger↔compilation binding, and the stored on-chain report (one pinned read against the chain the attestation names, digest compared). DECLINED for: control-set and evidence root recomputation in the browser — re-deriving the canonical hashing scheme in JavaScript risks a checkmark that lies, and the panel's "not checked here" row continues to say so plainly; the CLI recipes on /verify remain the way to check roots. |
| "Remove misleading mainnet bundle links from the two lost testnet policy entries" | **CLOSED for the current state.** Fresh chain-aware bundles for both testnet policies were retained on 2026-08-21 and are linked from the dossier. The two historical 2026-08-19 testnet artifacts remain unavailable and their rows link only their own transactions and attestations. |
| X post evidence | **CLOSED.** The launch post mentions `@XLayerOfficial` and is retained at https://x.com/TOUCH__STONE/status/2090844839055159485. |
| Form evidence, external adoption, multi-day unattended window, custody, independent security review, brand clearance | **OPEN — owner.** Builder Code registration and one attributed mainnet action closed on 2026-08-21; the remaining items need owner accounts, elapsed operating time or an independent party. |
| "Second live asset (FOBXX)" | **DECLINED for Phase 1.** Contradicts the recorded scope decision; FOBXX remains the documented monthly contrast asset without an adapter. |

---

## Status update — 2026-08-19 evening

The second external re-audit examined `fb7806e` and scored 8.0/10; five commits landed after
its snapshot. Deltas against its verdicts, each verifiable from chain or repo:

| Its verdict | Now |
|---|---|
| "No truthful live accepted policy; both panels UNVERIFIABLE" | **CLOSED.** First CONFIRMED states published 2026-08-19: asset + both policies, both chains. NAV 11.18208300 — refused on the 18th, confirmed on the 19th. Bundles retained under `site2/data/`. |
| "Registry V2 not deployed; SDK addresses null" | **CLOSED.** Deployed both chains (testnet blk 38699818, mainnet blk 68389940), manifests committed, SDK addresses filled. **V2 now holds its first publications** — both policies, both chains, 2026-08-19: testnet `0xd78803d2…`/`0x796dee43…`, mainnet `0x90736a7c…`/`0xf4cdbd1e…`. Each was submitted by the relayer (`0x5b4e381C…`) with the publisher recovered on chain from the EIP-712 attestation, under a two-provider RPC quorum, with the signed approval release's digest committed in the attestation payload. |
| "Demonstration ends only in refusal" | **CLOSED.** GuardedAction permit/refuse pairs on chain, both networks: testnet `0x5b6e65b9…`(1)/`0xfc9bcc47…`(0), mainnet `0x8b4b6c85…`(1)/`0x2b106907…`(0). |
| "AssetGateV2 does not enforce nonzero control-set root" | **CLOSED.** Contract-level `InvalidControlSetRoot`; tested. |
| "Approval digest never checked by the consumer" | **CLOSED.** `expectedApprovalDigest` immutable pin, `approval mismatch` refusal; tested. |
| "GuardedAction imports the concrete V1 gate" | **CLOSED.** Depends on `ITouchstoneGate`; works against either generation. |
| "main unprotected" | **CLOSED.** Required status check `required`, no force-push, no deletion. |
| "GitHub About overclaims 'signed onchain attestation'" | **CLOSED.** Narrowed to publisher-authenticated commitment + offline-verifiable report. |
| "Dossier lists one report; four reports lack public bundles" | **CLOSED with one historical gap class.** At that review: 11 reports listed and 9 bundles retained. Current state: 20 reports and 18 retained bundles. Fresh chain-aware testnet policy bundles exist for 2026-08-21; only the two overwritten 2026-08-19 testnet policy artifacts remain unavailable and are disclosed. |
| "Homepage count, OPERATIONS header, submission draft, judge 'Interactive' label stale" | **CLOSED**, this commit. |
| "Approvals ledger unsigned legacy data" | **CLOSED 2026-08-19.** The owner reviewed all ten decisions and signed: `data/compilations/APPROVALS-SIGNED-2026-08-19.json`, approver `0x537873b0…fA16Bc` recoverable from every artifact, timestamped at signing — the ledger's own dates remain the record of when each decision was made, and nothing was backdated. Verified 10/10 against `verify_signed_approval` and against the ledger's own lists. |
| "Every publication hand-started; publisher disabled" | **CLOSED for path proof, open for reliability.** The publisher was enabled under owner approval on 2026-08-20 and produced one unattended mainnet publication day. A multi-day operating window and production recovery remain unproven. |
| "No external consumer / Builder Code / rebrand" | **PARTIAL.** Builder Code closed with a live attributed mainnet action; external adoption and professional brand clearance remain open. |
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

### Blocker 1 — "Proves skepticism better than usefulness" · **CLOSED technically; adoption open**

The audit's central finding was that every report ended `UNVERIFIABLE`, so the only question
answered was one nobody asked. That was true at the audit snapshot. The current archive has
20 reports, 15 `CONFIRMED`, including both policy keys on both chains; external adoption remains
open and is tracked separately under Blocker 4.

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

**Current mechanics complete:** schema v5 carries policy identity and manifest digest, bundle
verification binds the policy before signature verification, and the publisher/service batch
path uses one policy key and workspace per policy. Current asset and policy reports share one
evidence root, bind the signed approval ledger, verify offline and are published under their
own keys. Historical v1 reports remain legacy and unchanged.
**Re-audit verification:** run the policy evaluation over the retained capture and confirm three
policy-scoped results share the evidence root; confirm `data/policies/` manifests are
digest-committed and predate the evaluation, then inspect the v5 policy metadata in the bundle.

### Blocker 2 — "Public product is a dossier, not an application" · **PARTIAL**
`/judge` is live at https://touchstone.gudman.xyz/judge: one page, minimal JavaScript, that lets a judge complete the retained loop in two
minutes without a terminal — problem statement, film, live policy states, an interactive
policy check, evidence, AI provenance, explorer links, integration snippet, trust assumptions.
**Verification:** the served file matches the tested local SHA-256 and returns HTTP 200; an independent person completing the loop unaided remains open.

### Blocker 3 — "X Layer is a publication destination, not the centre" · **PARTIAL**
`AssetGateV2` pins policy identity, policy root, control-set root and signed approval digest;
`GuardedAction` and `RWAAdmissionController` cannot execute their protected actions unless the
gate permits them. Permit/refuse transactions are live on mainnet. The SDK and live Terminal
carry the registered Builder Code's ERC-8021 attribution bytes. One mainnet admission execution
is mined with the exact suffix and visible explorer attribution.
**Verification:** deployment facts and explorer transactions are indexed by the dossier; the
attributed execution is `0xb48cf6182b7bf87df78817401c7fefc2e8a319b341b96e572552775361fa9a1e`.

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

**Hand-sweeping has missed something every single time it has been tried.** The mechanical
state builder, generated site facts and CI truth gate now exist. The 2026-08-21 audit found one
remaining hole: README and `site2/data/stats.json` could disagree numerically with committed
chain facts while CI passed. The gate now compares those report counts and the enumerated
report states directly.
**Verification:** change either public count and `scripts/assert_public_truth.py` fails.

### Blocker 6 — "The onchain guarantee is narrower than the pitch" · **PARTIAL**
True and verified for v1: `TouchstoneRegistry` contains no signature verification. The legacy
contract proves *an authorized publisher posted these fields*, not that the status came from the
signed report. `TouchstoneRegistryV2` binds an EIP-712 report digest, policy roots, parent
digest and signer identity. It is deployed on both chains and carries ten current policy
attestations; the legacy v1 guarantee remains narrower and is still described separately.

Two paths, and the sequencing matters:
1. **Immediately:** narrow every public claim to "publisher-authenticated onchain commitment
   with an offline-verifiable signed report." Never "trustless", never "onchain signature
   verification." This costs nothing and removes the overclaim today.
2. **Completed:** registry v2 was deployed and source-verified on both chains under owner
   authorization; current policy attestations are live. See §D and the deployment manifests.

### Blocker 7 — "Human approval is a mutable field" · **PARTIAL**
Approvals now carry an EIP-712 signed artifact with approver identity, timestamp, decision,
reason code, control digest and compilation digest. The verifier recovers the approver and binds
the proposal exactly; the public judge renders the retained signature metadata. Current policy
reports and Registry V2 attestations bind the signed ledger digest; earlier publications remain
historical legacy records.
**Verification:** local recovery/tamper tests pass and the current published policy bundles bind
the signed approval ledger.

### Blocker 8 — "Operational maturity is not production-grade" · **PARTIAL**

Eight sub-items:

| Sub-item | Status |
|---|---|
| Run observation continuously | **CLOSED** — `touchstone-observer@xlayer-mainnet` is active and records all three USTB sources every 15 minutes |
| Automate one complete publication without manual initiation | **CLOSED for path proof** — the 2026-08-20 and 2026-08-21 mainnet slots each published the asset and both policies unattended; a sustained reliability window remains open |
| Place the owner key offline | **OWNER** |
| Publisher key in a separate secret store or host | **PARTIAL** — the root-owned production environment and separate service identity are configured, but the key remains on the shared production host without an HSM, KMS or separate publisher host |
| Two independent RPC providers for pre-publication reads | **CLOSED for the current mainnet path** — fail-closed quorum is implemented and the 2026-08-21 gate and publication state were confirmed through two configured providers |
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
| P0.1 retained demonstration set | **CLOSED for the current archive** | 18 of 20 published reports have retained downloadable bundles. Fresh chain-aware bundles exist for both current testnet policies; the only unavailable files are the disclosed overwritten 2026-08-19 testnet policy artifacts |
| P0.2 judge application | **PARTIAL** | `/judge` is live with retained replay, current policy proof and live v2/gate transactions; unaided external completion remains open |
| P0.3 multi-policy proof | **CLOSED** | Current asset and policy reports share an evidence root, bind the signed approval ledger, verify offline and are published under their policy keys on both chains |
| P0.4 mainnet consumer | **CLOSED** | `AssetGateV2`, the GuardedAction permit/refuse pair and `RWAAdmissionController` are live on mainnet with real permitted and refused transactions |
| P0.5 make AI visible | **CLOSED** | `AI_USAGE.md` rewritten with measured outcomes and published at `/docs/ai` |
| P0.6 green public CI run | **CLOSED at `d2d1668`** | 1,985 Python tests passed with 1 skipped, 111 contract tests, 15 SDK tests and 125/125 mutation targets; public required CI succeeded at https://github.com/Ridwannurudeen/touchstone/actions/runs/32508657320 |
| P0.7 administrative eligibility | **PARTIAL / OWNER** | X account, launch post URL, explorer links and public repository are evidenced; form submission and receipt remain owner-controlled and open |
| P1 Builder Code | **CLOSED** | Registered code `f0axgs7smtk2nfa7`; live Terminal execution succeeded at block 68574822 and the X Layer explorer displays the code |
| P1 external integration | OWNER | |
| P1 AI evaluation benchmark | **CLOSED locally** | 40 fixed compiler-boundary cases; 8 accepted, 6 abstained, 26 rejected, 100% hostile rejection; see `docs/AI-BENCHMARK.md` |
| P1 sign approvals | **CLOSED for current publications** | The signed approval ledger contains 10 recoverable EIP-712 decisions, and current policy reports and Registry V2 attestations bind its digest; earlier publications remain historical legacy records |
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

## F. Audit #3 (2026-08-19) — the required completion sequence, tracked

The third external report examined the project after the first CONFIRMED states but before
that evening's signing and v2 publications, so two of its five "must complete" items were
done before it arrived. Status of its required sequence:

| # | Audit #3 item | Status |
|---|---|---|
| 1 | Create a new signed approval release | **DONE before the audit landed** — `APPROVALS-SIGNED-2026-08-19.json`, 10 EIP-712 decisions, one recovered approver. Then improved past the ask: the signatures are embedded in the ledger itself (version 2), so `approval_ledger_sha256` — the digest reports already bind and RegistryV2 already stores — now names signed decisions with no schema change. Every v2 entry must be signed and is bound to the exact compiler proposal it decides, declined entries included |
| 2 | Produce new bundles binding the new approval digest | **DONE 2026-08-20** — the first unattended slot on the VPS produced all three reports (asset + both policies) CONFIRMED, sharing one evidence root and binding the signed ledger digest `4f2c7dd5…`; offline verification passes on every bundle. (Was: READY, gated on the next epoch — the pipeline binds the current ledger digest at report build; the next slot (2026-08-20, after the 86,400 s confirmation age) produces them. Publishing a same-day "correction" to rebind the digest was rejected: tonight's reports were true when signed, and a correction that restates a correct report manufactures an error |
| 3 | Publish all three reports through Registry V2 | **DONE for the policy keys through sequence 3 on mainnet as of 2026-08-21; asset-wide leg resolved by design, not publication** — both policies sit in v2 as relayer-submitted attestations binding the signed ledger. The asset key deliberately does NOT enter v2: the contract accepts only `sequence == latest+1`, the asset's lineage now stands at v1 sequence 5, and the only way to seat it in v2 would be a fabricated sequence-1 report colliding with the real 2026-08-17 one — manufactured lineage. v2 serves the asset's v1 history through `getLegacyLatestReport`/`getLegacyReport`, which is what those functions exist for; the zero-policy asset-wide semantics remain in the codebase for assets whose lineage begins after v2. (Was: UNBLOCKED — the v2 path was policy-only (`attestation_from_report` refused asset-wide reports); asset-wide semantics are now defined and tested: zero policy identity, legal only as a pair, unpinnable by construction because AssetGateV2 refuses zero pins. Publication follows item 2 |
| 4 | Redeploy hardened AssetGateV2 | **DONE on mainnet 2026-08-20** — `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059`, all eight pins including approval digest `4f2c7dd5…` (the signed ledger), first live check `(true, "allowed")` against Registry v2; source verified on OKLink. Testnet follows its evening slot. (Was: WAITING on items 2–3 by design — the gate pins the approval digest exactly, and the only digest worth pinning is the signed ledger's. Pinning today's on-chain reports would pin the unsigned artifact the audits called weak |
| 5 | Deploy the meaningful consumer | **LIVE on mainnet 2026-08-20** — `RWAAdmissionController` at `0x5C5265392701A99cbB137aF8116E0F97f630329A`, source verified; on-chain story complete: propose (admitted + never-reported key), activate on the gate's word, execute (useCount 1), and the refused activation as a real status-0 transaction. (Was: BUILT — `RWAAdmissionController`: proposer-bound gate per asset, activation only on the gate's word, suspension computed on every read and every privileged action rather than stored, admission history permanent. Nine tests cover the audit's four demonstration scenarios plus suspension-recovery with no state mutation. Deployment follows item 4 |
| 6 | Verify source on OKLink | **DONE 2026-08-20** — the owner supplied the API key and all ten live contracts verified on OKLink, five per chain, each verification byte-proving its constructor arguments (including the original testnet gate's zero control-set root). Two traps closed on the way: the plugin's built-in list maps `xlayertest` to chain 195, the deprecated testnet — a `customChains` entry teaches it 1952 — and the plugin fetches its solc list from `solc-bin.ethereum.org`, which is dead; patched in node_modules to `binaries.soliditylang.org` (okverify is a local attended task, so CI never runs it). The superseded first registry stays unverified by decision. AssetGateV2 and the AdmissionController verify at deploy time |

Also from audit #3, fixed same evening: three public surfaces still carried pre-CONFIRMED
text, and the truth gate learned all six phrases it had missed (its stale-phrase list grows
in one direction only). Bundle filenames now lead with the chain id, closing the same-name
overwrite that lost two testnet policy bundles.

---

## G. Audit #4 (2026-08-19, website vs RedStone-level) — execution record

The fourth external report audited the public website: "an unusually polished technical
dossier" that needed to become a product site, with a P0/P1/P2 ladder ending in a live
Policy Terminal at the center and the dossier moved into the proof layer behind it.

**Review note:** Codex reached its usage limit mid-evening on 2026-08-19 and is unavailable
until 2026-08-21; this batch's design review ran against the second reviewer only, and the
whole batch queues for Codex re-review when it returns.

Executed (agent):
- Pages are now **generated from one canonical data source** (P0.11): `scripts/build_site.py`
  renders `site2/_pages/` sources with shared header/footer partials and `{{fact:*}}` tokens
  from `site2/_data/facts.json` (chain facts, committed and chain-verified) plus facts derived
  from the tree; CI refuses a rendered page that does not match its source. `build_docs.py`
  and `build_status.py` consume the same partials, because the docs template's own footer had
  gone stale exactly the way every hand-typed fact here has.
- Every address and transaction on the site was **re-verified from chain** before it became a
  fact: contracts classified at the deployer's CREATE addresses on both chains, publication
  transactions recovered from the workspaces' transparency logs, v2 attestations and
  permit/refuse executions recovered by bounded log scans (the public endpoints cap
  `eth_getLogs` at 100 blocks; a silent-empty retry wrapper produced a zero-event run first,
  and was replaced with one that raises).
- **Policy Terminal** at `/app` (P1.1–5): live reads of both registries on both chains, gate
  checks with exact on-chain reasons, wallet connect (EIP-1193; OKX Wallet when present),
  chain switching, simulate and execute of guarded actions, a local in-browser bundle
  verifier (Ed25519 via WebCrypto, approver and v2-attestation recovery via a self-hosted
  ethers build) that states what it does not check, and a per-wallet execution history scan.
  The nginx CSP stays `script-src 'none'` everywhere except the `/app` route, whose policy
  names exactly the X Layer RPC hosts the page may reach.
- Full information-architecture rebuild (P0.1–7, 10): new nav (Products / Solutions / Assets
  / Developers / Security · Launch Terminal), outcome-led homepage per the audit's blueprint,
  product stack, solutions personas, assets pages, developers and security pages, judge page
  cut to the five things, verify page in three tabs, dossier with network/registry/correction
  badges and explorer links, Open Graph and JSON-LD metadata, sitemap and robots.
- Chain-aware bundle filenames (P0.8) had landed in the pipeline the same evening.

Owner-gated from this audit: an external integration; OKX.AI service listing; institutional/company pages; brand
clearance. Deliberately deferred with reasons: the Next.js/Astro rebuild (§15 — the audit
itself licenses keeping the lightweight static architecture with the Terminal as an island;
a framework migration days before the deadline is how judges meet a broken site) and the
six illustration systems (P2, after the deploys).

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
