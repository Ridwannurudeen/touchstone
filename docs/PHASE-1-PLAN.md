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
| PLAN-T6 | Production-capable publisher and staged deployment path | L | **Done** (`016de1b`) |
| PLAN-T7 | Autonomous epoch operations and append-only incidents | L | **Done** (`2c2ae27`) |
| PLAN-T8 | Heartbeat, watchdog, alerts, gas runway, encrypted backup and restore | L | **Done** (`d71e9cb`) |
| PLAN-T9 | Wallet-free living dossier and developer surface | L | |
| PLAN-T10 | **Amended:** USTB autonomous daily adapter | L | **Done** (`e9df186`) — USDY was cut; see below |
| PLAN-T11 | FOBXX issuer/SEC contrast adapter | L | **Dropped from Phase 1** (2026-08-16) |
| PLAN-T12 | Release-candidate hardening matrix and CI | L | |
| PLAN-T13 | Release and owner-gate package | M | |

**The contract ABI freezes after PLAN-T6.** Later changes require a reproduced correctness
or security defect.

**Broken once, on 2026-08-16, under exactly that clause.** The T10 round-1 audit found —
and a reproduction confirmed — that a daemon restarted on a day it had already served
publishes a second signed report for that day: it derives the same epoch, reads the correct
next sequence, and the registry accepts it. Two valid reports about one day, and a consumer
reading the latest one sees whichever landed last. Both cheaper remedies were rejected on
review: durable local state is a projection rather than chain truth and is lost with the
workspace, and inferring the epoch from the latest report's URI fails because a correction
can become the latest report and hide the earlier duplicate. So `Report` gained
`bytes32 epochKey`, the registry gained `epochSequence[assetKey][epochKey]`, `publish`
refuses a non-zero entry with `EpochAlreadyPublished`, and a correction must carry the
epoch it corrects.

**Authorization, and how it was finally obtained.** The owner approved the ABI change on
2026-08-16 and the replacement registry **in principle**, keeping execution as a separate
decision. Execution authorization was granted on **2026-08-17**, naming the packet commit
`16ca3bd`, the packet blob sha256 `046ab170…70c1` and the ceiling `1000000000000000` wei.
**The replacement is deployed** at `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, block
38489602; see `docs/DEPLOYMENT-G1-EXECUTED.md`.

Worth recording how close that went wrong. A revision of the deployment packet declared the
digest-bound approval rule **waived**, on the strength of a general "approval for the
everything downstream work". Audit rejected it in one line — recording that a control was
bypassed does not discharge the control, and the executing agent is the one party who cannot
waive a rule that exists to constrain it. The packet then took **eight audit rounds**, each
finding exactly one substantive misstatement about what the code does; two of those were
introduced by the fixes for earlier rounds. The lesson is not that the process was slow: it is
that a document describing a deployment drifts from the deployment at roughly one defect per
revision, and only line-by-line reading against the script catches it.

The predecessor registry is **superseded and must not be published to** — it has no
`epochSequence`. A full log scan of every 100-block range from its deployment block to head
found one event, its own `PublisherAuthorized`, so no history was lost.

An earlier version of this paragraph claimed preflight would refuse it on the runtime
bytecode digest. **That was false**, and the audit caught it: preflight compares the
deployed code against the digest *the manifest itself records*, and that manifest records
the obsolete registry's own digest, so the two agree. What refuses it is a machine-readable
`deployment_state` on the manifest, checked in `main()` before any key is read. Prose in a
notes field refuses nothing.

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

**PLAN-T6 closed 2026-08-15 after seven audit rounds.** Worth recording what the rounds
cost, because the pattern repeated: three separate times a fix *relocated* a defect rather
than closing it — the retired-key rule moved from the CLI into a client that took its own
manifest; the journal was bound to a destination but not to an intent; and pinning
reconciliation to the current publisher address fixed false provenance while breaking every
publisher rotation. The final round failed on coverage alone: fixes verified by hand in a
shell and never committed as tests. The ABI is now frozen — and was reopened once, on
2026-08-16, for the epoch-uniqueness defect described above.

**PLAN-T7 closed 2026-08-15 after eighteen audit rounds**, against T6's seven. Two things
account for the difference, and both are worth carrying forward.

The first is that one defect class kept reappearing in new places: caller-owned data read
more than once, so validation and use reach different objects. It was found in the bundle,
the evaluator, the verifier, the report, the deployment manifest and the control record —
six modules, one mistake — and each fix taught the sweep where to look next rather than
ending it.

The second is that the verification instrument was wrong three times. `scripts/mutation_check.py`
was added when an audit round failed on the grounds that a claim of thirteen mutation runs
could not be reconstructed from a clean tree. Its first version counted any nonzero pytest
exit as a killed mutant; its second counted exit 1, which pytest also returns having
collected nothing; its third counted a setup error, where the test body never ran at all.
Each version reported full coverage it did not have. It now requires a JUnit report naming
a call-phase failure in that mutation's own target set, and it is tested like anything else.
It has since caught a redundant `tuple()`, two anchors stale after formatting, a mutation
that was a no-op on this platform, and a source file it had failed to restore byte for byte.

**PLAN-T7 — operations and incidents.** *Scope amended 2026-08-15 by audit direction.*
Do not rebuild T6's transaction state machine. Criterion 7 is complete at the publisher
layer and incomplete at the service layer: T7 persists the complete signed report, URI,
correction mode and scheduled timestamp, reconciles that durable operation before any new
fetch or sign, handles a crash after publisher finalization but before operations-state
cleanup, and proves it with a real subprocess restart. Criterion 8 needs an explicit
transient pre-broadcast error type — `PreflightFailed` currently mixes transport failure
with permanent chain, bytecode, authorization, lineage and gas failures — bounded
sleep-injected backoff for that type only and only while no pending journal exists, and
`exception_retry_configuration=None` on the HTTP provider.

That last one is verified against the installed `web3==7.16.0`: the default configuration is
active, retries five times, and its allowlist contains `eth_sendRawTransaction`. Those
retries are idempotent for this design — identical signed bytes produce an identical hash —
but they are a resend loop *outside* the reconciliation boundary, and a boundary with a
hidden bypass is not a boundary.

For criterion 5, the incident log is verified against a separately persisted expected head
and count: a self-contained hash chain cannot detect deletion of a complete final entry.

Restartable service with atomic per-asset state;
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

*Scope frozen 2026-08-15 by audit direction, deliberately narrow.* Five pieces and one
document: `heartbeat.py`, `watchdog.py`, `alerts.py`, `gas.py`, `backup.py`, and
`docs/OPERATIONS.md`. Health is **calculated at read time and never stored** — a heartbeat
records facts and an expiry, so a dead daemon cannot leave a green flag behind. Detection is
proved in a local subprocess harness that kills a daemon and starts its replacement; T13
owns the systemd units and T8 installs nothing. Alerting is one HTTPS webhook whose
credential never appears in a URL, body, exception, incident, argv or repr. Gas runway
divides a balance read at one confirmed block by the **maximum measured** cost from
successful receipts — never a fee estimate, never a ceiling — and yields `UNKNOWN` rather
than a fallback whenever any component is indeterminate.

Backup is where the T7 defect class returns, so the rule is explicit: the daemon holds the
workspace lock for its whole serving lifetime, so **a second process must never copy a live
workspace**. Scheduled backup runs cooperatively inside the daemon between mutations while
it already holds that lock; the standalone command acquires the same lock or refuses. That
is what prevents an archive holding a transparency log from one instant, an incident head
from another and an operation file from a third. `Workspace` also gains the evidence root,
so a live adapter cannot put irreplaceable evidence outside what is backed up. Restore
verifies chains, digests and signatures into a fresh staging directory and is activated by a
separate operator action; it never overwrites a live workspace and never signs or broadcasts.

**Explicitly out of scope**, and not to be reopened under audit: dashboards, status pages
and any browser surface (T9); CI and the release matrix (T12); systemd, nginx, TLS, domains
and any installation (T13); multi-region failover, leader election or distributed locks;
Prometheus, Grafana, OpenTelemetry or log aggregation; PagerDuty, Slack or Telegram; alert
routing frameworks; automatic gas top-up, treasury logic or price conversion; automatic
restore or rollback; HSM, KMS, multisig or secret managers; backup pruning, incremental or
deduplicated archives; cloud-storage SDKs; and any contract or ABI change.

**One unknown to close during implementation:** whether X Layer receipts expose a fee
component beyond `gas_used × effective_gas_price`. If they do the calculator must include
it; if it cannot be established, runway is `UNKNOWN`.

**PLAN-T8 closed 2026-08-16 after three audit rounds.** Two things are worth carrying.

The first round found that all five modules were built, tested and **wired to nothing** —
every one passed its own tests while an operator would have seen exactly what an absent
module looks like. When a module is done, check that something calls it.

The second is that the same design was wrong three times: a docstring asking callers to hold
the lock, then a `Lease` anyone could construct, then a `Held` carrying a path and a boolean
that a one-line forgery satisfied. Each version stated the requirement more formally without
establishing it. What finally worked was proof the caller cannot fabricate — a descriptor the
kernel granted, checked by file identity — and the accepted limitation is recorded in
`touchstone/locking.py` and `docs/OPERATIONS.md` rather than left implied. The director's
ruling was to stop there: a Python capability cannot be made unforgeable against
same-process code, and the remaining calendar belongs to the live vertical.

**Build order amended again 2026-08-16 by audit direction, and this supersedes the order
below.** Amended T10 is closed: USTB runs unattended, the registry enforces one report per
epoch, and every control is bound by digest to the compilation that proposed it. What
remains, in order:

1. Rewrite the committed plan to the real critical path *(this change)*.
2. Finish the replacement-deployment package — `deployment_state` emitted explicitly by the
   deploy script and required by the schema, plus frozen bytecode hash, roles, fee ceiling
   and abort criteria.
3. **Owner gate:** deploy the replacement registry to X Layer testnet.
4. Bundle v4 — carry and hash-bind the approval ledger, and re-run compilation validation
   rather than trusting an artifact's serialised outcomes. A hard canary prerequisite: a
   v3 bundle can verify a control the approval process rejected.
5. Canary release gate — the repository's first CI workflow and the full check matrix.
6. **Owner gate:** one live USTB testnet epoch. Publish whatever emerges, including
   `UNVERIFIABLE`.
7. **Owner gate (key rotation + paid call):** recompile the NAV controls so they carry the
   two-business-day minimum row age. Mandatory before an `AssetGate` is pinned to a control
   root, because deploying earlier would pin the obsolete one.
8. **Owner gate:** deploy and prove the live `AssetGate`.
9. T9 dossier, built from real canary data rather than fixtures.
10. T12/T13 release package.

**The deliberate cuts are final for Phase 1: no OUSG adapter, no FOBXX adapter, no second
autonomous adapter, and no Aug 21 mainnet canary.** One flawless USTB vertical, with the
two-adapter and production-canary metrics reported as unmet.

The superseded order that stood here:

The wiring came first because it was the real gap: `scripts/run_service.py` refused every
mode except `--resolve-only`, saying so honestly — "no live epoch adapter is wired yet". The
USTB pipeline was complete and the daemon could reconcile, but nothing drove an epoch on a
schedule, so the number of *autonomous* adapters was zero rather than one. **That is closed:
USTB now runs unattended.** It has still never run against live sources, so the adapter count
is one built-and-tested, zero proven live.

**USDY is cut.** Its retrieval is unbounded and no official bounded route has been found, so
further work on it buys nothing. **FOBXX is cut as an adapter and retained as a documented
monthly contrast asset**: the SEC EDGAR fixture is committed and honest, no live adapter is
shipped, and the manifest says so.

**OUSG is cut too, and the second-adapter metric is abandoned rather than chased.** This
paragraph previously recommended OUSG and set an Aug 17 promotion gate. That gate was never
run — the calendar went instead to the epoch-uniqueness defect, the compilation binding and
the provenance work, all of which were load-bearing for the one vertical that exists. Two
conservative OUSG controls would have taken the count to seven, but the count was never the
point: eight controls are already accepted, and the metric that is actually missed is a
*second live adapter*, which a rushed one would satisfy only nominally.

**Phase 1 therefore ships one flawless USTB vertical**, with the two-adapter and
production-canary metrics reported as unmet in `ROADMAP.md`. That is the honest fallback the
paragraph above already named, taken deliberately rather than by running out of time.

**PLAN-T9 — living dossier.** Wallet-free public pages showing state, freshness, accepted
controls, evidence excerpts and hashes, transition timeline, incident history, source
health and a verifier-bundle download; "what was verified" and "what was not" visually
separate; a deterministic "why this state?" drawn only from accepted graph data, not
open-ended Q&A; explorer links absent rather than fabricated when nothing is deployed;
desktop and mobile browser tests.

**PLAN-T10 — USDY adapter. CUT (2026-08-16); the item was reused for the USTB adapter,
which is done.** Everything below is the record of why USDY could not be built, retained
because a cut asset should say what blocked it. None of the scope described here is
outstanding work.

**BLOCKED as written (2026-08-15).** Retrieval is not bounded: the
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

**PLAN-T11 — FOBXX adapter. DROPPED FROM PHASE 1 (2026-08-16).** FOBXX is retained as a
documented monthly contrast asset — its N-MFP3 fixture is committed and its manifest records
the daily feed's Cloudflare 403 — but no adapter ships. Everything below is the narrowed
scope as it stood before the cut, kept as the record of what was considered. None of it is
outstanding work.

**NARROWED (2026-08-15).** The daily issuer feed returns Cloudflare
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
