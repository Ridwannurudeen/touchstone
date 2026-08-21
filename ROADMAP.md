# Touchstone — Build Plan & Roadmap

> **Status note (2026-08-21):** the Phase 0/Phase 1 calendar below is a historical planning
> record, not current deployment or operations state. Statements such as “mainnet is
> unscheduled,” hand-started-only publication, and the scripted two-act demo describe the
> plan when written and have been superseded. Current verified state is recorded in
> `site2/_data/facts.json`, `site2/data/stats.json`, `docs/OPERATIONS.md`, and
> `docs/SUBMISSION-DRAFT.md`.

> **Name chosen by the owner on 2026-08-13: Touchstone** — the stone assayers rubbed
> gold against to verify what it really was before anyone accepted it.
> That claim was corrected on 2026-08-14: **existing crypto projects do use the name** —
> see `docs/BRAND-CLEARANCE.md`, which records a Morpho vault curator branded Touchstone
> for gold-RWA lending (announced 2026-07-15, contracts live on Ethereum) and a PyPI SDK
> `touchstone-verify` implementing signed, hash-chained disclosure verification
> (2026-07-12). Known adjacent
> mark: Touchstone Investments (Cincinnati mutual-fund/ETF firm, Western & Southern) —
> different business (asset management vs. verification software), but a trademark
> opinion from counsel is required before commercial launch. DNS quick-check:
> touchstone.finance, touchstonelabs.xyz, touchstone-rwa.com show no DNS records
> (registrar confirmation pending); touchstone.xyz and usetouchstone.com resolve and
> are likely taken. Domain purchase, handles, and any announcement remain owner actions.
> (Prior internal codename ARGUS retired — collision with Cobo Argus.)

**Mission.** Touchstone is the policy and control plane for tokenized assets. It turns
issuer, regulator, custodian, oracle and onchain evidence into explicit policy for the
contracts, wallets and AI agents that act on it. The current USTB vertical begins with
issuer-published evidence: AI proposes cited, machine-checkable controls; deterministic
surveillance evaluates them; accountable humans approve policy; signed results are
published to X Layer for enforcement.

**Grant wedge.** Touchstone makes RWA liquidity conditional on verifiable evidence.

Touchstone does not issue assets, custody funds, recommend investments, assign credit
ratings, or claim facts beyond the evidence class it has actually verified.

**The sentence the project is built around:**

> *"I accepted this RWA because its required evidence was current, attributable, and
> independently verifiable through Touchstone."* — a future X Layer application

---

## Product principles

1. **Evidence classes are never inflated.** An issuer webpage is issuer disclosure — not
   independent attestation. An API observation is not proof of reserves. Retrieval
   failure is not issuer failure.
2. **AI proposes meaning; deterministic systems control state.** AI compiles evidence
   into typed controls. Schema validation, approved policies, and deterministic
   evaluation decide registry transitions.
3. **Every accepted claim is traceable** to an exact evidence span, source URL, artifact
   hash, retrieval time, compiler version, and control version.
4. **Abstention is a valid result.** `UNVERIFIABLE` is more valuable than a confident
   hallucination.
5. **Onchain state must have a consumer.** The registry is incomplete without a contract
   that admits, pauses, or rejects based on its state.
6. **Corrections are append-only.** Reports may be superseded, never silently rewritten.
7. **Public claims remain conservative.** Touchstone reports what it observed and verified —
   not whether an asset is safe, compliant, solvent, or suitable.
8. **Every deployment, post, grant application, partnership message, and submission
   requires owner approval.**

## System architecture

```text
Official issuer sources
        │
        ▼
Allowlisted isolated fetchers
        │
        ▼
Content-addressed evidence store
        │   raw bytes + source metadata · exact hashes + retrieval time
        │   immutable version history
        ▼
AI control compiler
        │   typed candidate control · exact supporting span
        │   confidence + abstention · compiler/prompt/model version
        ▼
Deterministic validator + approval gate
        ▼
Versioned approved control set
        ▼
Source-specific surveillance adapters
        ▼
Deterministic evaluator
        ▼
Signed observation report
        │   Ed25519 signature · hash-chained transparency log
        │   offline-verifiable bundle
        ▼
X Layer registry
        │   control-set root · evidence root · status + freshness
        │   immutable transition history
        ├───────────────┐
        ▼               ▼
   AssetGate      Living dossier
```

## Category and product boundary

Touchstone is not an oracle, credit rating or legal-compliance oracle. It determines
whether a specific, predeclared evidence policy is currently supported and gives a
consumer an enforceable answer. Price and NAV providers answer what value was reported;
custodians answer how an asset is held; permissioned-token standards answer whether an
investor or transfer is eligible. Touchstone answers whether the complete evidence policy
required for this action currently holds.

The long-term product spans the full policy lifecycle, while liquidity admission remains
the first commercially legible application:

```text
Asset onboarding
      â†“
Evidence collection
      â†“
Policy compilation and accountable approval
      â†“
Market, collateral or agent admission
      â†“
Continuous surveillance and automatic suspension
      â†“
Recovery, reactivation, audit and governance
```

The north-star platform has eight connected layers. They are a sequence, not a claim that
an enterprise suite exists today:

| Layer | Current foundation | Next proof required |
|---|---|---|
| Asset Passport | USTB descriptor, dossier and policy history | One canonical asset view covering authorities, dependencies, incidents and integrations |
| Evidence Network | Allowlisted bounded retrieval, content-addressed lineage, authority classes | Required/corroborating/informational/fallback semantics and multiple live authority classes |
| AI Policy Compiler | Provenance-bound proposals, deterministic gates, benchmark, signed human decisions | Model registry, critic evaluation, change control, rollback and NIST AI RMF mapping |
| Policy Studio | Versioned policy manifests and signed approval tooling | Simulation, maker-checker review, activation, expiry, exceptions and approval packs |
| Decision Network | Registry v2 attestations, separated logical roles, relayer and chained reports | Independent approvers, hardware-backed custody and quorum for material policies |
| Enforcement Control Plane | Policy-pinned gate and admission controller | Reusable vault, permissioned-token, lending, issuance and agent adapters |
| Institutional Console | Public terminal and proof surfaces | Authenticated organizations, RBAC, SSO, case management and private evidence |
| Developer and Agent Platform | Solidity and TypeScript clients plus event indexing | Stable API, webhooks, MCP service and controlled Agentic Wallet flow |

No layer graduates because its UI exists. It graduates when an independently operated
consumer uses it, its failure mode is measured, and its authority boundary is auditable.

## Evidence status entering Phase 0 (as of 2026-08-13)

Preliminary probes are encouraging but do not replace the formal audit:

- **PAXG (Paxos):** transparency page fetched directly — monthly attestation reports,
  KPMG LLP since Feb 2025. Direct repeatable PDF retrieval from the deployment
  environment **unverified**.
- **OUSG / USDY (Ondo):** secondary sources report end-of-business-day NAV updates,
  daily third-party reporting, daily Ankura Trust attestations for USDY. Exact
  endpoints and historical accessibility **unverified**.
- **USTB (Superstate):** documented public NAV/holdings API. Whether every required
  endpoint is unauthenticated and stable from the VPS **unverified**.
- **BENJI/FOBXX (Franklin Templeton):** official page exposes prospectus material and
  daily yield fields. Stable machine retrieval **unverified**.
- RWA.xyz may corroborate but is never the authoritative source for controls.

---

# Phase 0 — Evidence and brand kill gates (Aug 13–14)

**Purpose:** prove Touchstone can monitor real evidence before any product code expands.

**Source audit** of PAXG, OUSG, USDY, USTB, BENJI + one spare. Per source, record:
asset identity and canonical contract addresses; publishing legal entity; first-party
URL; evidence class; MIME type and true file signature; login/cookie/JS/anti-bot
requirements; retrieval from the production VPS (repeatedly — browser-only access is
insufficient); redirect behavior and URL stability; publication cadence, timezone,
holidays, grace period; version semantics; historical-version availability;
terms-of-use constraints; observable structured fields; proposed controls with exact
evidentiary limits; failure behavior.

**Asset acceptance gate.** An asset enters the build only with: an attributable
official source; repeatable no-login retrieval; ≥2 honest machine-observable controls;
explicit update semantics; a defensible asset-identity mapping; hashable, retainable
evidence; an adapter feasible within the sprint; no manual-download dependence.

**Portfolio selection:** one **hero** asset (strongest machine-readable daily evidence
— provisional order: USTB, then USDY, then OUSG/BENJI), one **second daily** asset
(cross-issuer repeatability), one **contrast** asset (likely PAXG — monthly cadence and
staleness semantics). PAXG is never the hero: monthly evidence may not change in the
judging window.

**Deliverables:** `SOURCE_AUDIT.md`; accepted source manifests; rejected candidates
with reasons; raw sample artifacts + hashes; proposed controls; cadence and failure
rules; evidence-class glossary; initial threat model; brand-collision report. No
issuer-specific negative findings published at this stage.

**Abort conditions.** Reject any asset requiring fragile interactive sessions,
aggregator-only data, unknowable cadence, speculative legal interpretation, unretainable
evidence, uncertain contract mapping, or controls claiming more than the source proves.
**Abort Touchstone entirely if fewer than two candidates pass. Do not preserve the concept by
lowering evidence standards.**

**Brand clearance for "Touchstone" (deadline Aug 14, before the project X account
opens):** completed 2026-08-14 and recorded in `docs/BRAND-CLEARANCE.md`. The earlier
2026-08-13 finding that no existing crypto/web3 project uses the name **was wrong** — two
live projects use it, one of them in this project's own verification lane;
Touchstone Investments (US mutual-fund/ETF firm) is the known adjacent financial mark,
different service class but requiring a counsel trademark opinion before commercial
launch. Remaining checks: USPTO, WIPO, EUIPO/TMview, Nigerian and operating-market
registries, GitHub, npm/PyPI, CoinGecko/CoinMarketCap/DefiLlama, explorers,
X/Telegram/Discord/Farcaster/LinkedIn handles, app stores, ENS; phonetic and visual
similarity. DNS quick-check found touchstone.finance, touchstonelabs.xyz, and
touchstone-rwa.com unresolving (registrar confirmation pending); touchstone.xyz and
usetouchstone.com likely taken. Domain purchase and handle registration are owner
actions.

# Phase 1 — Hackathon build (Aug 13–21)

**Dependency order (corrected):** evidence audit → state and control semantics → golden
fixtures → contract interface → live source adapter → compiler/evaluator → registry
publishing → consumer gate → product UI → operations hardening. Contracts encode
settled semantics; they are not designed before the evidence model.

### Aug 13 — Evidence kill gate
Audit sources; select hero/second/contrast; capture real artifacts + metadata; define
allowed evidence classes; start brand clearance; minimum repo structure for audit
artifacts. **Exit gate:** hero source retrieves repeatedly from the intended server; ≥2
honest controls identified; one historical observation exists for comparison; no
unresolved ambiguity about what the source proves.

### Aug 14 — Semantics, fixtures, threat model
Freeze control-language v0 **before contracts**. Control fields: `asset_key`,
`control_id`, `control_version`, `predicate_type`, `subject`, `source_id`,
`source_authority_class`, `evidence_span`, `cadence`, `grace_period`,
`observation_adapter`, `comparison_operator`, `expected_value`, `effective_from`,
`effective_until`, `compiler_confidence`, `approval_state`.
Asset states: `CONFIRMED` / `STALE` / `INCONSISTENT` / `UNVERIFIABLE`. Operational
events kept separate: `RECONFIRMED` / `EVIDENCE_CHANGED` / `SOURCE_ERROR` /
`CORRECTION_PUBLISHED`. A transient outage is `SOURCE_ERROR`, never instant
inconsistency; only an expired evidence deadline moves status toward `STALE`.
Also: golden evidence fixtures, expected compiler outputs and abstentions,
state-transition test matrix, adversarial-document tests, contract storage model, UI
wireframe, submission-copy skeleton.

### Aug 15 — Contracts and testnet foundation
`TouchstoneRegistry`: canonical asset key, control-set root, evidence root, status,
`observedAt`, `validUntil`, publisher, monotonic sequence, report URI. Protections:
publisher authorization, replay protection, monotonic enforcement, chain/domain
separation, correction events, key rotation, **no custody or value-transfer logic**.
`AssetGate`: allowed statuses, maximum observation age, required publisher, optional
control-set version. Tests cover every transition, stale boundary, unauthorized
publisher, replay, correction, rotation. Deploy fixture-driven system to X Layer
testnet only after tests pass.

### Aug 16 — Hero compiler and source adapter
Full hero ingestion path: allowlist → raw retrieval → content hash → normalization →
exact evidence-span extraction → AI candidate control → schema validation → confidence
gate → explicit abstention → deterministic observation evaluation.
**Security:** all documents are adversarial input; the model gets no shell, network,
wallet, or contract tools; instructions embedded in evidence cannot self-approve a
control or move it into evaluation — **narrowed 2026-08-15**: steering is a stated
residual, because a well-formed injected candidate is accepted as a proposal and only the
approval gate stops it (see `docs/THREAT-MODEL.md` T9 and R-9);
allowlisted URLs, blocked redirects; MIME/magic-byte/size/decompression/page/time
limits; isolated parsing worker; model ID, prompt hash, compiler version, input hash,
raw output all recorded. **Exit gate:** one real source compiles into an exact cited
control; ambiguous examples abstain; golden cases reproduce.

### Aug 17 — Signed surveillance and onchain publication
Scheduled epochs; Ed25519-signed reports; hash-chained transparency log; offline
verification bundle; idempotent submission; receipt tracking; restart reconciliation;
failure recovery without duplicate publication; hero report on testnet; working
`AssetGate` consumption. **By end of day the narrow vertical works end to end — if not,
stop adding assets.**

### Aug 18 — Product experience
Hero living dossier: state + freshness, accepted controls, evidence excerpts + hashes,
"what Touchstone verified" vs "what it did not verify", transition timeline, explorer links,
offline-verifier download, evidence-grounded "why this state?" interaction, developer
integration page. No wallet required to browse. Second source only after the hero flow
is polished.

### Aug 19 — Coverage and operations
*(Amended 2026-08-16: the second daily asset and the contrast asset are cut. See the
completion-metrics table — the two-adapter target is recorded as missed, not retargeted.)*
Public heartbeat; source
health; last successful epoch; next scheduled epoch; gas runway; incident history;
watchdog; alerting; backup/restore; production-like deployment rehearsal. **Contract
interface frozen at end of day.**

### Aug 20 — Hardening and release candidate
Full contract suite; pipeline tests; golden compiler evaluations; adversarial-document
tests; browser smoke tests; restart/recovery; duplicate-publication tests; source-outage
simulations; key-rotation rehearsal; restore rehearsal; full hero-demo rehearsal.
Prepare: verified addresses, release commit, deployment manifest, rollback procedure,
public limitations, submission form draft, X posts (drafts). *(Amended 2026-08-16: the
line that stood here staged a mainnet deployment for approval on Aug 20 — before the
Aug 21 testnet proof it was supposed to depend on. Mainnet is unscheduled and conditional
on a proven testnet loop.)* Stage the **testnet** replacement-registry deployment package
and request owner approval.

### Aug 21 — Testnet release candidate (internal deadline 18:00 UTC)

**Amended 2026-08-16.** This date previously scheduled a mainnet deploy and production
canary. That was not a credible plan and contradicted this repository's own owner gates,
which permit mainnet only "after a proven testnet loop" — and nothing had then published to
testnet at all. Scheduling an action the same documents forbid is how an operator ends up
taking it.

Aug 21 is now a **testnet** target: prove the loop end to end on X Layer testnet — deployed
registry, one autonomous live epoch, an offline-verifiable bundle, and a consumer contract
gating on the published state. After explicit approval only, and no feature work on the day.

**Mainnet is unscheduled.** It is conditional on the completed testnet loop above being
proven, not on a date, and returns as a separate owner decision.

### Hackathon completion metrics
Three real assets documented; ≥2 fully autonomous live adapters; ≥6 accepted controls;
100% of accepted claims span-cited and hash-bound; zero unproven production claims; one
live consumer contract gating on Touchstone state; every transition independently
verifiable; all contract authorization boundaries tested; public interface wallet-free;
watchdog + incident history + reconciliation operational; every scheduled epoch through
Sept 1 completed or publicly recorded as an incident.

**Standing as of 2026-08-16.** The targets are not restated downward; what is missed is
recorded as missed.

| Metric | Target | Actual |
|---|---|---|
| Accepted controls | ≥6 | **8 — met.** Each is a candidate a model proposed from issuer bytes, bound by digest to the compilation that accepted it |
| Assets documented | 3 | **3 — met.** USTB, USDY and FOBXX source manifests with golden fixtures |
| Fully autonomous live adapters | ≥2 | **1 — MISSED.** ⚠️ This row read "0 proven live… has never run against live sources" until 2026-08-18, by which point USTB had published five reports across two chains from live issuer retrieval. The row was stale, not conservative. The count is **one**: USTB runs against live sources. It is still a miss against a target of two, and **no continuity claim attaches to it** — every slot so far was hand-started, so the daemon has never held a sustained schedule. USDY is blocked on unbounded retrieval *and* an unreconciled issuer arithmetic discrepancy; FOBXX is a documented monthly contrast asset, not an adapter. **Phase 1 deliberately ships one flawless vertical rather than two hurried ones** |
| Live consumer contract gating on state | 1 | **1 — met on testnet, 2026-08-18.** `AssetGate` at `0xAac48DC261B04737FDCB101D5049395121034a83`, X Layer testnet, block 38602126. `check(USTB)` returns `(false, "status not allowed")` — it refuses, because the latest report is `UNVERIFIABLE` and the mask admits `CONFIRMED` only. Not deployed on mainnet: `requiredControlSetRoot` is immutable and the approved set is still moving |
| Production canary epoch | 1 | **1 — met on mainnet, 2026-08-18.** USTB sequence 1 on X Layer mainnet (chain 196), epoch `ustb-2026-08-18`, state `UNVERIFIABLE`, later restated by sequence 2 (a correction). Mainnet holds two reports, testnet three. A testnet canary preceded both on 2026-08-17, block 38526525 |
| Claims span-cited and hash-bound | 100% | Met for every accepted control |

## The minimum hero demo (90–120 seconds, two acts)

**Act 1 — AI makes disclosure executable.** Judge opens the hero dossier (no wallet);
sees source, evidence class, limitations; runs "Compile disclosure" on the labeled
testnet demo path; Touchstone fetches the official source; AI extracts one bounded
commitment into a typed control; the UI highlights the exact supporting span;
deterministic validation confirms span-in-artifact, schema, source identity, operator,
confidence; the accepted control root publishes to testnet. If the compiler cannot
support the control, **it abstains live — never hidden**.

**Act 2 — current evidence changes contract behavior.** A demo `AssetGate` shows
`REQUIRES_REFRESH` (its freshness requirement is deliberately stricter than the last
observation age); Touchstone retrieves the current official daily observation; UI shows
previous vs current values (an ordinary NAV movement is never called a risk event);
report signed; transparency log appends; freshness renews on X Layer; the gate flips to
`ACCEPTED`; judge opens the explorer transaction; judge asks "why did the gate accept
this asset?" and the dossier answers solely from accepted graph nodes. The gate reacts
to **verification freshness**, never declares the asset safe.

**Pre-staged:** deployed contracts, publisher authorization, funded account, verified
adapters, previous genuine evidence snapshot, previous signed epoch, approved control
set, demo consumer, hash-bound cached fallback artifacts, recorded fallback walkthrough.
**Never pre-staged:** modified issuer evidence, fabricated reports, synthetic NAV
changes presented as real, hidden manual state changes, self-funded activity described
as user activity. If the live source fails, switch **visibly** to verified historical
replay mode and log the incident.

## Aug 21 – Sept 1 operations calendar

Daily: poll each source per its documented window; offchain source-health checks; one
signed observation per asset; anchor fresh observations on X Layer; one aggregate daily
epoch root; heartbeat update; honest `RECONFIRMED`/`EVIDENCE_CHANGED`/failure surfacing;
onchain reconciliation; gas and watchdog checks.

| Date | System activity | Public milestone (drafts; owner approves) |
|---|---|---|
| Fri Aug 21 | **Testnet** RC target: first live testnet epoch, initial dossier roots. No mainnet action | Drafts only; nothing published |
| Sat Aug 22 | Weekend re-observation (no business-day NAV promised) | Architecture/verification thread |
| Sun Aug 23 | Reconfirmation or honest source-health incident | — |
| Mon Aug 24 | First post-weekend publication windows | First genuine fresh observation, if published |
| Tue Aug 25 | Daily surveillance and state renewal | Evidence-to-X-Layer lifecycle clip |
| Wed Aug 26 | Daily surveillance | Developer post: `AssetGate` integration |
| Thu Aug 27 | Daily surveillance | Only if state materially changes |
| Fri Aug 28 | Weekly reliability summary | 7-day uptime, epochs, incidents, corrections |
| Sat–Sun Aug 29–30 | Weekend reconfirmations; snapshot-readiness check | Heartbeat is the visible proof |
| Mon Aug 31 | Business-day + month-end monitoring | Only actual issuer changes (never promise PAXG timing) |
| Tue Sep 1 | Fresh snapshot-day epoch; archived verification bundle | Snapshot report + next-phase commitment |

**Reliability objectives:** 100% of scheduled epochs complete or produce a public
incident; 99.5% availability in the window; alert ≤5 min on daemon failure; safe
auto-restart ≤15 min; no duplicate registry sequence after restart; daily encrypted
backup; one tested restore before submission and one during the window; gas runway
through Sept 3+; no incident ever removed from public history.

---

# Phase 2 — Product proof (Sept–Oct 2026)

**Objective:** prove teams other than Touchstone want to consume the primitive.

Deliverables: 8–10 production-grade assets across ≥3 RWA categories (same evidence
standard — never a vanity count); verification API v1; Python and TypeScript SDKs;
webhooks/event subscriptions; commissioned rescans via x402; integration sandbox;
external contract security review; initial legal review (research language, source
terms/copyright, financial-promotion boundaries, liability and corrections, issuer
naming/trademark); methodology, conflict-of-interest, correction, and appeals policies;
two X Layer design partners.

**Graduation metrics:** ≥8 assets at production evidence standards; ≥30 active
controls; 100% citation-bound claims; 99.5% surveillance SLO over 30 days; corrections
within 24h; two independent testnet integrations; one external team using `AssetGate`
or the API publicly; **first paid** commissioned adapter, rescan, or freshness
subscription.

**First revenue** comes from a protocol, wallet, or infrastructure team — never from
issuers paying for favorable state. Issuer-funded integrations are disclosed and
organizationally separated from methodology.

**Checkpoint:** Touchstone is a credible X Layer project when another builder publicly
consumes its state. Deployment alone is not adoption.

# Phase 3 — Open standard and institutional readiness (Nov–Dec 2026)

Deliverables: Control Language Spec v0.1; Registry Interface Spec v0.1; Evidence Bundle
Spec v0.1; reference compiler + offline verifier; reference clients; formal threat
model; independent smart-contract audit; public compiler evaluation set; published
methodology-change process; publisher/key-rotation spec; xStocks-readiness research
package (contingent on confirmed ecosystem direction — never presented as guaranteed);
corporate-action control prototypes (separate from core); first non-Touchstone publisher
sandbox; a NIST AI RMF / Generative AI Profile mapping for compiler governance; and an
SSDF-based software-development control baseline. ISO/IEC 27001 remains a later management-
system and certification path, not a badge earned by adding documentation.

**Graduation metrics:** 25 production-grade assets; 5 external integrations (≥2 live);
3 external spec/adapter contributors; zero unresolved critical/high contract findings;
public compiler evaluation with measured precision and abstention (≥90% precision on
supported control families; coverage may stay lower through abstention); 3 paying
organizations or $25k cumulative revenue; one external publisher accepted in a
non-material test environment.

**Monetization added:** metered API plans, enterprise freshness SLAs, integration and
adapter fees, dedicated evidence archives, paid on-demand scans, protocol support
contracts. The public dossier, base verification endpoint, specs, and offline verifier
stay open.

**OKX / X Layer strategy:** monthly ecosystem contribution reports; position as
listing-diligence and collateral-admission infrastructure (never the listing
decision-maker); request technical feedback from X Layer RWA/devrel; grants only with
owner approval; demonstrate how tokenized equities could consume disclosure and
corporate-action controls; never imply OKX/X Layer/issuer endorsement without written
confirmation; registry and reference consumer stay native to X Layer even as observed
assets span chains.

# Phase 4 — Verification network (2027 H1)

**Objective:** remove Touchstone as the sole publisher.

Deliverables: curated multi-publisher network with identities and performance history;
independent observation comparison; challenge and correction interface; publisher
quorum policies; conflict disclosures; disaster-recovery regions; adapter certification
tests; issuer-side machine-readable disclosure tooling; multi-chain observation with X
Layer as the canonical registry home. **No staking, slashing, or token merely to claim
decentralization** — accountable independent publishers with observable performance
come first.

**Graduation metrics:** 100 covered assets; 5 independent publishers; 10 production
consumers; 99.9% SLO over 90 days; ≥2 externally maintained publisher implementations;
challenge acknowledgement <4h; confirmed corrections <24h; 10 paying organizations or
~$250k ARR; no single publisher required by every consumer policy.

# Phase 5 — The independent RWA verification institution (2027+)

Public positioning is **"the open verification and admission layer for tokenized
real-world assets"** — never "the Moody's of RWA" (that implies subjective ratings,
regulated activity, and authority not yet earned).

Deliverables: independent methodology and appeals council; formal legal/compliance
structure; publisher accreditation; public methodology and model-risk reports; annual
audits; cross-jurisdiction source policies; coverage across major RWA categories and
origin chains; exchange, wallet, custodian, and DeFi integrations; an issuer disclosure
standard designed for machine verification; institutional-grade historical evidence
archive; model/compiler reproducibility program.

**Long-term metrics:** 500 production-grade assets; 10+ RWA categories; 10 origin
chains; 25+ production consumers; 10 independent publishers; 99.95% SLO; published
annual transparency and model-risk reports; majority of registry reports produced or
corroborated outside the founding organization; 50 paying organizations or >$1M ARR; no
unresolved critical integrity incident; demonstrable use in listing, collateral,
wallet, or settlement workflows.

---

## Defensibility — what compounds

The Solidity registry and dossier UI are cloneable; the moat is everything that
accumulates: (1) the content-addressed historical evidence graph; (2) the corpus of
accepted/rejected/corrected/abstained controls tied to exact evidence; (3) compiler
evaluations built on real financial-document failure cases; (4) hardened issuer-specific
adapters with known cadence and failure semantics; (5) the public operational record —
uptime, correction speed, incident transparency; (6) integration depth in production
consumers; (7) publisher reputation over time; (8) issuer relationships that yield
machine-readable feeds without surrendering independence; (9) open-standard leadership
— contributors converge on Touchstone interfaces even with other publishers; (10) trust
earned through restraint — a public history of abstaining instead of overclaiming.
A well-funded team can reproduce the first demo. It cannot instantly reproduce years of
evidence lineage, correction history, and integration trust.

## Security, integrity, and governance ladder

**Immediate (hackathon):** separated deployer / publisher / Ed25519-reporting /
operations keys; no secrets in code, logs, bundles, or clients; onchain key rotation;
monotonic sequences + replay protection; allowlisted adapters; sandboxed parsing;
prompt-injection tests; full model/prompt version recording; signed release manifests;
daily backups + tested restore; public status and incident history.

**Before external production dependence:** hardware-backed or multisig admin roles;
independent contract audit; external pipeline security review; incident-response
runbook; 99.9% SLO architecture; multi-region deployment; supply-chain controls;
reproducible deployment; upgrade/rollback policy; independent legal review; published
correction and challenge policy.

**Before multi-publisher operation:** admission criteria; conflict disclosures;
key-compromise procedure; quorum and disagreement semantics; suspension rules; appeals;
independent methodology governance; no token or slashing until real adversarial and
legal requirements justify them.

### Adopted from the incumbents (design audit, 2026-08-17/18)

Two rounds against Chainlink and RedStone. Round 1 audited their price-feed surface, which
was the wrong opponent; round 2 found the products in this category. Ordered by what each
closes, not by how large it sounds.

1. **Root / derivative publisher hierarchy.** From RedStone's TSSO, which co-signs
   single-source NAV with a cold root key for material change and a bounded hot key for
   routine updates. Closes routine-key compromise without giving the hot key authority over
   the control set. `touchstone/publish.py`, `touchstone/keyring.py`,
   `TouchstoneRegistry.sol`.
2. **Chained publication.** A report must commit to its predecessor's digest. Today
   `report.py` carries `sequence` and `correction_of` but no parent, and the registry
   enforces sequence monotonicity only — so an authorized publisher can place an unrelated
   report at the next sequence and nothing detects the splice. Sequence binds position; it
   does not bind content. Requires a registry struct change, so it lands in the next
   deployed version rather than being bolted onto the current one.
3. **Explicit `SOURCE_ERROR` publication.** The state exists in the transition model and has
   no runtime producer: a fetch failure ends the epoch instead. `touchstone/epoch.py`,
   `touchstone/schedule.py`, `touchstone/controls.py`.
4. **Scheduled republication of unchanged state.** TSSO re-signs a static value to keep it
   from reading as stale. Adopt the shape, not the numeric deviation threshold — a 0.1%
   band is meaningless over `CONFIRMED` / `UNVERIFIABLE` / `STALE` / `INCONSISTENT`.
5. **Bind the evidence span to the adapter field it justifies** (residual R-1). The span is
   presence-only today.

**Deferred, deliberately:** threshold co-signing. RedStone ship a quorum product with
`getUniqueSignersThreshold` and an onchain median, and still concluded that a valuation with
one authoritative source cannot be established by aggregation. Three signers reading the same
issuer API agree on the same wrong answer. Quorum earns its place when there are publishers
whose disagreement carries information, not before.

**Watched, not adoptable:** zkTLS (Chainlink DECO) is a public sandbox with no production
product. It would prove transport — that these bytes came from that server — which is
strictly stronger than the unauthenticated fetch this project records today. It would not
prove typed interpretation, predicate evaluation, approval binding, or abstention.

## Public-backing milestone arc (every item drafted, owner approves)

The owner's personal account carries the convictions; the project account carries
technical progress. Deliberate, not promotional.

1. **The commitment** (after Phase 0 gates + provisional brand clearance): the problem,
   the evidence-integrity principles, the decision to build publicly. Never name
   issuers as partners or imply endorsement.
2. **Evidence standard** (after control-language v0): one exact citation becoming a
   typed control, plus rejected examples. Message: *the hard part is not extracting a
   number — it is refusing to claim more than the source proves.*
3. **First X Layer enforcement** (after the testnet full loop): source artifact →
   signed report → registry transaction → AssetGate decision → explorer link.
4. **Living dossier** (Aug 18–20): the public dossier and the verified / not-verified
   distinction.
5. **Testnet release candidate** (Aug 21): hero demo, **testnet** addresses, open
   verification endpoint, limitations, the @XLayerOfficial mention. Separate approvals for
   post and submission. *(Amended 2026-08-16: mainnet is unscheduled and conditional on a
   proven testnet loop, so it cannot be a launch item on a fixed date.)*
6. **Reliability proof** (Aug 28): scheduled vs completed epochs, evidence changes,
   incidents and recovery, uptime. Observation counts are never presented as adoption.
7. **Snapshot report** (Sept 1): full judging-window history, signed archive, roadmap.
8. **External adoption** (only when real): the first protocol consuming Touchstone state —
   the milestone that proves infrastructure status.

## Ambition theater — banned phrasing and mechanics

Cut everywhere, permanently: "the Moody's/S&P of tokenized finance" (use
"machine-verification and admission layer"); "regulatory-grade" before formal legal and
audit work; stake/slashing before independent publishers and real dispute data; any
governance token; issuer-paid ratings (Touchstone does not rate, and payment never affects
status); asset-count vanity expansion; daily social posting as proof of life (the
heartbeat and signed epochs are the proof); guaranteed xStocks alignment (say "ready to
help if confirmed"); "every major RWA chain" before production consumers; automated
legal conclusions.

## Priority stack and never-cut list

Hackathon priority: 1) evidence integrity, 2) one flawless hero loop, 3)
contract-enforced consumption, 4) autonomous reliability, 5) public transparency, 6)
second/third asset coverage, 7) x402 and richer dossier interaction.
If time slips, cut in order: x402 rescans → third adapter → semantic-diff visualization
→ rich Q&A.
**Never cut:** exact citations, abstention, signed reports, the transparency log,
`AssetGate`, end-to-end surveillance, public incident visibility, key separation, the
Sept 1 operations plan.
