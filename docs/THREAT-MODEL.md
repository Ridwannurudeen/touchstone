# Touchstone Threat Model — Phase 1

**Status:** Phase 1 (hackathon build). Written 2026-08-14; **revised 2026-08-17** after
PLAN-T7 and PLAN-T8 closed and PLAN-T10/T11 were cut. That revision existed to remove
deferrals pointing at items that had since closed *without* doing the work — a backlog label
naming a finished item reads as covered when it is not.

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
| B3 | **Model provider** (control compiler) | Proposing candidate controls only | A hostile model cannot itself change state: compilation only ever emits `proposed` records, and a candidate the deterministic evaluator cannot decide is refused at compilation. An approved control is bound to the compilation that accepted it, checked at report construction and repeated by the offline verifier from artifacts the bundle carries. What remains is that the approval decision itself is unattributed — see R-9 |
| B4 | **Parsing worker** | Normalising bytes into typed observations | Process isolation only, not a kernel sandbox — see R-3 |
| B5 | **Evidence store** | Retaining exact bytes and their order | Local filesystem trust; chain verification detects modification, not a privileged rewrite of both objects and index |
| B6 | **Ed25519 reporting key** | Authenticity of a report's content | A compromised key can sign false reports. A bundle verifies against the key it carries, so a consumer must decide which key it trusts out of band. Rollover is built (`touchstone/keyring.py`) and is additive: a superseded key stays published and trusted, and only the active key may sign or publish anew. Note the limit this creates — because a bundle carries its own key, a bundle signed by a **revoked** key still passes `verify_bundle` (`touchstone/verify.py:133`). Revocation is a manifest-level withdrawal of trust, not a cryptographic one, so a consumer who cares must consult the manifest. Custody is the open part — R-5 |
| B7 | **EVM publisher key** | Authority to write to the registry | A compromised key can publish authentic-looking state; the owner can revoke it onchain |
| B8 | **Deployer / owner key** | Contract deployment and publisher authorisation | Full control of the registry's publisher set |
| B9 | **Operations / backup identity** | Service continuity and archive integrity | Backup loss or forgery. Its address is a required manifest field as of PLAN-T6, so the publisher can be shown not to be running as it; that is an *address* separation only. Nothing yet verifies who actually funds the publisher or holds the archive, and archive integrity is now built (PLAN-T8: `touchstone/backup.py`), though nothing verifies **who** holds the archive |
| B10 | **Registry contract** (X Layer) | Immutable, ordered, append-only history | Chain reorganisation or a wrong-chain deployment; guarded by an immutable expected chain id compared on every report publication, including corrections (`contracts/contracts/TouchstoneRegistry.sol:239`); publisher-authorisation writes are not chain-checked |
| B11 | **Consumer contract** (`AssetGate`) | Enforcing its own freshness policy | A permissive policy admits stale state; the gate reacts to verification freshness, never to asset safety |
| B12 | **Public projection** (dossier, heartbeat) | Displaying only signed, verified data | Claim inflation in the UI — see T26 |
| B13 | **Viewer's browser** | Rendering | No wallet is required and no key material reaches the page |
| B14 | **Control approver / release authority** | Deciding which proposals become approved, evaluable controls | This is the gate the compiler's output must pass, so it is the point where a hostile or careless approval enters the system. Today approval is a field on the record (`approval_state`) set by whoever edits the control set; there is no separate approver identity, no signature over the approval, and no four-eyes requirement |
| B15 | **Host clock / time source** | Retrieval timestamps, freshness deadlines, confirmation windows | Every freshness and staleness decision and the 24h confirmation separation derive from it. The chain provides a partial check — the registry rejects an `observedAt` in the future against `block.timestamp` (`contracts/contracts/TouchstoneRegistry.sol:257`) and the gate measures age the same way — so a clock fast enough that `observedAt` is *still* ahead of chain time when the transaction executes is rejected. Publication delay masks a moderately fast clock, and offchain-only decisions are never checked. See R-10 |
| B16 | **JSON-RPC endpoint** | Reporting chain state honestly | Reads and writes go through one configured endpoint, so it can both accept a transaction and describe the resulting state. See R-12 |

Keys at B6, B7, B8 and B9 are **required to be four distinct identities**. **Amended
2026-08-15 by PLAN-T6:** the separation is now enforced rather than stated. A deployment
manifest must state all four role addresses and cannot declare a publisher that is also
the deployer or the operations address (`touchstone/deployment.py`); the publisher key must
derive exactly the declared publisher address; a run refuses to start if the reporting seed
and the publisher key are the same secret **when both are present on one host** — on a
split-host deployment neither can see the other, so that particular collision is an
operator responsibility rather than an enforced property; and preflight refuses to publish
if the registry's `owner()` is the publisher, or if the publisher belongs to a different
`publisherIdentity` lineage than the manifest declares. No unlocked-account path remains in
the *publishing* code on any network — deployment and owner-administration calls still use
an unlocked signer under Hardhat, which is an operator action rather than an unattended
one. What is *not* enforced is custody: see the revised R-5 and `docs/KEY-MANAGEMENT.md`.

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
| T4 | **Content-encoding confusion / decompression bomb** | **Implemented for the JSON path (PLAN-T5).** `Accept-Encoding: identity` is requested, every response is capped on the wire, and a non-identity `Content-Encoding` is now refused, so a compressed body cannot smuggle more expanded data than the cap allows. Archive and document expansion limits are unbuilt, and unneeded while no portfolio source is an archive or document after the **PLAN-T10** cut |
| T5 | **Oversized input** — memory exhaustion via a large response | **Implemented.** Per-source `max_bytes` enforced at read time and again after transport (`touchstone/sources.py:192`, `touchstone/sources.py:235`) |
| T6 | **Hostile JSON** — deep nesting, duplicate keys, float coercion, unexpected shape | **Implemented.** Depth cap (`touchstone/normalize/ustb.py:17`), magic-byte root check, exact-object field sets, decimal-as-text parsing, and duplicate row-date rejection. Numbers never round-trip through binary floats |
| T7 | **Hostile PDF** | **Not applicable in Phase 1.** No PDF path exists, and none is needed: **PLAN-T10's USDY adapter was cut on 2026-08-16** and PLAN-T11's FOBXX adapter was dropped, so no portfolio source is a PDF or archive. Page, expanded-text and timeout limits become required again the moment one is added |
| T8 | **Parser escape** — parsing code executes attacker-influenced logic in the main process | **Partially mitigated.** Normalisation runs in a spawned worker with a hard wall-clock timeout (`touchstone/normalize/ustb.py:265`, default 2.0s at `:18`), which bounds runaway parsing. It does **not** contain a genuinely compromised worker: the worker's result crosses back over a `multiprocessing` connection (`touchstone/normalize/ustb.py:295`), and the parent deserialises whatever arrives. See **R-3** |

### Compilation

| ID | Threat | Disposition |
|---|---|---|
| T9 | **Prompt injection** — instructions embedded in issuer evidence steer the compiler | **Partially mitigated, and the limit is now pinned by tests.** The model is given no tool surface at all: the request body carries only `model` and `messages` (`temperature` was removed when current models began rejecting it as deprecated), so there is no shell, network, wallet or contract capability for injected text to invoke. Impact is further bounded because output is only a *proposal* that must survive schema validation, an adapter/source binding check (`touchstone/compiler.py:379`) and explicit approval before evaluation. **This constrains impact; it does not prevent steering** — embedded instructions can still shape a schema-valid, byte-citable proposal. **Tested as of PLAN-T5:** evidence carrying explicit self-approval instructions is refused outright, because a candidate declaring any `approval_state` other than `proposed` is rejected; a fabricated citation is rejected; a control redirected to another adapter is rejected; and the request carries no tool schema for injected text to invoke. **The honest limit is also pinned:** a *well-formed* injected candidate — correct adapter, exact citation, `proposed`, maximum confidence — is ACCEPTED by the compiler, because nothing detects that a human never intended it. Only the approval gate stops it, and approval is unattributed (B14, R-9) |
| T10 | **False citation** — a proposed control cites evidence it did not come from | **Partially implemented — byte-presence only.** The cited span must occur byte-exactly in both the stored artifact and the excerpt shown to the model (`touchstone/compiler.py:325`). That proves the bytes are present; it does not prove uniqueness or that they denote the value the adapter consumed. The broader threat remains open under **R-1** |
| T11 | **Silent model substitution** — output attributed to a model that did not produce it | **Closed 2026-08-16.** The provider boundary returns the identity the service answered with, and provenance records `returned_model_name` beside `requested_model_name`, the resolved endpoint, the response id, and a digest over the whole response body — which is persisted in the artifact, so the digest is checkable rather than asserted. A response whose returned model differs from the requested one is refused, as is any finish reason other than `stop`. The identity is still the provider's own claim about itself: Touchstone can prove what the service *said* it used, not what it actually ran |
| T12 | **Overconfident acceptance** — an ambiguous document yields a confident control | **Partially implemented.** Below-threshold confidence abstains rather than accepting (`touchstone/compiler.py:340`), but `compiler_confidence` is **supplied by the model in its own proposal**, so a confidently wrong or hostile output can clear the threshold by asserting a high number. The remaining protection is that a control must be marked approved before it becomes evaluable — which is a string comparison on a field, not an attested human decision (B14, R-9, R-11) |

### Evaluation and state

| ID | Threat | Disposition |
|---|---|---|
| T13 | **Mutable evidence** — the source revises a value after it was observed | **Partially implemented.** Value controls observe only a row whose whole record is identical in two retained captures at least 24h apart; a row revised between them is skipped. This compares two instants only (`touchstone/evaluate.py:361`), so a row revised and restored between captures is indistinguishable from one never touched. Documented in `docs/CONTROL-LANGUAGE.md` and `SOURCE_AUDIT.md` |
| T14 | **Retrieval failure mistaken for issuer failure** | **Rule implemented; runtime path still not, after PLAN-T7 closed.** The transition rule is correct and tested: `SOURCE_ERROR` preserves the previous state while its evidence deadline holds and only becomes `STALE` after expiry (`touchstone/controls.py:308`) — **except** that a contradicted result is checked first (`touchstone/controls.py:306`), so a source error arriving alongside a genuine contradiction still yields `INCONSISTENT`. But **no runtime caller ever produces that event** — a fetch or normalisation failure propagates out of `run_ustb_epoch` and terminates the run instead of being recorded — though the failed slot now opens an incident and the schedule continues (T22, T23). **No open plan item owns the missing producer.** |
| T15 | **Missing or conflicting observations** | **Implemented within an epoch that completes.** Absent or unusable evidence yields `UNEVALUABLE`, which drives `UNVERIFIABLE` rather than a confident result; only a genuine predicate conflict yields `INCONSISTENT` (`touchstone/controls.py:262`). This covers evidence that is retrieved but unusable, not an epoch that fails to complete — see T14 |
| T16 | **Evidence-store tampering** | **Implemented for detection.** The index is a hash chain and every append re-verifies the full chain and re-hashes every referenced object (`touchstone/evidence.py:78`, `:88`). A privileged actor able to rewrite objects *and* recompute the whole chain is not defended against — **R-4** |

### Publication

| ID | Threat | Disposition |
|---|---|---|
| T17 | **Signature or key compromise** | **Partially implemented.** Reports are Ed25519-signed and offline-verifiable; the registry supports onchain publisher rotation with preserved historical attribution. Reporting-key rollover is **implemented** (`touchstone/keyring.py`): the outgoing key is superseded rather than dropped, so bundles it already signed stay verifiable, and revocation is a separate, later step that cannot leave a deployment unable to sign. Rollover under load is exercised in **PLAN-T12**. Hardware-backed or multisig custody is **not a Phase 1 item at all** — `ROADMAP.md` places it in the "before external production dependence" ladder; see **R-5** |
| T18 | **Sequence replay / gap** | **Implemented.** The registry enforces a monotonic per-asset sequence, and the publisher refuses a sequence already onchain or not exactly next (`touchstone/publish.py:381`) |
| T19 | **Duplicate publication after a crash** | **Implemented, revised 2026-08-15.** The transaction is signed *before* anything is journalled, and the journal records the exact signed bytes and nonce, so recovery re-sends those bytes rather than re-signing: identical nonce, identical hash, at most one publication however many attempts occur. This also fixed two defects in the first T6 implementation — a definite pre-broadcast refusal used to leave a journal entry claiming an unknown broadcast outcome, and a dropped transaction had no path back. The journalled hash is recomputed from its bytes on load, so an edited journal is refused. Real subprocess-restart coverage is **PLAN-T7/PLAN-T12** |
| T20 | **Chain or deployment mismatch** — publishing to the wrong chain or a wrong contract | **Partially implemented.** The registry stores an immutable expected chain id (`contracts/contracts/TouchstoneRegistry.sol:72`) and compares it on every report publication, including corrections (`contracts/contracts/TouchstoneRegistry.sol:239`); publisher-authorisation writes are not chain-checked. **Amended 2026-08-15:** client-side verification is now **implemented**. Before signing, the publisher compares the endpoint's own chain id, the runtime bytecode actually deployed at the registry address, and the chain id the registry was constructed with, against a committed deployment manifest, and refuses on any disagreement (`touchstone/publish.py`). Publisher **lineage** is verified too: `publisherIdentity` must match the manifest, so an owner who authorizes a replacement publisher directly instead of rotating — creating a second, unrelated lineage that reads as authorized — is refused |
| T21 | **RPC failure** | **Partially implemented.** No value is ever invented on failure, and an interrupted send is reconciled rather than resent (see T19). **Amended 2026-08-15:** endpoint pinning and client-side chain-id and runtime-bytecode verification are **implemented** (see T20), and a typed `PreflightFailed` is raised without signing. Not built: retry/backoff on a failed submission (**PLAN-T7**) |
| T27 | **Dishonest RPC endpoint** | **Not defended — residual R-12.** Every fact the publisher reconciles against — latest sequence, receipts, events, stored reports — is read back from the same single configured endpoint that it writes through |

### Operations and presentation

| ID | Threat | Disposition |
|---|---|---|
| T22 | **Source outage** | **Partially implemented (PLAN-T7 closed 2026-08-15).** A failed slot is recorded as an incident and the schedule continues (`touchstone/schedule.py:153`, `touchstone/incidents.py:164`). **The `SOURCE_ERROR` transition itself still has no runtime producer** — `grep` finds it only in `touchstone/controls.py`, so a fetch failure ends the epoch and opens an incident rather than yielding the `SOURCE_ERROR` result whose semantics T14 describes. That residual is **not owned by any open plan item**; it was left behind when T7 closed |
| T23 | **Scheduler stops on the first failure** | **Implemented (PLAN-T7).** A failed slot is caught and reported rather than swallowed, and the schedule continues (`touchstone/schedule.py:153`, with the reasoning recorded at `:342`). The recurring scheduler still does not overlap invocations |
| T24 | **Backup loss** | **Implemented (PLAN-T8).** Encrypted backup and restore exist as `touchstone/backup.py` with `scripts/backup_workspace.py` and `scripts/restore_workspace.py`. Restore is exercised, not merely written |
| T25 | **Incident deletion** — an embarrassing failure quietly disappears | **Implemented (PLAN-T7).** `touchstone/incidents.py` is a hash-chained JSON-lines log whose head **and entry count** are persisted separately, because truncation alone leaves a shorter chain that verifies (`:99`, `:222`). Recovery closes an incident with a new event (`:185`) rather than removing one, and hardlinked logs are refused (`:131`) |
| T26 | **UI claim inflation** — the public surface implies more than the evidence proves | **Partially implemented (PLAN-T9, 2026-08-18).** The public surface exists at touchstone.gudman.xyz. It states each asset's real status including the ones with no approved control, carries every limitation string from the signed report verbatim, and names `AssetGate` as not deployed wherever the contract appears. What remains backlog is the visual separation requirement below, which has not been independently reviewed. Requirements: "verified" and "not verified" visually separated, deterministic explanations drawn only from accepted graph data, explorer links absent rather than fabricated when undeployed, and no ordinary NAV movement described as a risk event |

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
| Magic-byte limits | Implemented for JSON. PDF/ZIP magic checks are unbuilt and unneeded after the **PLAN-T10** cut |
| Size limits | Implemented (`touchstone/sources.py:192`) |
| Decompression limits | **Implemented for the JSON path (PLAN-T5)** — a non-identity Content-Encoding is refused. Archive/document expansion limits are unbuilt, and unneeded while no portfolio source is an archive or document after the **PLAN-T10** cut |
| Page limits | Not applicable — no PDF source remains in Phase 1 after the PLAN-T10 cut. **No open item owns this**; it returns with the first document source |
| Time limits | Implemented and **proved (PLAN-T5)** — a worker that never returns is terminated by the wall-clock limit and raises a typed failure |
| Isolated parsing worker | Implemented, with **R-3** stated |
| Model id, prompt hash, compiler version, input hash, raw output recorded | Yes — prompt hash, compiler version, input hash, raw output and the full response body are recorded, with the provider's **returned** model identity beside the requested one and a mismatch refused (T11 closed) |

### 5.2 Immediate (hackathon) security ladder

| Requirement | Status |
|---|---|
| Separated deployer / publisher / Ed25519 / operations keys | **Partially implemented 2026-08-15 (PLAN-T6).** Precisely: **three** EVM role addresses — publisher, deployer, operations — are required manifest fields and must be pairwise distinct, and the loaded publisher key must derive the declared publisher, so that separation is enforced. The reporter is **Ed25519 and has no EVM address at all**, so it is not among them. (`publisher_identity_address` is publisher *lineage*, not a fourth role; it equals the publisher on a first authorization and is refused if it is the deployer or operations.) Reporter-versus-publisher **secret** separation is checked only where both variables are present on one host; a split-host deployment could reuse one secret undetected. Custody remains a residual: **R-5** |
| No secrets in code, logs, bundles or clients | Holds today; no secret is committed. The E2E now warns when a Hardhat log containing development keys cannot be removed |
| Onchain key rotation | Implemented in the registry, with preserved historical attribution and lineage |
| Monotonic sequences + replay protection | Implemented (registry + `touchstone/publish.py:381`) |
| Allowlisted adapters | Implemented — control adapter must match the source manifest (`touchstone/compiler.py:379`) |
| Sandboxed parsing | Process isolation only — **R-3** |
| Prompt-injection tests | **Implemented (PLAN-T5)** — five cases: self-approval, fabricated citation, adapter redirection, the absence of any tool surface, and the honest limit that a well-formed injected candidate is accepted as a proposal |
| Full model/prompt version recording | Partially — see the row above and T11/R-11 |
| Signed release manifests | Backlog: **PLAN-T13** |
| Daily backups + tested restore | **Implemented (PLAN-T8)** — `touchstone/backup.py`, `scripts/backup_workspace.py`, `scripts/restore_workspace.py` |
| Public status and incident history | Incident history **implemented** (PLAN-T7, `touchstone/incidents.py`); the **public** surface that exposes it is **PLAN-T9**, still open |

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
  nothing establishes that an older row is never revised later. **The approved control set
  declares no minimum row age at all** — the retired hand-written controls used two business
  days, derived from those same two captures, but the compiler did not propose it and
  approval may not add it, so confirmation is now the only safeguard on this path. The
  business-day count ignores exchange and bank holidays.
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
- **R-5 — Keys are separated but not custodied.** *Revised 2026-08-15 by PLAN-T6.* The
  three EVM role addresses are distinct and enforced wherever an EVM key is loaded or
  used; the Ed25519 reporter is separated by construction but its *secret* is only proved
  distinct from the publisher's where both sit on one host. So the original form of this
  residual is narrowed rather than closed. What remains is
  custody: both runtime keys are plain environment variables on their host, with no HSM,
  KMS, passphrase at rest, threshold or multisig. Anything that can read the process
  environment can publish. The mitigation is scope — the publisher key can only append
  reports, never revoke, rotate or rewrite — and recovery is the deployer calling
  `rotatePublisher`. The deployer key itself is a single key whose loss or theft is
  unrecoverable, because the registry has no owner-rotation path. Compromise *detection*
  does not exist: nothing watches for a publication from an unexpected publisher or a
  report signed by a retired key. **PLAN-T7 and PLAN-T8 have both closed without building it**, so
  no open item owns it; it is a standing residual. See
  `docs/KEY-MANAGEMENT.md`.
- **R-6 — Verification bundles carry no *evidence* artifacts and no chain state.** Narrowed
  2026-08-17: bundle v4 carries the compilation artifacts each control cites **and** the
  approval ledger, so provenance and the human approval decision are both checkable offline.
  What a bundle still holds is the signed report, its canonical bytes, the control records,
  the evidence **references** and the published key. An offline verifier can
  therefore confirm the signature, recompute the control-set and evidence roots, and check
  internal consistency — and nothing more. It contains no registry state, no transparency
  log and no evidence-store index, so it cannot confirm the report was published onchain,
  cannot verify the log's hash chain, and cannot replay normalisation or prove that a
  reported row occurs inside an artifact it does not carry. **Evidence remains digest-only**
  — that is the part v4 did not change, and the limitation the report's own caveats state.
- **R-7 — TLS is trusted without pinning.** Evidence integrity in transit rests on the
  platform certificate store.
- **R-9 — Control approval is unattributed.** Approval is a field on the control record set
  by whoever edits the control set (B14). There is no approver identity, no signature over
  the approval decision, and no separation between the person proposing a control and the
  person approving it. The compiler's confidence gate cannot substitute for this, because
  the confidence value is supplied by the model itself (T12).
- **R-11 — Compilation is bound to evaluation. Closed 2026-08-16.** These were two
  disconnected paths: compilation validated a candidate and emitted a `proposed` record,
  evaluation admitted any record whose `approval_state` read `approved`, and the
  `compiler_provenance_digests` a report carried were checked by both the builder and the
  offline verifier only as well-formed hexadecimal. Nothing required an evaluated control to
  have come from a compilation, to match one, or to be reachable from any digest. The
  compiler's validation was therefore advisory to whoever curated the control set.

  Three things closed it. **The binding exists:** `ControlRecord` carries
  `compilation_sha256`, part of `canonical_bytes()` and so of the control-set root a
  consumer contract pins. A proposal must carry none — the digest is over the artifact
  containing the proposal — and approval attaches it. **It is enforced:** report
  construction resolves each artifact, hashes it, finds the accepted candidate, and requires
  the approved record to differ only in `approval_state` and `compilation_sha256`, naming
  any other edited field. **It is independently checkable:** a v3 verification bundle
  carries the artifacts themselves, and the offline verifier resolves them in memory, hashes
  them against the digests they are filed under, requires the set to equal the report's
  provenance exactly, and repeats the binding — with no filesystem and no access to the
  publisher's ledger.

  The hand-written control set that predated this is retired. It cited real spans and
  evaluated correctly, but nothing had compiled it, so a report claiming compiler provenance
  for it claimed something untrue. The approved set is eight candidates a model proposed from
  the issuer's own bytes; two further accepted candidates were declined by a human, recorded
  with reasons in `data/compilations/APPROVALS.json`, and cannot be relabelled approved.

  **What remains is R-9, and it is the load-bearing gap now.** The approval decision itself
  is unattributed: the ledger records what was approved, when, and why a candidate was
  declined, but not *by whom*, and nothing signs the decision. "AI proposes, deterministic
  systems decide" now holds for the compile-to-evaluate path; who approves remains a
  question this system does not answer.

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
- **R-13 — Writing evidence is upstream of publishing, and the observer writes evidence.**
  The continuous observer (`scripts/run_observer.py`) is described elsewhere as the process
  that "can do least": it holds no key, imports no signer or publisher, and since 2026-08-18
  runs as its own Unix identity. That is all true and it is **not** the same as saying a
  compromise of it is bounded to public artifacts.

  It appends to the same evidence store the daily service reads, which is deliberate — that
  is how a qualifying confirmation predecessor comes to exist. But `retrieved_at` is supplied
  by the caller and only *format*-checked (`touchstone/evidence.py:432`); nothing binds it to
  the instant bytes actually arrived, and nothing binds the bytes to the issuer beyond the
  fetch policy at the moment of retrieval. So code running as the observer identity could
  append a fabricated payload with a backdated capture time, and the next epoch's
  `confirmation_capture` would select it as a qualifying predecessor
  (`touchstone/epoch.py:182`). A value control comparing the current row against that
  fabricated row would find them identical and report `SATISFIED` — and the asset could reach
  `CONFIRMED` on evidence that was never retrieved.

  **The observer therefore cannot publish, but it can determine what a publication concludes.**
  Any claim that separating it from the publisher bounds the damage should be read narrowly:
  it bounds *key* exposure, not *conclusion* integrity.

  Partial mitigations that exist today: the evidence index is a hash chain, so an insertion
  cannot be hidden after the fact; the bundle names the confirmation capture it evaluated
  against, so a reader can see which artifact was relied on; and the archive retains both.
  None of these prevent the write — they make it visible afterwards to someone who looks.

  A future item should constrain `retrieved_at` at the store boundary to the writer's own
  clock within a tolerance, which would remove *retroactive* fabrication and leave only the
  slower, more visible kind that has to wait out the confirmation interval in real time. It is
  not implemented, because tests and fixture paths construct histories at arbitrary instants
  and the constraint needs a considered exemption rather than a flag.

## 7. Explicitly out of scope for Phase 1

The Phase 3 formal threat model, independent smart-contract audit, external pipeline
security review, incident-response runbook, multi-region deployment, supply-chain controls
and reproducible builds are not part of this document and are not claimed. Multi-publisher
quorum, challenge mechanics and any staking or slashing design are Phase 4 and do not
exist.
