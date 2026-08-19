# Build plan — closing the external audit

`docs/AUDIT-RESPONSE.md` says *what* the auditor found and where each finding stands. This says
*how it gets built*, in order, with the verification a re-audit can run.

Drafted jointly: the sequencing and design decisions below are agreed between the two reviewing
agents except where §9 records a disagreement.

**Scope:** all 31 findings. **Constraint:** correctness over speed — the owner has removed the
deadline as a limit.

---

## The constraint that shapes Phases 0–2

The offline verifier accepts **exactly one** schema version and **exact** field sets:

```python
if bundle["version"] != BUNDLE_VERSION:   # verify.py:210
if report["version"] != REPORT_VERSION:   # verify.py:361
if set(report) != _REPORT_FIELDS:         # verify.py:359
```

Current: `touchstone.observation-report.v4` / `touchstone.verification-bundle.v4`, 16 report
fields, 8 bundle fields.

**So adding policy metadata to a report is a breaking change.** Do it carelessly and the five
already-published bundles stop verifying — which would be the single worst possible regression,
because "a stranger can check this offline" is the project's core claim.

Every schema step below is therefore: bump to v5, teach the verifier **both**, keep v4 bundles
verifying forever, and prove it with the retained bundles as fixtures.

---

## Phase 0 — Foundations (nothing downstream is safe without these)

### 0.1 Report and bundle schema v5, with v4 kept verifiable
**Files:** `touchstone/report.py`, `touchstone/verify.py`, `touchstone/signing.py`, fixtures.
**Size:** 1.5–2 days.

Add `policy` to the report: `{policy_id, policy_version, policy_digest, control_ids}` — absent
on an asset-wide report, present on a policy report. Bump both versions to v5. The verifier
dispatches on version and keeps the v4 path intact.

**Verification:** the five retained bundles verify under v5 code unchanged; a v5 policy bundle
verifies; a v5 bundle with a tampered `policy_digest` fails; a v4 bundle with policy fields
injected fails.

### 0.2 Canonical project state and CI that fails on contradiction
**Files:** new `scripts/build_project_state.py`, `scripts/assert_public_truth.py`,
`.github/workflows/ci.yml`, every renderer.
**Size:** 2–3 days.

Generate `project-state.json` from deployment manifests, chain reads, transparency logs, the
approval ledger, policy manifests and bundles. Render homepage metrics, dossier index, README
tables, status page, coverage and submission facts *from it*. Add a CI gate failing on: a
deployed contract described as undeployed, a report count disagreeing with chain state, a
control count disagreeing with the ledger, a network named without a chain id, or any phrase on
the stale list.

**Why this is Phase 0 and not a cleanup task:** hand-sweeping the public record has been tried
four times and missed something every time, including twice after I had declared it clean. It
is not a discipline problem; it is the wrong mechanism.

**Verification:** delete a true fact from a page and watch CI fail; change a report count and
watch CI fail.

---

## Phase 1 — Finish policy profiles end to end

Policy evaluation exists and is proven (`touchstone/policy.py`, 31 tests). It is not wired to
anything.

### 1.1 Evaluate and report per policy
**Files:** `touchstone/epoch.py`, `touchstone/report.py`, `touchstone/ustb_daemon.py`.
**Size:** 1–1.5 days. **Depends on:** 0.1.

One epoch, one evidence capture, N+1 reports: the asset-wide report plus one per policy, each
signed, each committing its policy digest. Evaluation reuses the same observations and the same
confirmation predecessor — no extra fetch.

**Verification:** one run produces three reports from one capture set; all three share an
`evidence_root`; each policy report's digest commits its manifest.

### 1.2 Publish per policy key
**Files:** `scripts/publish_epoch.py`, `scripts/run_service.py`.
**Size:** 0.5–1 day. **Depends on:** 1.1.

Publish under `<asset>#policy:<id>:<version>`. **No registry change** — the contract is keyed by
an opaque `bytes32` and enforces sequence, epoch uniqueness, corrections and lineage per key
(`TouchstoneRegistry.sol:87,198`). The key format is already accepted by `publish.py` and
`verify.py`.

**Verification:** three registry keys each with an independent sequence; `epochSequence` per
policy; a correction on one policy leaves the others untouched.

### 1.3 Consumer gates reading policy keys
**Files:** `contracts/scripts/deploy_gate.js`, deployment manifests. **No Solidity change.**
**Size:** 0.5 day. **Depends on:** 1.2.

`AssetGate.check(bytes32)` passes its argument straight to `getLatestReport` and never
interprets it (`AssetGate.sol:47-48`). Deploy one gate per policy, each pinned to that policy's
`requiredControlSetRoot`.

**Verification:** the freshness gate returns `(true, "allowed")` and the settlement gate returns
`(false, "status not allowed")` **in the same block**, from the same evidence.

> That pair of calls is the demo. One transaction permitted, one refused, same asset, same
> instant, nothing weakened.

---

## Phase 2 — Make X Layer the centre, not a destination

### 2.1 `GuardedAction`
**Files:** new `contracts/contracts/GuardedAction.sol` + tests.
**Size:** 1–1.5 days. **Depends on:** 1.3.

A consumer whose principal function cannot execute unless its gate permits. Zero-value is fine;
**inseparability is the point** — no path may reach the underlying function without the check.

**Verification:** a test proving the underlying function is unreachable directly; one permitted
and one reverted mainnet transaction, both on the explorer.

### 2.2 Builder Code and ERC-8021 attribution
**Files:** deploy scripts, the judge page's transaction path.
**Size:** 0.5 day + owner registration.

### 2.3 Integration kit
**Files:** new `sdk/` — Solidity interface, TypeScript client, addresses, policy ids, worked
example, event indexer, fixtures.
**Size:** 2 days.

**Verification:** a clean clone integrates in under five minutes following only the README.

---

## Phase 3 — Close the trust gap

### 3.1 Narrow the public claim *first*
**Files:** README, `site2/`, `docs/SUBMISSION-DRAFT.md`. **Size:** 2 hours.

The registry today proves *an authorized publisher posted these fields*. Until 3.3 ships, every
public surface says "publisher-authenticated onchain commitment with an offline-verifiable
signed report" — never "trustless", never "onchain signature verification". **This lands before
any v2 work**, because the overclaim is live now and the fix costs nothing.

### 3.2 Signed approvals
**Files:** `touchstone/approval.py`, `touchstone/verify.py`, `data/compilations/APPROVALS.json`.
**Size:** 1.5–2 days. **Depends on:** 0.1.

Approval becomes an EIP-712 signed artifact over `{control_digest, compilation_digest,
decision, reason_code, timestamp}` carrying approver identity. Existing unsigned entries stay
readable and are labelled unattributed — they are historical fact and deleting them would hide
the correction history.

**Verification:** recover the approver address from a published approval; a tampered decision
fails recovery; the five published reports still verify.

### 3.3 Registry v2
**Files:** new `contracts/contracts/TouchstoneRegistryV2.sol`, publisher, verifier.
**Size:** 3–4 days. **Depends on:** 0.1, 3.2.

**Agreed design.** Ed25519 keeps signing the report — that is the artifact, and the offline
bundle depends on it. EIP-712/secp256k1 signs a *separate attestation* over
`{reportDigest, policyId, policyRoot, controlSetRoot, evidenceRoot, publisher, validUntil}`.

They are not two signatures over one claim: one says *"this is the report"*, the other says
*"this digest is what I put on chain"*. They cannot contradict, and only the second is
EVM-checkable. It also lets a relayer pay gas without becoming the reporting authority.

Add a parent digest so each report is linked to its predecessor by **content**, not merely by
sequence.

**The five published reports stay legacy v1**, unchanged, with a compatibility read path. Bridge
entries only where a digest is provable from retained artifacts, marked legacy and not
policy-bound — presenting a reconstruction as an original is the one thing this project must
never do.

**Verification:** a v2 report whose EIP-712 signer is recovered on chain; a mismatched digest
rejected; every v1 report still readable.

---

## Phase 4 — The product surface

### 4.1 `/judge`
**Size:** 2–3 days. **Depends on:** 1.3, and 2.1 for the action step.

Select asset and predeclared policy → official source and retrieval time → labelled compile or
retained replay → AI-proposed typed control → highlighted supporting bytes → every deterministic
gate it passed → signed human approval → current evaluation → the X Layer report → a guarded
action → the explorer transaction.

Ten seconds to understand, two minutes to complete, no terminal.
**Verification:** an independent person completes it unaided and unprompted.

### 4.2 The film, reshot
**Size:** 0.5 day. **Depends on:** 1.3.

The current cut ends on refusal. The seven beats stand; beat 5 gains the pass/refuse pair.
Owner narrates.

---

## Phase 5 — Operational maturity (Blocker 8's eight sub-items)

| # | Item | Status | Work | Size |
|---|---|---|---|---|
| 1 | Run observation continuously | **CLOSED** | live since 2026-08-18 | — |
| 2 | One publication with no manual initiation | OPEN | enable the publisher unit; prove from the journal | 0.5 d + owner |
| 3 | Owner key offline | OWNER | hardware or offline host | — |
| 4 | Publisher key on a separate host | PARTIAL | separate identity done; separate **host** needs a second VPS | 0.5 d + owner |
| 5 | Two independent RPC providers | OPEN | quorum read before publication; refuse on disagreement | 1 d |
| 6 | Schedule from UTC | **CLOSED** | already does | — |
| 7 | Publish a measured window | OPEN | from the transparency log and heartbeat history | 0.5 d |
| 8 | Completed / missed / corrected slot counts | OPEN | derived, on `/status` | 0.5 d |

**Never claim "production uptime" from a short window.** Publish the measured number and the
window it was measured over.

### 5.1 Resolve the workspace split — blocker on 5.2
**Size:** 0.5 day. **Depends on:** owner authorisation.

Every published report came from the local mainnet workspace; the VPS observer builds a separate
evidence history. Enabling the publisher on the host would publish from a history with no
transparency log and no link to the public reports. Back up both, make the published-history
workspace canonical, copy it before the publisher runs, keep VPS-only captures separately, and
**never concatenate two hash chains**.

---

## Phase 6 — Breadth, only after the loop is real

### 6.1 AI evaluation benchmark
**Size:** 2–3 days. 30–50 adversarial cases: fabricated citations, wrong source binding, wrong
asset binding, malformed schemas, prompt injection, self-approval attempts, low-confidence
ambiguity, duplicates, stale sources. Report exact-span validity, deterministic acceptance,
abstention and injection-rejection rates.

### 6.2 A second asset — FOBXX
**Size:** 3–4 days. Regulator-filed SEC N-MFP3 evidence is a different *source class* from
issuer-published, which is what makes the platform claim real. Monthly rather than daily, and
the contrast is the point.

**Not USDY.** Its own research found an unresolved arithmetic discrepancy
(`total/outstandingValue` = 100.87% against a published 105.29%) and a 260 MB unbounded archive.

---

## Owner track — start now, longest lead time

| Item | Why it cannot wait |
|---|---|
| **Rebrand decision** | An existing product occupies a near-identical verification architecture. Contracts stay as legacy; the public name, site, X account, repo description and SDK package change. Do not replace one unchecked name with another chosen in a rush. |
| **One external integration** | Worth more than any amount of documentation. Another X Layer project calling the gate, a public integration PR, a signed design-partner statement, or an SDK transaction from a wallet that is not ours. |
| **Builder Code registration** | Gates 2.2. |
| **Administrative eligibility** | X account, the exact @XLayerOfficial post URL, explorer links, form receipt, repository access. An administrative omission disqualifying a technically strong project would be the worst possible loss. |
| **Publisher enable + key custody** | Gates 5.2 and 5.4. |

---

## Sequencing

```
0.1 schema v5 ─┬─ 1.1 per-policy reports ── 1.2 publish ── 1.3 gates ─┬─ 2.1 GuardedAction ── 2.2 Builder Code
               │                                                      └─ 4.1 /judge ── 4.2 film
               ├─ 3.2 signed approvals ──┐
               └──────────────────────────┴─ 3.3 registry v2
0.2 canonical state + CI ── (gates every public claim from here on)
3.1 narrow the claim  ← independent, ship immediately
5.x operations        ← parallel, owner-gated at 5.1/5.2
6.x breadth           ← last
```

**Critical path:** 0.1 → 1.1 → 1.2 → 1.3 → 2.1 → 4.1. Roughly 9–12 working days of agent time.
Everything else parallelises against it.

**Total agent estimate: 26–35 working days**, excluding owner items and re-audit cycles.

---

## §9 — Recorded disagreements

**AssetGate needs no Solidity change.** Codex's draft allocated 1.5–2 days to making the gate
policy-aware. `check(bytes32)` passes its argument straight to `getLatestReport` and never
interprets it; only the pinned root differs, which is constructor configuration. Codex's own
earlier memo said this before its plan contradicted it. **Dropped from the plan.**

**The public docs still contradicted themselves.** Codex asserted this without citing an
instance; checking every occurrence found three false claims. It was right and I was wrong, and
that is the fourth consecutive hand-sweep to miss something. It is why 0.2 is Phase 0.

---

## Declined, on the merits rather than on schedule

- **The 200,000 USDT Launch Grant.** It requires 10M USDT of genuine OKX DEX interface volume.
  A swap widget added to look integrated produces no legitimate volume and distorts the product.
- **Weakening any confirmation window to manufacture a `CONFIRMED`.** Policy profiles make it
  unnecessary; doing it anyway destroys the property the project exists for.
- **Approving duplicate controls to raise the count.** Five were declined for stated reasons,
  two explicitly as redundant.
- **A policy marketplace.** Unrelated to any finding and it widens the trust boundary.
- **Retroactive correctness claims for the five published reports.** They stay as they are.
