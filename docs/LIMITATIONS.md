# Limitations

**Unpublished text in a file.** This is not a legal opinion, not an audit
report, not a safety case, and not an independent attestation. It is a
list of things Touchstone does not do, written before anyone has to ask.

The project's hardest standard is that a document must not claim more than
the code does. Where a fact has not been verified against this repository,
it is written `not_configured`, `not_deployed`, or stated as unknown.

Nothing here asserts that any observed asset is safe, solvent, compliant,
or suitable.

Day-to-day operations, including the live testnet registry, are
`docs/OPERATIONS.md`. The security residuals this file summarises are
sourced from `docs/THREAT-MODEL.md`. Completion metrics are sourced from
`ROADMAP.md`.

---

## 1. What Touchstone is trusted to say

Touchstone claims one class of thing: that it retrieved exact bytes from a
first-party endpoint reached from an allowlisted URL at the recorded
retrieval time, that a stated control was evaluated against those bytes
deterministically, and that the result was signed and — if a publisher
was authorised to — published.

It does not claim the issuer is honest. It does not claim the published
figures are accurate. It does not claim an asset is sound.

The retrieval time is Touchstone's own. It is supplied by the caller or
read from the local clock (`touchstone/sources.py`). It is not an
authenticated publication timestamp from the issuer. No source in the
portfolio provides one.

---

## 2. Hackathon completion metrics — what was missed

The targets in `ROADMAP.md` were not restated downward. Misses are
recorded as misses. Standing in that table is dated 2026-08-16; the
testnet registry landed the next day and does not change the three
missed rows.

| Metric | Target | Actual |
|---|---|---|
| Accepted controls | ≥6 | **5 — missed.** The 0.3.0 recompile (2026-08-17, live retrieval) proposed ten candidates that passed the deterministic gates. Five were approved and five declined, with reasons, in `data/compilations/APPROVALS.json`. The five declines were not forced: three are duplicate presence checks on a document another approved control already reads, and one clears the 0.80 confidence gate with zero margin. Approving any of them would have met this target and would have counted duplication as breadth, so the number stands at five and the row reads missed. Each approved control is bound by digest to the compilation that accepted it, and a declined candidate cannot be relabelled approved. |
| Assets documented | 3 | **3 — met.** USTB, USDY and FOBXX source manifests, with golden fixtures where retrieval was bounded. |
| Fully autonomous live adapters | ≥2 | **1 — missed.** USTB ran the unattended daemon against the live issuer on 2026-08-17 and published sequence 1, so the count is one, not zero. This row read "0 proven live... it has never run against live sources" until 2026-08-18, which the canary row two lines below had already contradicted. The production observer sustains a 15-minute retrieval schedule for all three USTB sources, and since 2026-08-20 the enabled publisher unit has run the mainnet slot unattended — one day of it; recovery on the production host remains unproven. Publication count is no longer the gap: 14 reports across two chains, 9 `CONFIRMED`. The miss is the asset count. USDY is blocked on unbounded retrieval (a single 260,431,605-byte archive). FOBXX now has a strict offline SEC normalizer and fixtures, but no live epoch or production-host adapter run. OUSG was cut rather than rushed into the second slot (`manifests/sources/ousg.json`, `phase_1_status.state: cut`). Phase 1 deliberately ships one vertical rather than two hurried ones. |
| Live consumer contract gating on state | 1 | **1 — met on testnet, 2026-08-18.** `AssetGate` at `0xAac48DC261B04737FDCB101D5049395121034a83` on X Layer testnet, block 38602126, pointed at the live registry. `check()` on USTB returns **`(false, "status not allowed")`** — the gate refuses the asset, because the latest report is `UNVERIFIABLE` and the mask admits `CONFIRMED` only. That refusal is the point rather than a failure: a gate widened until it returned `allowed` would be a consumer contract accepting an unverified asset. The deployment script now requires a nonzero `requiredControlSetRoot`; the historical gate was deployed before that pinning guard and remains a legacy testnet artifact. Publisher lineage and freshness are still enforced. On mainnet the reasoning that kept a gate off — an immutable pin against a still-moving control set — expired when the owner signed the approval release: `AssetGateV2` at `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` (2026-08-20, block 68427105) pins policy id, policy root, control-set root and the signed approval-ledger digest, and returned `(true, "allowed")` for the pinned policy key on deployment day. |
| Production canary epoch | 1 | **1 — met on mainnet, 2026-08-18.** USTB sequence 1 on X Layer **mainnet** (chain 196), registry `0xc9d58e44…D30d` — note this address is a *superseded* registry on chain 1952, so only the chain id identifies it — epoch `ustb-2026-08-18`, observed 14:04:21Z, state `UNVERIFIABLE`. It was later restated by sequence 2, a correction; mainnet now holds two reports and testnet three. The testnet canary of 2026-08-17 (block 38526525) stands as its own record. Both abstained, and for the same reason: a fresh workspace has no capture from ≥24h earlier, so the NAV value controls cannot confirm a row and the engine refuses to assert one. This row previously read "unmet for mainnet, which remains unscheduled". |
| Claims span-cited and hash-bound | 100% | Met for every accepted control. |

The living dossier (PLAN-T9) shipped 2026-08-18 and is live at
https://touchstone.gudman.xyz — 26 routes, an offline verifier, a live
Policy Terminal at `/app` (the one page with JavaScript: the vendored
ethers bundle, no external hosts), a coverage page, and this
documentation rendered from the repository. This paragraph previously
read "there is no public page", and was being served *from* the public
page. A project whose whole claim is that it does not assert more than
the evidence supports cannot leave a page telling its reader that page
does not exist.

PLAN-T12 (release-candidate matrix) is not marked done in
`docs/PHASE-1-PLAN.md`. A CI workflow exists at
`.github/workflows/ci.yml`. Whether every case that document named has
been added is not claimed here.

---

## 3. Parser isolation is process isolation

Normalisation of USTB payloads runs in a spawned worker
(`touchstone/normalize/ustb.py`, `multiprocessing.get_context("spawn")`)
with a hard wall-clock timeout (`DEFAULT_ISOLATED_TIMEOUT = 2.0`
seconds). A worker that never returns is terminated and, if it ignores
that, killed.

That bounds a misbehaving parser and a runaway one. It is **not** a
kernel sandbox. There is no seccomp, container, namespace, or capability
restriction. The worker retains the privileges of the service account.

The result crosses back over a `multiprocessing` connection
(`receive.recv()`). The parent deserialises whatever arrives. A worker
that an adversary has genuinely compromised can act on the parent across
that channel. Closing this would need a restricted transport carrying
only plain data, plus OS-level confinement. That is residual **R-3** in
`docs/THREAT-MODEL.md`.

There is no PDF or unbounded archive parser in Phase 1. USDY's archive path remains cut.
FOBXX's retained SEC N-MFP3 XML path is bounded by source bytes, XML depth and unsafe-marker
checks, but it is not a live production adapter.

---

## 4. Steering by injected evidence is a retained residual

The compiler is given no tool surface. The request body carries only
`model` and `messages` (`touchstone/compiler.py`, `HTTPProvider`). There
is no shell, network, wallet, or contract capability for injected text
to invoke.

A candidate declaring any `approval_state` other than `proposed` is
refused. A fabricated citation is refused. A control redirected to
another adapter is refused.

This constrains impact. It does not prevent steering. A well-formed
injected candidate — correct adapter, exact citation, `proposed`,
maximum confidence — is accepted as a proposal, because nothing detects
that a human never intended it. That limit is pinned by tests
(`tests/test_compiler.py`).

Only the approval gate stops it. Since 2026-08-19 the live approval
ledger is version 2: every entry, declined entries included, carries the
approver's EIP-712 signature over the decision, reason, timestamp,
control digest and compilation digest; validation refuses any unsigned
entry, recovers the named approver, and binds each signed decision to
the exact compiler proposal so a signature cannot be repurposed for a
different control. Reports published earlier commit by digest to the
version-1 unsigned ledger and their bundles verify unchanged forever —
those decisions stay readable but unattributed, because signing them
now would manufacture history. There is still no four-eyes requirement:
one person proposes, operates and approves. The compiler's confidence
value is supplied by the model itself, so it cannot substitute for that
gate. The role separation is the remaining part of residual **R-9**
(and threat **T9**).

The serving runtime does not call a model. `scripts/compile_controls.py`
is the only place a model is invoked; it runs at proposal time, on the
operator's machine, and does not approve anything.

---

## 5. Value controls abstain without a qualifying earlier capture

A value control on the USTB NAV source observes only a row whose whole
normalised record is identical in a qualifying earlier capture.
"Qualifying" means retrieved at least
`CONFIRMATION_INTERVAL_SECONDS = 86_400` earlier
(`touchstone/evidence.py`). Two captures taken minutes apart, including
either side of midnight, never confirm each other.

`run_ustb_epoch` resolves that predecessor **before** appending this
epoch's own capture, so a fetch can never confirm itself
(`touchstone/epoch.py`). If there is no qualifying predecessor,
`_confirmed_nav_row` returns nothing, the control evaluates
`UNEVALUABLE`, and — while evidence is still fresh — the asset state is
`UNVERIFIABLE` (`touchstone/controls.py`, `touchstone/evaluate.py`).

An unseeded workspace therefore reports `UNVERIFIABLE`. That is the
honest result, not a defect. The canary packet accepts it.

The confirmation window is empirical, not proven (residual **R-2**). A
row revised and restored between the two captures is indistinguishable
from one never touched. One approved USTB control now declares
`minimum_row_age_business_days`: `ustb-nav-per-share-present`, at two
business days. Earlier sets carried none — the compiler did not propose
the field, and approval may change only `approval_state` and
`compilation_sha256`, so it could not be added afterwards. The 0.3.0
compiler prompt requests it and the evaluator accepts it only on the NAV
source and never for `fresh_within`. Note the deterministic gate
validates a non-negative integer, not the constant two: this candidate
declares two because the model proposed two, not because the compiler
could produce nothing else.

Presence controls on the yield and holdings sources do not use this
window. They prove only that the issuer returned a named scalar in these
hash-bound bytes.

---

## 6. Keys, hosts, and what is absent

There is no HSM, no KMS, no passphrase at rest, no multisig, and no
threshold signing. Runtime keys are environment variables on their host.
Anything that can read the process environment can publish. The
publisher key can only append reports; it cannot revoke, rotate, or
rewrite. Recovery is the deployer calling `rotatePublisher`. The
deployer key itself is a single key. The registry has no owner-rotation
path, so loss or theft of the deployer is unrecoverable. See
`docs/KEY-MANAGEMENT.md` and residual **R-5**.

There is no multi-region failover, no leader election, and no second
publisher. The shared VPS runs `touchstone-observer@xlayer-mainnet`, the
status timer, and — since 2026-08-20 — the enabled publisher unit, which
has produced exactly one unattended mainnet publication day (its first
slot failed closed on a parse timeout; the corrected next slot
published). The observer is recording all three USTB sources every 15
minutes. Detection and recovery timings are proven in a local subprocess
harness; restart and recovery on the production host itself remain
undemonstrated, and one unattended day is not a measured window.

Compromise detection does not exist. Nothing watches for a publication
from an unexpected publisher or a report signed by a retired key.

TLS is trusted without pinning (residual **R-7**). Evidence integrity in
transit rests on the platform certificate store.

The publisher supports fail-closed reads from two independent HTTPS RPC
hosts and refuses disagreement or partial availability. Production
configured `TOUCHSTONE_RPC_QUORUM` in the publisher unit's environment
when the unit was enabled on 2026-08-20 (verified on the host), and the
Registry v2 publication path refuses to run without it. Residual
**R-12** is operationally closed for the publishing path; reads made by
other tools still use single endpoints.

Time is taken from the host clock (residual **R-10**). The chain rejects
a future `observedAt`; that check is one-sided and delay-sensitive.

The production observer now retrieves all three USTB sources from the
shared VPS every 15 minutes. Cross-region retrieval and a second host are
still unverified (residual **R-8**).

---

## 7. Evidence, bundles, and citations

The evidence index is a hash chain. Every append re-verifies the chain
and re-hashes referenced objects. That detects modification. An actor
with write access who rewrites objects and recomputes the entire chain
is not detected. The transparency log is another local JSON-lines file
with the same trust properties. The only genuinely external record is
what has been published on chain, and that is a root, not the evidence.
Residual **R-4**.

A verification bundle carries the signed report, control records,
evidence **references**, compilation artifacts, the approval ledger, and
the published key. It does not carry evidence bytes, registry state, or
the transparency log. An offline verifier can confirm the signature,
recompute roots, and repeat the compilation-to-control binding. It
cannot confirm the report was published, cannot verify the log, and
cannot replay normalisation against artifacts it does not hold.
Residual **R-6**.

Byte-span provenance proves the cited bytes occur in the artifact. It
does not prove uniqueness, or that they denote the field the adapter
consumed. Residual **R-1**.

---

## 8. Retrieval failures, incidents, and scheduling

A source outage is not asset inconsistency. The transition rule
preserves the previous state under `SOURCE_ERROR` until the evidence
deadline expires. **No runtime caller currently produces that event.**
A fetch or normalisation failure leaves `run_ustb_epoch` and the failed
slot opens an incident instead. That residual is not owned by any open
plan item (`docs/THREAT-MODEL.md` T14, T22).

A missed slot is recorded and never backfilled. Running yesterday's
slot today retrieves today's evidence and files it under yesterday.

Alerts are one HTTPS webhook. Delivery is not guaranteed. There is no
retry-until-success, no paging escalation, and no failover.

Gas runway is `UNKNOWN` whenever any operand is missing, and unknown
fails the gate. Top-up is manual.

---

## 9. Claims this project does not make

These are not residuals to be closed later. They are outside what
Touchstone is.

- **Legal.** `docs/BRAND-CLEARANCE.md` is a record of searches. It is
  not a clearance opinion. `legal_status` is `not_assessed` for every
  entry. Superstate API terms of use are recorded `unresolved` in
  `manifests/sources/ustb.json`.
- **Solvency, reserves, collateral.** An issuer API is issuer
  disclosure. USDY's third-party attestation, if it were retrieved, would
  still cover Ondo USDY LLC only and would still not be an audit. It is
  not retrieved.
- **Compliance, suitability, credit.** Touchstone does not rate. It does
  not recommend. It does not decide listings.
- **Safety of an asset or of a consumer protocol.** `AssetGate` reacts
  to verification freshness. A permissive policy admits stale state. Gates are live on
  both chains — the original testnet gate at
  `0xAac48DC261B04737FDCB101D5049395121034a83` (which refused USTB for two days and has
  admitted it since the first CONFIRMED state on 2026-08-19) and policy-pinned gates on
  testnet and mainnet — but every deployed consumer is this project's own; no third
  party consumes any of them yet.
- **Independent attestation, audit, or formal verification of
  Touchstone itself.** The Phase 3 formal threat model, independent
  contract audit, and external pipeline review are not claimed. This
  document is not a substitute.
- **Production reliability.** Until an epoch is produced and published
  without a person present, every reliability figure in
  `docs/OPERATIONS.md` is a property of the tests.
- **Endorsement.** Nothing here implies OKX, X Layer, Superstate, Ondo,
  Franklin Templeton, or any issuer endorses this project.

---

## 10. Explicitly out of scope for Phase 1

From `docs/PHASE-1-PLAN.md` and `ROADMAP.md` Phases 2–5, and not claimed:
legal review; external contract and pipeline audits; a public verification API; paid
rescans; a second autonomous live adapter; a production FOBXX or OUSG route; PAXG;
design partners; formal specifications; multi-publisher quorum;
accreditation; staking or a token; institutional governance; HSM or
multisig custody; multi-region deployment.

The public dossier is live at https://touchstone.gudman.xyz. A public
verification endpoint is still `not_deployed` — verification is offline, by
running the verifier against a bundle.
