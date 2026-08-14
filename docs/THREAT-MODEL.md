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

Touchstone claims exactly one class of thing: *that it retrieved exact bytes from an
allowlisted first-party endpoint at the recorded retrieval time, that a stated control was
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
| B3 | **Model provider** (control compiler) | Proposing candidate controls only | A hostile or compromised model cannot change state — proposals pass deterministic validation and an approval gate before any evaluation |
| B4 | **Parsing worker** | Normalising bytes into typed observations | Process isolation only, not a kernel sandbox — see R-3 |
| B5 | **Evidence store** | Retaining exact bytes and their order | Local filesystem trust; chain verification detects modification, not a privileged rewrite of both objects and index |
| B6 | **Ed25519 reporting key** | Authenticity of a report's content | A compromised key can sign false reports. A bundle verifies against the key it carries, so a consumer must decide which key it trusts out of band; reporting-key rollover is not built (R-5) |
| B7 | **EVM publisher key** | Authority to write to the registry | A compromised key can publish authentic-looking state; the owner can revoke it onchain |
| B8 | **Deployer / owner key** | Contract deployment and publisher authorisation | Full control of the registry's publisher set |
| B9 | **Operations / backup identity** | Service continuity and archive integrity | Backup loss or forgery; separated from publishing keys by policy — see PLAN-T6/PLAN-T8 |
| B10 | **Registry contract** (X Layer) | Immutable, ordered, append-only history | Chain reorganisation or a wrong-chain deployment; guarded by an immutable expected chain id |
| B11 | **Consumer contract** (`AssetGate`) | Enforcing its own freshness policy | A permissive policy admits stale state; the gate reacts to verification freshness, never to asset safety |
| B12 | **Public projection** (dossier, heartbeat) | Displaying only signed, verified data | Claim inflation in the UI — see T26 |
| B13 | **Viewer's browser** | Rendering | No wallet is required and no key material reaches the page |
| B14 | **Control approver / release authority** | Deciding which proposals become approved, evaluable controls | This is the gate the compiler's output must pass, so it is the point where a hostile or careless approval enters the system. Today approval is a field on the record (`approval_state`) set by whoever edits the control set; there is no separate approver identity, no signature over the approval, and no four-eyes requirement |
| B15 | **Host clock / time source** | Retrieval timestamps, freshness deadlines, confirmation windows | Every freshness and staleness decision, the 24h confirmation separation, and the gate's age check depend on it. A wrong or manipulated host clock can make stale evidence look fresh or suppress a valid observation. No trusted time source, monotonic guard or clock-skew check exists |

Keys at B6, B7, B8 and B9 are **required to be four distinct identities**. Today the local
publisher path uses an unlocked development account, so that separation exists as policy
and templates but is not yet enforced in a production path. That is PLAN-T6, tracked as R-5.

## 3. Threats and current disposition

Legend: **Implemented** (control exists and is tested) · **Backlog** (a named item in
`docs/PHASE-1-PLAN.md` that will implement it) · **Residual** (accepted, documented, not
mitigated in Phase 1). Backlog items are written `PLAN-Tn` to keep them distinct from the
threat identifiers `T1`–`T26` used in this section.

### Evidence acquisition

| ID | Threat | Disposition |
|---|---|---|
| T1 | **Source impersonation / wrong endpoint** — evidence fetched from an address that is not the approved source | **Implemented.** Only an exact allowlisted URL may be fetched; any other initial URL is rejected (`touchstone/sources.py:151`). URLs must be HTTPS with no embedded credentials and no non-443 port (`touchstone/sources.py:276`) |
| T2 | **Redirect abuse** — an open or cross-host redirect moves retrieval to attacker-controlled bytes | **Partially implemented.** At most one redirect is followed, cross-host and non-HTTPS redirects are refused (`touchstone/sources.py:196`, `touchstone/sources.py:295`). The final URL is *not* required to be independently present in the allowlist, so a same-host open redirect is still followed. **Backlog: PLAN-T5** |
| T3 | **MIME confusion** — a source returns a different content type than the manifest approved | **Backlog: PLAN-T5.** `SourceManifest.expected_mime` exists and the received `Content-Type` is recorded as `declared_mime` (`touchstone/sources.py:213`), but the two are never compared. Recorded, not enforced |
| T4 | **Content-encoding confusion / decompression bomb** | **Partially implemented.** `Accept-Encoding: identity` is requested (`touchstone/sources.py:134`) and every response is capped at the manifest's byte limit before storage (`touchstone/sources.py:192`). The response's actual `Content-Encoding` is never validated, so a source that compresses anyway is not detected. **Backlog: PLAN-T5**; ZIP/PDF expansion limits arrive with **PLAN-T10** |
| T5 | **Oversized input** — memory exhaustion via a large response | **Implemented.** Per-source `max_bytes` enforced at read time and again after transport (`touchstone/sources.py:192`, `touchstone/sources.py:235`) |
| T6 | **Hostile JSON** — deep nesting, duplicate keys, float coercion, unexpected shape | **Implemented.** Depth cap (`touchstone/normalize/ustb.py:17`), magic-byte root check, exact-object field sets, decimal-as-text parsing, and duplicate row-date rejection. Numbers never round-trip through binary floats |
| T7 | **Hostile PDF** | **Backlog: PLAN-T10.** No PDF path exists yet; page, expanded-text and timeout limits are required before USDY lands |
| T8 | **Parser escape** — parsing code executes attacker-influenced logic in the main process | **Mitigated, with a stated limit.** Normalisation runs in a spawned worker with a hard wall-clock timeout (`touchstone/normalize/ustb.py:265`, default 2.0s at `:18`). See **R-3** — this is process isolation, not a sandbox |

### Compilation

| ID | Threat | Disposition |
|---|---|---|
| T9 | **Prompt injection** — instructions embedded in issuer evidence steer the compiler | **Partially mitigated, untested.** The model is given no tool surface at all: the request body carries only `model`, `messages` and `temperature` (`touchstone/compiler.py:114`), so there is no shell, network, wallet or contract capability for injected text to invoke. Impact is further bounded because output is only a *proposal* that must survive schema validation, an adapter/source binding check (`touchstone/compiler.py:379`) and explicit approval before evaluation. **This constrains impact; it does not prevent steering.** Embedded instructions can still shape a schema-valid, byte-citable proposal, and **no prompt-injection test exists**. Backlog: **PLAN-T5** |
| T10 | **False citation** — a proposed control cites evidence it did not come from | **Partially implemented — byte-presence only.** The cited span must occur byte-exactly in both the stored artifact and the excerpt shown to the model (`touchstone/compiler.py:325`). That proves the bytes are present; it does not prove uniqueness or that they denote the value the adapter consumed. The broader threat remains open under **R-1** |
| T11 | **Silent model substitution** — output attributed to a model that did not produce it | **Not implemented.** Prompt hash, compiler version, input hash and the exact raw output are recorded, but the recorded model id is the *requested* one read from configuration (`touchstone/compiler.py:90`), and the response parser reads only the message content and never the provider's returned model identity (`touchstone/compiler.py:157`). A substituted model is therefore attributed to the configured name. The actual model remains provider-attested and untested by Touchstone |
| T12 | **Overconfident acceptance** — an ambiguous document yields a confident control | **Partially implemented.** Below-threshold confidence abstains rather than accepting (`touchstone/compiler.py:340`), but `compiler_confidence` is **supplied by the model in its own proposal**, so a confidently wrong or hostile output can clear the threshold by asserting a high number. The remaining protection is human approval before a control becomes evaluable, not the gate |

### Evaluation and state

| ID | Threat | Disposition |
|---|---|---|
| T13 | **Mutable evidence** — the source revises a value after it was observed | **Implemented.** Value controls observe only a row confirmed unchanged across two retained captures at least 24h apart; a revised row is skipped. Documented in `docs/CONTROL-LANGUAGE.md` and `SOURCE_AUDIT.md` |
| T14 | **Retrieval failure mistaken for issuer failure** | **Rule implemented; runtime path not.** The transition rule is correct and tested: `SOURCE_ERROR` preserves the previous state while its evidence deadline holds and only becomes `STALE` after expiry (`touchstone/controls.py:282`). But **no runtime caller ever produces that event** — a fetch or normalisation failure propagates out of the epoch (`touchstone/epoch.py:187`) and terminates the run instead of being recorded. Backlog: **PLAN-T7** |
| T15 | **Missing or conflicting observations** | **Implemented within an epoch that completes.** Absent or unusable evidence yields `UNEVALUABLE`, which drives `UNVERIFIABLE` rather than a confident result; only a genuine predicate conflict yields `INCONSISTENT` (`touchstone/controls.py:262`). This covers evidence that is retrieved but unusable, not an epoch that fails to complete — see T14 |
| T16 | **Evidence-store tampering** | **Implemented for detection.** The index is a hash chain and every append re-verifies the full chain and re-hashes every referenced object (`touchstone/evidence.py:78`, `:88`). A privileged actor able to rewrite objects *and* recompute the whole chain is not defended against — **R-4** |

### Publication

| ID | Threat | Disposition |
|---|---|---|
| T17 | **Signature or key compromise** | **Partially implemented.** Reports are Ed25519-signed and offline-verifiable; the registry supports onchain publisher rotation with preserved historical attribution. Reporting-key rollover and hardware/multisig custody are **PLAN-T6/PLAN-T12**; see **R-5** |
| T18 | **Sequence replay / gap** | **Implemented.** The registry enforces a monotonic per-asset sequence, and the publisher refuses a sequence already onchain or not exactly next (`touchstone/publish.py:381`) |
| T19 | **Duplicate publication after a crash** | **Implemented.** A persisted pending journal plus onchain reconciliation resolves an interrupted send instead of resending it (`touchstone/publish.py:372`). Real subprocess-restart coverage is **PLAN-T7/PLAN-T12** |
| T20 | **Chain or deployment mismatch** — publishing to the wrong chain or a wrong contract | **Partially implemented.** The registry stores an immutable expected chain id and compares it on every write (`contracts/contracts/TouchstoneRegistry.sol:72`). Manifest-pinned RPC, chain id, address and runtime-bytecode verification on the client side are **PLAN-T6** |
| T21 | **RPC failure or dishonest RPC** | **Partially implemented.** No value is ever invented on failure, and an interrupted send is reconciled against the chain rather than resent (see T19). Not built: endpoint pinning, expected chain id and runtime-bytecode verification on the client side, and retry policy. Backlog: **PLAN-T6/PLAN-T7** |

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
  retrieve evidence says nothing about the issuer. It preserves the last evidence-derived
  state until that evidence's own deadline expires.
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
| Instructions embedded in evidence are never followed | Partially — structurally prevented; **no test** (T9). Backlog: **PLAN-T5** |
| Allowlisted URLs | Implemented (`touchstone/sources.py:151`) |
| Blocked redirects | Partially — same-host single redirect still followed. Backlog: **PLAN-T5** |
| MIME limits | **Not enforced.** Backlog: **PLAN-T5** |
| Magic-byte limits | Implemented for JSON; PDF/ZIP with **PLAN-T10** |
| Size limits | Implemented (`touchstone/sources.py:192`) |
| Decompression limits | Not implemented. Backlog: **PLAN-T5/T10** |
| Page limits | Not applicable until PDFs land. Backlog: **PLAN-T10** |
| Time limits | Implemented for parsing (`touchstone/normalize/ustb.py:18`); network timeout on fetch |
| Isolated parsing worker | Implemented, with **R-3** stated |
| Model id, prompt hash, compiler version, input hash, raw output recorded | Implemented |

### 5.2 Immediate (hackathon) security ladder

| Requirement | Status |
|---|---|
| Separated deployer / publisher / Ed25519 / operations keys | **Not yet** — policy and templates only. Backlog: **PLAN-T6**, residual **R-5** |
| No secrets in code, logs, bundles or clients | Holds today; no secret is committed. The E2E now warns when a Hardhat log containing development keys cannot be removed |
| Onchain key rotation | Implemented in the registry, with preserved historical attribution and lineage |
| Monotonic sequences + replay protection | Implemented (registry + `touchstone/publish.py:381`) |
| Allowlisted adapters | Implemented — control adapter must match the source manifest (`touchstone/compiler.py:379`) |
| Sandboxed parsing | Process isolation only — **R-3** |
| Prompt-injection tests | **Missing.** Backlog: **PLAN-T5** |
| Full model/prompt version recording | Implemented |
| Signed release manifests | Backlog: **PLAN-T13** |
| Daily backups + tested restore | Backlog: **PLAN-T8** |
| Public status and incident history | Backlog: **PLAN-T7/T8/T9** |

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
  shows a row was not revised between two retained captures. It cannot establish that an
  older row is never revised. The two-business-day minimum age is derived from two captures
  and the business-day count ignores exchange and bank holidays.
- **R-3 — Parser isolation is process isolation, not a kernel-enforced sandbox.**
  Normalisation runs in a spawned worker with a wall-clock timeout. There is no seccomp,
  container, namespace or capability restriction; a worker retains the privileges of the
  service account. A parser escape would be contained only by process boundaries and the
  timeout.
- **R-4 — Evidence-store integrity is detection, not prevention.** The hash-chained index
  detects modification of stored artifacts or entries. An actor with write access to the
  store who rewrites objects and recomputes the entire chain is not detected locally;
  published roots and the transparency log are the external check.
- **R-5 — Key separation is not yet enforced in a runtime path.** The local publisher uses
  an unlocked development account. Distinct deployer, publisher, reporter and operations
  identities are required and specified but arrive with T6; no hardware or multisig custody
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
- **R-10 — Time is taken from the host clock.** Retrieval timestamps, freshness deadlines,
  the 24-hour confirmation separation and the consumer gate's age check all derive from the
  local clock (B15). There is no trusted time source, no monotonic guard against a clock
  moving backwards, and no skew check against the chain or the source. A wrong host clock
  produces confidently wrong freshness.
- **R-8 — Source probes were run from a development machine.** Repeated retrieval from the
  eventual deployment host is unverified and remains parked behind the deployment gate.

## 7. Explicitly out of scope for Phase 1

The Phase 3 formal threat model, independent smart-contract audit, external pipeline
security review, incident-response runbook, multi-region deployment, supply-chain controls
and reproducible builds are not part of this document and are not claimed. Multi-publisher
quorum, challenge mechanics and any staking or slashing design are Phase 4 and do not
exist.
