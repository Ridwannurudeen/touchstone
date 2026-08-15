# Touchstone Threat Model — Phase 1

**Status:** Phase 1 (hackathon build). Written 2026-08-14 against `main`.

This document states what Touchstone defends against today, what it does not, and where
each roadmap security requirement actually stands. It is deliberately narrow: the formal
threat model, the independent contract audit, and the external pipeline review are Phase 3
deliverables and are **not** claimed here.

Every control below is cited to the code that implements it. Where a requirement is not
implemented, it is recorded as a backlog item or a residual limitation rather than
described as covered. Nothing here asserts that any observed asset is safe, solvent,
compliant, or suitable, and nothing here claims an independent attestation, audit, or
legal opinion that Touchstone has not obtained.

## 1. What Touchstone is trusted to say

Touchstone claims exactly one class of thing: *that it retrieved exact bytes from a
first-party endpoint reached from an allowlisted URL at the recorded retrieval time, that a stated control was
evaluated against those bytes deterministically, and that the result was signed and
published.* It does not claim the issuer is honest, that the published figures are
accurate, or that an asset is sound.

Note the retrieval time is Touchstone's own: it is supplied by the caller or read from the
local clock (`touchstone/sources.py:216`). It is not an authenticated publication timestamp
from the issuer, and no source in the portfolio provides one.

A consumer relying on Touchstone inherits the trust assumptions in section 2. Those
assumptions are the product, so they are enumerated rather than minimised.

## 2. Trust boundaries

| # | Boundary | Trusted for | If it fails |
|---|---|---|---|
| B1 | **Source** (issuer endpoint) | Publishing its own figures | Touchstone reports what the issuer published; it cannot detect issuer dishonesty |
| B2 | **Network path to the source** | Transport integrity via TLS | A successful TLS MITM could substitute evidence; pinning is not implemented |
| B3 | **Model provider** (control compiler) | Proposing candidate controls only | A hostile model cannot itself change state: compilation only ever emits `proposed` records. But the evaluator's admission rule is the `approval_state` field alone (`touchstone/evaluate.py:201`), and it is **not bound to a compiler record** — see R-11 |
| B4 | **Parsing worker** | Normalising bytes into typed observations | Process isolation only, not a kernel sandbox — see R-3 |
| B5 | **Evidence store** | Retaining exact bytes and their order | Local filesystem trust; chain verification detects modification, not a privileged rewrite of both objects and index |
| B6 | **Ed25519 reporting key** | Authenticity of a report's content | A compromised key can sign false reports. A bundle verifies against the key it carries, so a consumer must decide which key it trusts out of band; reporting-key rollover is not built (R-5) |
| B7 | **EVM publisher key** | Authority to write to the registry | A compromised key can publish authentic-looking state; the owner can revoke it onchain |
| B8 | **Deployer / owner key** | Contract deployment and publisher authorisation | Full control of the registry's publisher set |
| B9 | **Operations / backup identity** | Service continuity and archive integrity | Backup loss or forgery; separated from publishing keys by policy — see PLAN-T6/PLAN-T8 |
| B10 | **Registry contract** (X Layer) | Immutable, ordered, append-only history | Chain reorganisation or a wrong-chain deployment; guarded by an immutable expected chain id compared on every report publication, including corrections (`contracts/contracts/TouchstoneRegistry.sol:239`); publisher-authorisation writes are not chain-checked |
| B11 | **Consumer contract** (`AssetGate`) | Enforcing its own freshness policy | A permissive policy admits stale state; the gate reacts to verification freshness, never to asset safety |
| B12 | **Public projection** (dossier, heartbeat) | Displaying only signed, verified data | Claim inflation in the UI — see T26 |
| B13 | **Viewer's browser** | Rendering | No wallet is required and no key material reaches the page |
| B14 | **Control approver / release authority** | Deciding which proposals become approved, evaluable controls | This is the gate the compiler's output must pass, so it is the point where a hostile or careless approval enters the system. Today approval is a field on the record (`approval_state`) set by whoever edits the control set; there is no separate approver identity, no signature over the approval, and no four-eyes requirement |
| B15 | **Host clock / time source** | Retrieval timestamps, freshness deadlines, confirmation windows | Every freshness and staleness decision and the 24h confirmation separation derive from it. The chain provides a partial check — the registry rejects an `observedAt` in the future against `block.timestamp` (`contracts/contracts/TouchstoneRegistry.sol:257`) and the gate measures age the same way — so a clock fast enough that `observedAt` is *still* ahead of chain time when the transaction executes is rejected. Publication delay masks a moderately fast clock, and offchain-only decisions are never checked. See R-10 |
| B16 | **JSON-RPC endpoint** | Reporting chain state honestly | Reads and writes go through one configured endpoint, so it can both accept a transaction and describe the resulting state. See R-12 |

Keys at B6, B7, B8 and B9 are **required to be four distinct identities**. Today the local
publisher path uses an unlocked development account, so that separation exists as stated
policy only — the identities, manifests and templates are not in the repository yet. That is PLAN-T6, tracked as R-5.

## 3. Threats and current disposition

Legend: **Implemented** (control exists and is tested) · **Backlog** (a named item in
`docs/PHASE-1-PLAN.md` that will implement it) · **Residual** (accepted, documented, not
mitigated in Phase 1). Backlog items are written `PLAN-Tn` to keep them distinct from the
threat identifiers `T1`–`T27` used in this section.

### Evidence acquisition

| ID | Threat | Disposition |
|---|---|---|
| T1 | **Source impersonation / wrong endpoint** — evidence fetched from an address that is not the approved source | **Implemented (PLAN-T5).** The initial URL must match the manifest exactly, every URL must be HTTPS with no embedded credentials and no non-443 port, and a redirect may now only land on a URL the allowlist itself names, so retrieval cannot end anywhere the manifest did not approve |
| T2 | **Redirect abuse** — an open or cross-host redirect moves retrieval to attacker-controlled bytes | **Implemented (PLAN-T5).** At most one redirect is followed; cross-host and non-HTTPS redirects are refused; and the target must itself be allowlisted, so a same-host open redirect on an approved host no longer moves retrieval off-manifest. A test drives that exact case |
| T3 | **MIME confusion** — a source returns a different content type than the manifest approved | **Implemented (PLAN-T5).** The received media type is compared against the manifest's `expected_mime` and a mismatch is refused before anything is stored; a missing `Content-Type` is refused outright. Parameters such as `charset` are ignored for the comparison |
| T4 | **Content-encoding confusion / decompression bomb** | **Implemented for the JSON path (PLAN-T5).** `Accept-Encoding: identity` is requested, every response is capped on the wire, and a non-identity `Content-Encoding` is now refused, so a compressed body cannot smuggle more expanded data than the cap allows. Archive and document expansion limits remain **PLAN-T10** |
| T5 | **Oversized input** — memory exhaustion via a large response | **Implemented.** Per-source `max_bytes` enforced at read time and again after transport (`touchstone/sources.py:192`, `touchstone/sources.py:235`) |
| T6 | **Hostile JSON** — deep nesting, duplicate keys, float coercion, unexpected shape | **Implemented.** Depth cap (`touchstone/normalize/ustb.py:17`), magic-byte root check, exact-object field sets, decimal-as-text parsing, and duplicate row-date rejection. Numbers never round-trip through binary floats |
| T7 | **Hostile PDF** | **Backlog: PLAN-T10.** No PDF path exists yet; page, expanded-text and timeout limits are required before USDY lands |
| T8 | **Parser escape** — parsing code executes attacker-influenced logic in the main process | **Partially mitigated.** Normalisation runs in a spawned worker with a hard wall-clock timeout (`touchstone/normalize/ustb.py:265`, default 2.0s at `:18`), which bounds runaway parsing. It does **not** contain a genuinely compromised worker: the worker's result crosses back over a `multiprocessing` connection (`touchstone/normalize/ustb.py:295`), and the parent deserialises whatever arrives. See **R-3** |

### Compilation

| ID | Threat | Disposition |
|---|---|---|
| T9 | **Prompt injection** — instructions embedded in issuer evidence steer the compiler | **Partially mitigated, and the limit is now pinned by tests.** The model is given no tool surface at all: the request body carries only `model`, `messages` and `temperature` (`touchstone/compiler.py:114`), so there is no shell, network, wallet or contract capability for injected text to invoke. Impact is further bounded because output is only a *proposal* that must survive schema validation, an adapter/source binding check (`touchstone/compiler.py:379`) and explicit approval before evaluation. **This constrains impact; it does not prevent steering** — embedded instructions can still shape a schema-valid, byte-citable proposal. **Tested as of PLAN-T5:** evidence carrying explicit self-approval instructions is refused outright, because a candidate declaring any `approval_state` other than `proposed` is rejected; a fabricated citation is rejected; a control redirected to another adapter is rejected; and the request carries no tool schema for injected text to invoke. **The honest limit is also pinned:** a *well-formed* injected candidate — correct adapter, exact citation, `proposed`, maximum confidence — is ACCEPTED by the compiler, because nothing detects that a human never intended it. Only the approval gate stops it, and approval is unattributed (B14, R-9) |
| T10 | **False citation** — a proposed control cites evidence it did not come from | **Partially implemented — byte-presence only.** The cited span must occur byte-exactly in both the stored artifact and the excerpt shown to the model (`touchstone/compiler.py:325`). That proves the bytes are present; it does not prove uniqueness or that they denote the value the adapter consumed. The broader threat remains open under **R-1** |
| T11 | **Silent model substitution** — output attributed to a model that did not produce it | **Not implemented — residual R-11.** Prompt hash, compiler version, input hash and the exact raw output are recorded, but the recorded model id is the *requested* one read from configuration (`touchstone/compiler.py:90`), and the response parser reads only the message content and never the provider's returned model identity (`touchstone/compiler.py:157`). A substituted model is therefore attributed to the configured name. The actual model remains provider-attested and untested by Touchstone |
| T12 | **Overconfident acceptance** — an ambiguous document yields a confident control | **Partially implemented.** Below-threshold confidence abstains rather than accepting (`touchstone/compiler.py:340`), but `compiler_confidence` is **supplied by the model in its own proposal**, so a confidently wrong or hostile output can clear the threshold by asserting a high number. The remaining protection is that a control must be marked approved before it becomes evaluable — which is a string comparison on a field, not an attested human decision (B14, R-9, R-11) |

### Evaluation and state

| ID | Threat | Disposition |
|---|---|---|
| T13 | **Mutable evidence** — the source revises a value after it was observed | **Partially implemented.** Value controls observe only a row whose whole record is identical in two retained captures at least 24h apart; a row revised between them is skipped. This compares two instants only (`touchstone/evaluate.py:361`), so a row revised and restored between captures is indistinguishable from one never touched. Documented in `docs/CONTROL-LANGUAGE.md` and `SOURCE_AUDIT.md` |
| T14 | **Retrieval failure mistaken for issuer failure** | **Rule implemented; runtime path not.** The transition rule is correct and tested: `SOURCE_ERROR` preserves the previous state while its evidence deadline holds and only becomes `STALE` after expiry (`touchstone/controls.py:282`) — **except** that a contradicted result is checked first (`touchstone/controls.py:280`), so a source error arriving alongside a genuine contradiction still yields `INCONSISTENT`. But **no runtime caller ever produces that event** — a fetch or normalisation failure propagates out of the epoch (`touchstone/epoch.py:187`) and terminates the run instead of being recorded. Backlog: **PLAN-T7** |
| T15 | **Missing or conflicting observations** | **Implemented within an epoch that completes.** Absent or unusable evidence yields `UNEVALUABLE`, which drives `UNVERIFIABLE` rather than a confident result; only a genuine predicate conflict yields `INCONSISTENT` (`touchstone/controls.py:262`). This covers evidence that is retrieved but unusable, not an epoch that fails to complete — see T14 |
| T16 | **Evidence-store tampering** | **Implemented for detection.** The index is a hash chain and every append re-verifies the full chain and re-hashes every referenced object (`touchstone/evidence.py:78`, `:88`). A privileged actor able to rewrite objects *and* recompute the whole chain is not defended against — **R-4** |

### Publication

| ID | Threat | Disposition |
|---|---|---|
| T17 | **Signature or key compromise** | **Partially implemented.** Reports are Ed25519-signed and offline-verifiable; the registry supports onchain publisher rotation with preserved historical attribution. Reporting-key rollover is built in **PLAN-T6**, which now names it explicitly, and exercised in **PLAN-T12**. Hardware-backed or multisig custody is **not a Phase 1 item at all** — `ROADMAP.md` places it in the "before external production dependence" ladder; see **R-5** |
| T18 | **Sequence replay / gap** | **Implemented.** The registry enforces a monotonic per-asset sequence, and the publisher refuses a sequence already onchain or not exactly next (`touchstone/publish.py:381`) |
| T19 | **Duplicate publication after a crash** | **Implemented.** A persisted pending journal plus onchain reconciliation resolves an interrupted send instead of resending it (`touchstone/publish.py:372`). Real subprocess-restart coverage is **PLAN-T7/PLAN-T12** |
| T20 | **Chain or deployment mismatch** — publishing to the wrong chain or a wrong contract | **Partially implemented.** The registry stores an immutable expected chain id (`contracts/contracts/TouchstoneRegistry.sol:72`) and compares it on every report publication, including corrections (`contracts/contracts/TouchstoneRegistry.sol:239`); publisher-authorisation writes are not chain-checked. Manifest-pinned RPC, chain id, address and runtime-bytecode verification on the client side are **PLAN-T6** |
| T21 | **RPC failure** | **Partially implemented.** No value is ever invented on failure, and an interrupted send is reconciled rather than resent (see T19). Not built: endpoint pinning and client-side chain-id and runtime-bytecode verification (**PLAN-T6**), and retry/backoff on a failed submission (**PLAN-T7**) |
| T27 | **Dishonest RPC endpoint** | **Not defended — residual R-12.** Every fact the publisher reconciles against — latest sequence, receipts, events, stored reports — is read back from the same single configured endpoint that it writes through |

### Operations and presentation

| ID | Threat | Disposition |
|---|---|---|
| T22 | **Source outage** | **Backlog: PLAN-T7.** The semantics exist (`SOURCE_ERROR`); the service that records an incident and keeps scheduling does not |
| T23 | **Scheduler stops on the first failure** | **Backlog: PLAN-T7.** The scheduler is recurring and does not overlap invocations (`touchstone/schedule.py:37`). Its real limitation is that an exception from the job propagates and ends the loop, so one failed epoch stops all future ones |
| T24 | **Backup loss** | **Backlog: PLAN-T8.** No backup or restore path exists |
| T25 | **Incident deletion** — an embarrassing failure quietly disappears | **Backlog: PLAN-T7.** Incident history must be append-only and hash-chained; recovery closes an incident with a new event rather than removing it. Not built |
| T26 | **UI claim inflation** — the public surface implies more than the evidence proves | **Backlog: PLAN-T9.** No public surface exists yet. Requirements: "verified" and "not verified" visually separated, deterministic explanations drawn only from accepted graph data, explorer links absent rather than fabricated when undeployed, and no ordinary NAV movement described as a risk event |

## 4. State semantics that this model depends on

These distinctions are load-bearing and must not be collapsed:

- **`SOURCE_ERROR` is an operational event, not an asset judgement.** Touchstone failing to
  retrieve evidence says nothing about the issuer. Absent a contradicted result it preserves
  the last evidence-derived state until that evidence's own deadline expires; a contradiction
  is evaluated first (`touchstone/controls.py:280`) and still yields `INCONSISTENT`.
- **`STALE` means the evidence deadline passed**, not that anything is wrong with the asset.
- **`INCONSISTENT` requires a genuine predicate conflict** between attributable
  observations — never a retrieval failure, never a missing field, never an ordinary value
  movement.
- **`UNVERIFIABLE` is a success mode.** Abstention is preferred over a confident claim; a
  first epoch with no confirming capture reports `UNVERIFIABLE` by design.

## 5. Roadmap requirement traceability

### 5.1 Aug 16 security requirements

| Requirement | Status |
|---|---|
| All documents treated as adversarial input | Implemented — exact-object parsing, typed conversion, no evaluation of document content |
| Model gets no shell, network, wallet or contract tools | **Implemented** — no tool surface in the request (`touchstone/compiler.py:114`) |
| Instructions embedded in evidence are never followed | Partially — impact is bounded by tool denial and deterministic gates and the limit is pinned by five tests, but **steering is not prevented**: a well-formed injected candidate is accepted as a proposal (T9, R-9) |
| Allowlisted URLs | **Implemented (PLAN-T5)** — the initial URL must match the manifest, and a redirect may only land on that source's own URL or a `redirect_alias` it declares (T1, T2) |
| Blocked redirects | **Implemented (PLAN-T5)** — at most one hop, same host, HTTPS, and the target must be the source's own URL or a `redirect_alias` it declares; a redirect to a *different* approved source is refused |
| MIME limits | **Implemented (PLAN-T5)** — enforced against the manifest before storage |
| Magic-byte limits | Implemented for JSON; PDF/ZIP with **PLAN-T10** |
| Size limits | Implemented (`touchstone/sources.py:192`) |
| Decompression limits | **Implemented for the JSON path (PLAN-T5)** — a non-identity Content-Encoding is refused. Archive/document expansion limits remain **PLAN-T10** |
| Page limits | Not applicable until PDFs land. Backlog: **PLAN-T10** |
| Time limits | Implemented and **proved (PLAN-T5)** — a worker that never returns is terminated by the wall-clock limit and raises a typed failure |
| Isolated parsing worker | Implemented, with **R-3** stated |
| Model id, prompt hash, compiler version, input hash, raw output recorded | Partially — prompt hash, compiler version, input hash and raw output are recorded; the **requested** model id is recorded rather than the provider's returned identity (T11, R-11) |

### 5.2 Immediate (hackathon) security ladder

| Requirement | Status |
|---|---|
| Separated deployer / publisher / Ed25519 / operations keys | **Not yet** — stated policy only; no distinct identities or manifest templates exist in the repository. Backlog: **PLAN-T6**, residual **R-5** |
| No secrets in code, logs, bundles or clients | Holds today; no secret is committed. The E2E now warns when a Hardhat log containing development keys cannot be removed |
| Onchain key rotation | Implemented in the registry, with preserved historical attribution and lineage |
| Monotonic sequences + replay protection | Implemented (registry + `touchstone/publish.py:381`) |
| Allowlisted adapters | Implemented — control adapter must match the source manifest (`touchstone/compiler.py:379`) |
| Sandboxed parsing | Process isolation only — **R-3** |
| Prompt-injection tests | **Implemented (PLAN-T5)** — five cases: self-approval, fabricated citation, adapter redirection, the absence of any tool surface, and the honest limit that a well-formed injected candidate is accepted as a proposal |
| Full model/prompt version recording | Partially — see the row above and T11/R-11 |
| Signed release manifests | Backlog: **PLAN-T13** |
| Daily backups + tested restore | Backlog: **PLAN-T8** |
| Public status and incident history | Backlog: **PLAN-T7/PLAN-T8/PLAN-T9** |

## 6. Residual limitations

These are accepted for Phase 1 and stated publicly rather than mitigated.

- **R-1 — Byte-span provenance is not structurally bound to adapter output.** Control
  Language v0 stores `evidence_span` as an observation-specific raw substring. Compilation
  proves only that those bytes occur in the artifact and in the provider excerpt
  (`touchstone/compiler.py:325`); source/adapter compatibility is validated separately
  (`touchstone/compiler.py:379`). It does not prove the occurrence is unique, nor that it
  denotes the field and typed value the deterministic adapter actually consumed. Duplicate
  or decoy occurrences, formatting changes, and ordinary observation rollover can therefore
  produce ambiguous provenance or unnecessary control-identity churn. Strict source schemas
  and adapter allowlisting reduce evaluation risk but do not provide structural evidence
  binding. **A dedicated future item** must add an adapter-bound structural locator
  (preferably a JSON Pointer against an adapter-defined logical record) requiring exactly
  one resolution of the expected type, agreement between the resolved value/date and the
  adapter's typed observation, and storage of the resolved pointer, raw span, value, date
  and evidence digest in *epoch provenance* rather than durable control identity — with
  compiler, evaluator and verifier agreement and offline re-resolution.
- **R-2 — The confirmation window is empirical, not proven.** Cross-capture confirmation
  shows only that a row was identical *at* two capture instants (`touchstone/evaluate.py:361`);
  a row revised and restored between them is indistinguishable from one never touched, and
  nothing establishes that an older row is never revised later. The two-business-day minimum age is derived from two captures
  and the business-day count ignores exchange and bank holidays.
- **R-3 — Parser isolation bounds runtime, not compromise.** Normalisation runs in a
  spawned worker with a wall-clock timeout. There is no seccomp, container, namespace or
  capability restriction, and the worker retains the privileges of the service account.
  Critically, the worker's result is returned to the parent over a `multiprocessing`
  connection (`touchstone/normalize/ustb.py:295`) whose receive path deserialises the
  object the child sent. A worker that an adversary has genuinely compromised can therefore
  act on the parent across that channel; the process boundary limits a *misbehaving* parser
  and a runaway one, not a *controlled* one. Closing this requires a restricted transport
  carrying only plain data, plus OS-level confinement.
- **R-4 — Evidence-store integrity is detection, not prevention, and nothing anchors it
  externally.** The hash-chained index detects modification of stored artifacts or entries.
  An actor with write access who rewrites objects and recomputes the entire chain is not
  detected. The transparency log is not an independent check on this: it is another local
  JSON-lines file (`touchstone/translog.py:42`) with the same trust properties, and while
  it can produce signed checkpoints, those are only returned to the caller — there is no
  publication, distribution or external pinning path for them. The only genuinely external
  record is what has been published onchain, and that is a root, not the evidence.
- **R-5 — Key separation is not yet enforced in a runtime path.** The local publisher uses
  an unlocked development account. Distinct deployer, publisher, reporter and operations
  identities are required and specified but arrive with PLAN-T6; no hardware or multisig custody
  exists in Phase 1.
- **R-6 — Verification bundles carry digests, not artifacts, and no chain state.** A bundle
  holds the signed report, its canonical bytes, the control records, the evidence
  references and the published key (`touchstone/verify.py:38`). An offline verifier can
  therefore confirm the signature, recompute the control-set and evidence roots, and check
  internal consistency — and nothing more. It contains no registry state, no transparency
  log and no evidence-store index, so it cannot confirm the report was published onchain,
  cannot verify the log's hash chain, and cannot replay normalisation or prove that a
  reported row occurs inside an artifact it does not carry.
- **R-7 — TLS is trusted without pinning.** Evidence integrity in transit rests on the
  platform certificate store.
- **R-9 — Control approval is unattributed.** Approval is a field on the control record set
  by whoever edits the control set (B14). There is no approver identity, no signature over
  the approval decision, and no separation between the person proposing a control and the
  person approving it. The compiler's confidence gate cannot substitute for this, because
  the confidence value is supplied by the model itself (T12).
- **R-11 — Compilation is not bound to evaluation.** These are two disconnected paths.
  Compilation validates a candidate's span, adapter binding and confidence, and emits a
  `proposed` record. Evaluation admits any `ControlRecord` whose `approval_state` reads
  `approved` (`touchstone/evaluate.py:201`); nothing requires that record to have come from
  a compilation, to match one, or to be reachable from any provenance digest. A report
  carries `compiler_provenance_digests`, but the report builder and the offline verifier
  check those only as well-formed digests — they are never resolved back to a compilation
  that produced the evaluated control. Consequently the compiler's validation is advisory
  to whoever curates the control set rather than a precondition of evaluation, and the
  "AI proposes, deterministic systems decide" separation rests entirely on that curator
  (B14, R-9). Also unresolved: the recorded model identity is the requested one, not the
  provider's returned one (T11), so provenance cannot attest which model proposed a
  control.

  **Closed in part by PLAN-T5:** the offline verifier now refuses a bundle whose controls
  are not `approved`, so an independent verifier no longer accepts one carrying proposals.
  What remains is the binding itself — report construction still matches controls to
  evaluations by `control_id` alone (`touchstone/report.py:161`), and nothing ties an
  approved control to the compilation that produced it.

  Of the three changes needed, the offline verifier's rejection of controls whose
  `approval_state` is not `approved` is **done (PLAN-T5)**. The other two remain
  unscheduled: (a) bind an approved control to the specific compilation record that
  produced it and have the verifier check that binding; and (b) separately, record the
  model identity the provider returned rather than the one requested
  (`touchstone/compiler.py:152`), without which provenance cannot attest which model
  proposed a control at all. The PLAN-T5 check closes neither the binding gap nor T11;
  remedy (a) does not close T11, and remedy (b) does not close the binding gap.
- **R-10 — Time is taken from the host clock, with only a one-sided chain check.**
  Retrieval timestamps, freshness deadlines and the 24-hour confirmation separation all
  derive from the local clock (B15). The chain catches one direction: the registry rejects
  a future `observedAt` relative to `block.timestamp`
  (`contracts/contracts/TouchstoneRegistry.sol:257`), and `AssetGate` measures observation
  age against the same chain time. That check is one-sided and delay-sensitive: it rejects
  only an `observedAt` still ahead of chain time when the transaction executes, so
  publication delay masks a moderately fast clock. Source-asserted dates are not ignored
  either — freshness requires `observed_on <= now` (`touchstone/evaluate.py:332`) and value
  rows dated after `now` cannot be selected (`touchstone/evaluate.py:361`) — so a slow clock
  does not silently accept a future-dated row; it excludes that row and falls back to an
  older qualifying one, or abstains when none exists. What is missing is two-sided
  validation, any cross-check for purely offchain decisions, and a monotonic guard against
  the clock moving backwards between epochs.
- **R-12 — Chain state is read from the endpoint it is written through.** The publisher
  reconciles against latest sequence, receipts, events and stored reports supplied by the
  same single configured JSON-RPC endpoint used to submit transactions (B16). An endpoint
  that answers dishonestly can report a publication that did not occur, or conceal one that
  did, and every client-side consistency check would still agree. Endpoint pinning does not
  address this; independent confirmation would require a second, independently operated
  endpoint or a light-client proof. Neither exists in Phase 1.
- **R-8 — Source probes were run from a development machine.** Repeated retrieval from the
  eventual deployment host is unverified and remains parked behind the deployment gate.

## 7. Explicitly out of scope for Phase 1

The Phase 3 formal threat model, independent smart-contract audit, external pipeline
security review, incident-response runbook, multi-region deployment, supply-chain controls
and reproducible builds are not part of this document and are not claimed. Multi-publisher
quorum, challenge mechanics and any staking or slashing design are Phase 4 and do not
exist.
