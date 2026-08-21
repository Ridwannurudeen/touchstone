# The remaining work, ranked

> **Historical execution plan.** This file preserves the pre-confirmation diagnosis and the
> work order that produced Registry V2, signed approvals, mainnet enforcement and unattended
> publication. It is not current project status. Use `README.md`, `docs/OPERATIONS.md`,
> `docs/LIMITATIONS.md` and `docs/SUBMISSION-DRAFT.md` for current facts.

Three inputs produced this list: an external judge-perspective audit of the whole project, two
Codex audits of the recent build, and defects found while working them. It is ordered by
judge impact per hour, not by how interesting the work is.

**Scope changed 2026-08-19.** This was first written as three-day triage against the
2026-08-21 deadline. The owner has since directed that the deadline not constrain the work:
build and fix everything properly, with the resources to finish it. So the cuts made for time
are reinstated as sequenced work rather than dropped, and the ordering below optimises for a
system that is correct and complete rather than for what fits before Friday.

The submission is **not** in. The form and every public post remain owner-gated; the current
state is recorded in `docs/SUBMISSION-DRAFT.md`.

Each item says who can do it. **Owner** means it needs a decision or an account only the owner
holds — those are not blocked on engineering and should be started first because they have the
longest lead time.

### What the deadline was forcing us to drop, now back in scope

| Was cut for time | Why it matters |
|---|---|
| **Registry v2 with EIP-712** | The contract currently proves *an authorized publisher posted these fields* — not that the status came from the signed report. That gap is the difference between "publisher-attested" and "verifiable". |
| **Signed approvals** | Human approval is the strongest defence against a valid-looking malicious proposal, and it is an unauthenticated JSON field. |
| **AI evaluation benchmark** | The compiler's measured behaviour is currently 72 outcomes from ordinary use, not an adversarial corpus. Fabricated citations and prompt injection are untested at scale. |
| **A second asset (FOBXX)** | Regulator-filed evidence rather than issuer-published is a different *class* of source, and the platform claim is not real with one. |
| **Rebrand** | A collision with an existing product occupying a near-identical verification architecture. |

---

## The one-sentence diagnosis

> Touchstone proves skepticism better than usefulness. Every published report ends
> `UNVERIFIABLE`, the gate refuses, and nothing external consumes it — so the honest question
> a judge asks is *"can this ever enable anything, or is it only a sophisticated way to say
> no?"*

Everything in P0 exists to answer that question truthfully, without weakening a single control.

---

## P0 — competition-critical

### 0.1 Policy profiles: one truthful pass, one truthful refusal
**Agent.** The single highest-value change, and the answer to the diagnosis above.

Stop reducing an asset to one universal verdict. Different consumers need different evidence,
so publish per-policy states from the same retained evidence:

| Policy | Requirement | Honest result today |
|---|---|---|
| Disclosure freshness v1 | holdings and yield disclosures exist and are inside their declared windows | **can pass** |
| NAV settlement v1 | NAV row survives a second capture at the minimum age | **still refuses** |

Both from identical evidence, neither weakened, and **no policy created or changed after
seeing its result**. That is the difference between "it says no" and "it answers precisely the
question you asked it".

Predeclare, version and sign the policy roots before evaluating. A new policy version gets a
new id; an existing one is never silently edited.

### 0.2 One canonical state, and CI that fails on contradiction
**Agent.** The systemic fix for a defect that has now recurred three times.

Public claims have contradicted each other repeatedly — a dossier saying "there is one" report
while operations recorded five; a runbook whose §3 said the gate was live and whose §4 said it
had never been deployed. Each sweep fixed what it looked at and missed a file it did not.

Generate `project-state.json` from deployment manifests, chain reads, transparency logs, the
approval ledger and the bundles. Render homepage metrics, the dossier index, README tables,
report counts, control counts, the status page and submission facts *from that file*. Then add
a CI check that fails when a deployed contract is called undeployed, a report count disagrees
with chain state, or a known stale phrase reappears.

**A verification product cannot survive stale copy.** Hand-sweeping has been tried and has
missed something every time.

### 0.3 The judge page
**Agent.** The submission form asks for a URL and a repository, so those carry the whole
argument. A judge should understand the product in ten seconds and complete the loop in two
minutes without a terminal.

One page: the problem in a sentence, the 90-second film, live policy states, an interactive
policy check, mainnet and testnet evidence, AI provenance, explorer links, an integration
snippet, the trust assumptions, the bundle.

### 0.4 A mainnet consumer whose action cannot skip the gate
**Agent to build, owner to approve the deploy.** `AssetGate` currently emits an event after
checking; it protects nothing. Deploy `PolicyGate` (pinned to a frozen policy root) plus a
`GuardedAction` whose principal function cannot execute unless the gate permits it. Zero-value
is fine; inseparability is the point. Record one permitted and one refused action.

### 0.5 Resolve the workspace split — release blocker
**Agent to execute, owner to authorise.** Every published report came from the local mainnet
workspace; the VPS observer is building a *separate* evidence history. If the publisher unit is
enabled on the host it will publish from the VPS history, which has no transparency log, no
bundles and no link to the reports the public site shows. Two evidence histories presented as
one product.

Resolution: back up both trees, make the local published-history workspace canonical, copy it
to the host before the publisher ever runs there, and keep the VPS-only captures separately
rather than concatenating two hash chains.

### 0.6 A green public CI run
**Agent.** The repository claims ~1,818 tests. No judge will run them. Expose a green Actions
run, the test matrix, contract tests, the mutation harness and a tagged release, so the claim
is checkable rather than trusted.

### 0.7 Administrative eligibility, evidenced not asserted
**Owner.** The repo currently records some of these as owner-reported. Retain and link: the
active dedicated X account, the exact post URL mentioning @XLayerOfficial, testnet and mainnet
explorer evidence, the form receipt, and public repository access. An administrative omission
disqualifying a technically strong project would be the worst possible loss.

---

## P1 — high impact, after the loop works

- **0.8 Sign human approvals.** *Agent.* Approval is the strongest defence against a
  valid-looking malicious proposal, and it is currently an unauthenticated JSON field with no
  approver identity, signature or timestamp. Make it an EIP-712 signed artifact and show the
  signature beside the model proposal: AI proposed, deterministic code validated, a **named
  human** approved.
- **0.9 Make the AI visible.** *Agent.* `AI_USAGE.md` currently reads as though AI wrote the
  code. A judge opening only that file would miss the production compiler entirely. Add an
  `/ai` page: provider, requested and returned model, prompt hash, evidence digest, candidate
  output, each deterministic gate, the approval signature, and abstention reasons.
- **0.10 Register an X Layer Builder Code** and attach ERC-8021 attribution to app-originated
  transactions. *Owner registers, agent wires.* Turns ecosystem contribution from a claim into
  a measurement.
- **0.11 One external integration.** *Owner.* Worth more than any documentation. Minimum: one
  other X Layer project calling the gate, a public integration PR, a signed design-partner
  statement, or an SDK transaction from a wallet that is not ours.
- **0.12 A five-minute integration kit.** *Agent.* Solidity interface, TypeScript client,
  addresses, policy ids, example, indexer, fixtures.

---

## P2 — only after the loop is complete

- **AI evaluation benchmark**, 30–50 cases: fabricated citations, wrong source binding, prompt
  injection, self-approval attempts, duplicates. Report exact-span validity, acceptance rate,
  abstention rate, injection rejection.
- **A second asset — FOBXX, not USDY.** The SEC N-MFP3 route is regulator-filed rather than
  issuer-published, which strengthens the platform story; USDY still has an unresolved
  arithmetic discrepancy and an unbounded archive, and rushing it would repeat exactly the kind
  of overclaim this project exists to refuse.
- **Registry v2** binding an EIP-712 report digest, parent digest and signer identity onchain.
  Until it exists, **do not call the current registry trustless**: it proves an authorized
  publisher posted these fields, not that the status was derived from the signed report.

---

## Owner decisions that gate everything else

1. **Rebrand.** *(Highest lead time.)* The brand audit found an existing `touchstone-verify`
   product with a near-identical verification architecture — Ed25519, hash-chained records,
   offline verification — plus a live RWA vault using the same name and metaphor, and relevant
   trademarks. Contracts and deployment history can stay as legacy infrastructure; the public
   name, site, X account, repo description and SDK package would change. **Do not replace one
   unchecked name with another invented in a rush.**
2. **Enable the publisher on the host** — puts a signing key on a shared box (`DEPLOY-SERVICE.md` §3c).
3. **Merge `feat/t12-ci` into `main`** — 66+ commits; `main` has never seen any of this work.
4. **Repo public** after the deadline.

---

## Explicitly not worth the remaining time

Adding documentation sections; approving duplicate controls to hit a count; weakening the
confirmation window to manufacture a `CONFIRMED`; a token; rushing USDY; redesigning the visual
system again; chasing DEX volume for the Launch Grant, which needs 10M USDT of genuine
interface volume and is not a credible target for a non-trading product.

The static documentation, the offline verifier and the fail-closed engine are already strong.
What remains is turning them into something a protocol can use.
