# Phase 1 execution plan

**Issued 2026-08-14** by the project's review process, which sets the build order and
audits each item before the next begins. Items are referenced elsewhere in the repository
as `PLAN-T1` … `PLAN-T13`, distinct from the `T1` … `T27` threat identifiers in
`docs/THREAT-MODEL.md`.

Order follows `ROADMAP.md`'s own priority stack: evidence integrity, then one flawless hero
loop, then contract-enforced consumption, then autonomous reliability, then public
transparency, then second and third asset coverage. **An item begins only after the
previous one passes audit.**

| ID | Goal | Size | Status |
|---|---|---|---|
| PLAN-T1 | Make the managed hero E2E deterministic and non-skipping | S | **Done** (`2ddd23d`) |
| PLAN-T2 | Close the Phase 0 threat-model deliverable | M | **Done** (`d659b02`) |
| PLAN-T3 | Complete brand-clearance research | M | **Done** (`1135c25`) |
| PLAN-T4 | Source manifests and golden fixtures for USTB, USDY, FOBXX | M | **Done** (`d96f944`) |
| PLAN-T5 | Hero evidence security and the authoritative USTB oracle check | L | **Done** (`5c73edf`) |
| PLAN-T6 | Production-capable publisher and staged deployment path | L | In progress |
| PLAN-T7 | Autonomous epoch operations and append-only incidents | L | |
| PLAN-T8 | Heartbeat, watchdog, alerts, gas runway, encrypted backup and restore | L | |
| PLAN-T9 | Wallet-free living dossier and developer surface | L | |
| PLAN-T10 | USDY autonomous daily adapter | L | |
| PLAN-T11 | FOBXX issuer/SEC contrast adapter | L | |
| PLAN-T12 | Release-candidate hardening matrix and CI | L | |
| PLAN-T13 | Release and owner-gate package | M | |

**The contract ABI freezes after PLAN-T6.** Later changes require a reproduced correctness
or security defect.

## Item detail

**PLAN-T1 — deterministic hero E2E.** Pin the local chain clock to the committed capture's
retrieval instant; the test starts and stops its own node; remove the environmental
self-skip. *Done.*

**PLAN-T2 — threat model.** `docs/THREAT-MODEL.md`: trust boundaries, threats across
acquisition, compilation, evaluation, publication and operations, and a trace from every
`ROADMAP.md` Aug-16 security requirement and immediate security-ladder item to an
implemented control, a backlog item, or a stated residual. Must state that parser isolation
is process isolation, not a kernel-enforced sandbox, and must not invent legal, solvency,
compliance, safety or independent-attestation claims.

**PLAN-T3 — brand clearance.** `docs/BRAND-CLEARANCE.md`: dated searches with result links
across trademark registries, package registries, token trackers, explorers, social handles,
app stores, ENS and registrars; phonetic and visual similarity; the Touchstone Investments
adjacency recorded separately; "no result found" distinguished from legal clearance; a
counsel question set. **Registers and purchases nothing.**

**PLAN-T4 — manifests and fixtures.** Machine-readable `manifests/sources/*.json` recording
identity, authority class, legal entity, method, URL, expected MIME and magic bytes,
cadence, timezone, grace period, size/page/decompression limits, retention, failure
semantics, asset identity and fixture hashes; retained fixtures for every source that can
be retrieved within bounds; a read-only bounded `scripts/probe_sources.py`.

**Amended 2026-08-15 by verified negative findings.** Two fixtures this item originally
required cannot be obtained. The USDY attestation is reachable only inside a 260 MB archive,
and pulling it was ruled against because a one-time download would not make daily retrieval
bounded. The FOBXX daily feed returns Cloudflare 403. Both are recorded in their manifests
with the exact blocker and the follow-up, and both move to the item that owns the adapter,
contingent on the blocker clearing. The N-MFP3 fixture was retrieved and is committed.

**PLAN-T5 — evidence security and oracle check.** Enforce MIME against the manifest; reject
non-identity `Content-Encoding`; make redirects fail closed unless the final URL is itself
allowlisted; prove a hung parsing worker is terminated by the wall-clock limit; prove
embedded instructions cannot self-approve a control or move it into evaluation — **amended 2026-08-15**: steering is explicitly retained as a residual risk, because a well-formed injected candidate is accepted as a proposal and only the approval gate stops it (threat model T9, R-9); add `touchstone/oracles.py`
pinning a block, verifying chain/address/decimals, and comparing only against a confirmed
row of the matching date within an explicit tolerance. The offline verifier must also
reject a bundle whose controls are not `approved`; the compilation-to-control binding of
R-11 is not closed by that check and remains unscheduled until an item names it.

**PLAN-T6 — production publisher.** Locally signed raw transactions with no unlocked remote
account; RPC URL, chain id, registry address, runtime bytecode hash, publisher lineage and
confirmation depth from a validated deployment manifest; preflight verification before
signing; distinct deployer, publisher, reporter and operations identities; deployment
scripts and manifest templates; Ed25519 reporting-key rollover that keeps existing bundles
verifiable while selecting the new key for future reports; `docs/KEY-MANAGEMENT.md`.
**Sends no testnet or mainnet transaction.**

**PLAN-T7 — operations and incidents.** Restartable service with atomic per-asset state;
append-only hash-chained incident history where recovery closes an incident with a new
event rather than deleting it; `SOURCE_ERROR` recorded and the previous state preserved
until its deadline; a failed epoch never ends future scheduling; restart reconciliation
before any resend; bounded retry with backoff on a failed submission; missed slots
recorded, never backfilled with invented timestamps.

**PLAN-T8 — reliability layer.** Heartbeat that cannot stay green after daemon death;
watchdog detection within five minutes and recovery within fifteen; alerting without
logging its secret; gas runway from real balance and measured costs; authenticated
encrypted backups; restore that verifies hashes, chains and signatures before activation;
`docs/OPERATIONS.md` with the Aug 21 – Sept 1 schedule and incident policy.

**PLAN-T9 — living dossier.** Wallet-free public pages showing state, freshness, accepted
controls, evidence excerpts and hashes, transition timeline, incident history, source
health and a verifier-bundle download; "what was verified" and "what was not" visually
separate; a deterministic "why this state?" drawn only from accepted graph data, not
open-ended Q&A; explorer links absent rather than fabricated when nothing is deployed;
desktop and mobile browser tests.

**PLAN-T10 — USDY adapter. BLOCKED as written (2026-08-15).** Retrieval is not bounded: the
archive is served only as a single 260,431,605-byte zip, the `subpath` parameter is ignored,
the folder page carries no embedded listing, and the unauthenticated listing API returns 404.
This item cannot begin until a bounded mechanism is found and verified, or until the second
daily slot is filled by another asset — OUSG is qualified as a candidate but not promoted.
The scope below stands only once that is resolved. Re-scrape the official page each run for
the current link;
retrieve only the bounded current-year archive; enforce archive entry count, compressed and
expanded size, path traversal, PDF magic, page count, extracted-text size and parsing
timeout in an isolated worker; select attestations by report date, never by upload
metadata; evaluate collateralisation; implement the documented publication lag; state that
the attestation covers Ondo USDY LLC only.

**PLAN-T11 — FOBXX adapter. NARROWED (2026-08-15).** The daily issuer feed returns Cloudflare
403 from the development environment, so this item covers the **monthly SEC path only**:
filing discovery, N-MFP3 freshness, the NAV peg and liquidity floors as read from the filing,
with liquidity taken from its dated rows rather than by position. Daily-liveness and
issuer-versus-regulator reconciliation are restored only if repeatable access from the
production host is verified. The original daily scope, retained for when that happens: exact
allowlisted request bodies for the issuer endpoints, no
arbitrary query construction; official EDGAR endpoints with a compliant identifying user
agent; daily feed liveness, NAV peg, liquidity floors when present, monthly filing
freshness and issuer-versus-regulator reconciliation; blank liquidity is `UNEVALUABLE`,
never a breach; omit the seven-day yield because it was never verified; honest degradation
to the monthly source.

**PLAN-T12 — release-candidate matrix.** The repository's first CI, running Python tests,
lint, contract tests, the managed local-chain E2E and browser smoke tests with no silent
skip; new integration coverage for restart with a pending transaction, duplicate-publication
recovery, full source outage and recovery, publisher rotation, reporting-key rollover,
encrypted backup and restore, rejection of a bundle carrying non-approved controls,
clean-browser dossier smoke, and the full two-act hero demo.

**PLAN-T13 — release and gate package.** Release builder recording exact commit, artifact
hashes, compiler settings, runtime bytecode hashes, dependency locks, schema versions,
control roots, fixture hashes and test summaries; deployment, rollback, limitations, demo
runbook and submission drafts; milestone post drafts left unpublished; manifest fields
requiring real addresses left explicitly `not_deployed`.

## Owner-gated actions

These are prepared up to the gate and **not executed**. Each requires explicit owner
approval, per `ROADMAP.md` product principle 8.

| ID | Gate | Prepared, not executed |
|---|---|---|
| G1 | X Layer testnet deployment | Audited network config, local dry-run output, bytecode hashes, deployment script, role manifest, funding estimate, rollback commands |
| G2 | VPS / site deployment | Release archive, service units, web server config, backup timer, alert template, rollback procedure, local smoke results |
| G3 | Mainnet deployment and canary | Tested release commit, signed release manifest, mainnet manifest template, role separation plan, funding requirement, canary payload and abort criteria |
| G4 | Domains, handles, accounts, posts | Brand-clearance report, availability table, ranked name recommendation, all milestone post drafts |
| G5 | Hackathon submission | Form draft, release archive, signed manifest, AI usage statement, demo runbook, claim-to-evidence checklist |
| G6 | Trademark / legal action | Brand-collision evidence and counsel question set |
| G7 | Live operations through Sept 1 | Service configuration, schedules, incident policy, backups, restore procedure, gas plan, watchdog, daily runbook |

## Out of scope

`ROADMAP.md` Phases 2–5 (Sept 2026 onward) are not part of this plan: legal review,
external contract and pipeline audits, API and SDKs, paid rescans, asset expansion beyond
the three selected, design partners, formal specifications, independent audit, external
publisher sandbox, multi-publisher quorum, accreditation, staking or token systems, and
institutional governance. PAXG remains parked pending a stable attributable report
interface. FOBXX onchain supply aggregation stays outside Phase 1.
