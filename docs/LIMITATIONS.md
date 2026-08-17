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
| Accepted controls | ≥6 | **8 — met.** Each is a candidate a model proposed from issuer bytes, bound by digest to the compilation that accepted it. Listed in `data/compilations/APPROVALS.json`. Two further candidates were declined, with reasons, and cannot be relabelled approved. |
| Assets documented | 3 | **3 — met.** USTB, USDY and FOBXX source manifests, with golden fixtures where retrieval was bounded. |
| Fully autonomous live adapters | ≥2 | **0 proven live — missed.** One adapter (USTB) is built, wired and tested end to end. It has never run against live sources, so the count of *live* adapters is zero. Counting it as one while saying it has never run live would be self-contradictory. USDY is blocked on unbounded retrieval (a single 260,431,605-byte archive). FOBXX is retained as a documented monthly contrast asset; no adapter ships. OUSG was cut rather than rushed into the second slot (`manifests/sources/ousg.json`, `phase_1_status.state: cut`). Phase 1 deliberately ships one vertical rather than two hurried ones. |
| Live consumer contract gating on state | 1 | **0 — missed.** `AssetGate` is written and tested. It is deployed only ephemerally inside the local end-to-end run (`scripts/e2e_local.py`). It has never existed on a persistent chain. Address: `not_deployed`. |
| Production canary epoch | 1 | **0 — missed.** Nothing has been published to any registry. The active testnet registry holds zero reports. The canary is prepared in `docs/CANARY-G1B.md` and is an owner gate that has not been run. |
| Claims span-cited and hash-bound | 100% | Met for every accepted control. |

The living dossier (PLAN-T9) is unbuilt. There is no public page, no
wallet-free UI, and no developer surface. T26 in the threat model is
still backlog for that reason.

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

There is no PDF or archive parser in Phase 1. Those paths were cut with
the USDY and FOBXX adapters.

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

Only the approval gate stops it. Approval is a field on the record, set
by whoever edits the control set. There is no approver identity, no
signature over the decision, and no four-eyes requirement. The
compiler's confidence value is supplied by the model itself, so it
cannot substitute for that gate. This is residual **R-9** (and threat
**T9**) in `docs/THREAT-MODEL.md`.

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
from one never touched. No approved USTB control currently declares
`minimum_row_age_business_days`. The retired hand-written set used two
business days; the compiler did not propose that field, and approval may
change only `approval_state` and `compilation_sha256`, so it cannot be
added to an approved control.

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
publisher. A single host runs a single daemon. The host is
`not_configured`. Supervisor units are `not_configured`. Detection and
recovery timings are proven in a local subprocess harness; they have
not been proven on production hardware, because there is no production
host yet.

Compromise detection does not exist. Nothing watches for a publication
from an unexpected publisher or a report signed by a retired key.

TLS is trusted without pinning (residual **R-7**). Evidence integrity in
transit rests on the platform certificate store.

Chain state is read from the same single JSON-RPC endpoint it is written
through (residual **R-12**). An endpoint that answers dishonestly can
report a publication that did not occur, or conceal one that did.

Time is taken from the host clock (residual **R-10**). The chain rejects
a future `observedAt`; that check is one-sided and delay-sensitive.

Source probes were run from a development machine (residual **R-8**).
Repeated retrieval from the eventual deployment host is unverified.

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
  to verification freshness. A permissive policy admits stale state. The
  gate has never been deployed to a persistent chain.
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
legal review; external contract and pipeline audits; API and SDKs; paid
rescans; a second autonomous adapter; FOBXX or OUSG adapters; PAXG;
design partners; formal specifications; multi-publisher quorum;
accreditation; staking or a token; institutional governance; HSM or
multisig custody; multi-region deployment.

The public dossier, a public verification endpoint, and any project URL
are `not_deployed`.
