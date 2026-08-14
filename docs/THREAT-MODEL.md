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

Touchstone claims exactly one class of thing: *that a named first-party source published
specific bytes at a specific time, that a stated control was evaluated against those bytes
deterministically, and that the result was signed and published.* It does not claim the
issuer is honest, that the published figures are accurate, or that an asset is sound.

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
| B6 | **Ed25519 reporting key** | Authenticity of a report's content | A compromised key can sign false reports until rotated; consumers pin publisher lineage |
| B7 | **EVM publisher key** | Authority to write to the registry | A compromised key can publish authentic-looking state; the owner can revoke it onchain |
| B8 | **Deployer / owner key** | Contract deployment and publisher authorisation | Full control of the registry's publisher set |
| B9 | **Operations / backup identity** | Service continuity and archive integrity | Backup loss or forgery; separated from publishing keys by policy — see T6/T8 |
| B10 | **Registry contract** (X Layer) | Immutable, ordered, append-only history | Chain reorganisation or a wrong-chain deployment; guarded by an immutable expected chain id |
| B11 | **Consumer contract** (`AssetGate`) | Enforcing its own freshness policy | A permissive policy admits stale state; the gate reacts to verification freshness, never to asset safety |
| B12 | **Public projection** (dossier, heartbeat) | Displaying only signed, verified data | Claim inflation in the UI — see T20 |
| B13 | **Viewer's browser** | Rendering | No wallet is required and no key material reaches the page |

Keys at B6, B7, B8 and B9 are **required to be four distinct identities**. Today the local
publisher path uses an unlocked development account, so that separation exists as policy
and templates but is not yet enforced in a production path. That is T6, tracked as R-5.

## 3. Threats and current disposition

Legend: **Implemented** (control exists and is tested) · **Backlog** (named item that will
implement it) · **Residual** (accepted, documented, not mitigated in Phase 1).

### Evidence acquisition

| ID | Threat | Disposition |
|---|---|---|
| T1 | **Source impersonation / wrong endpoint** — evidence fetched from an address that is not the approved source | **Implemented.** Only an exact allowlisted URL may be fetched; any other initial URL is rejected (`touchstone/sources.py:151`). URLs must be HTTPS with no embedded credentials and no non-443 port (`touchstone/sources.py:276`) |
| T2 | **Redirect abuse** — an open or cross-host redirect moves retrieval to attacker-controlled bytes | **Partially implemented.** At most one redirect is followed, cross-host and non-HTTPS redirects are refused (`touchstone/sources.py:196`, `touchstone/sources.py:295`). The final URL is *not* required to be independently present in the allowlist, so a same-host open redirect is still followed. **Backlog: T5** |
| T3 | **MIME confusion** — a source returns a different content type than the manifest approved | **Backlog: T5.** `SourceManifest.expected_mime` exists and the received `Content-Type` is recorded as `declared_mime` (`touchstone/sources.py:213`), but the two are never compared. Recorded, not enforced |
| T4 | **Content-encoding confusion / decompression bomb** | **Partially implemented.** `Accept-Encoding: identity` is requested (`touchstone/sources.py:134`) and every response is capped at the manifest's byte limit before storage (`touchstone/sources.py:192`). The response's actual `Content-Encoding` is never validated, so a source that compresses anyway is not detected. **Backlog: T5**; ZIP/PDF expansion limits arrive with **T10** |
| T5 | **Oversized input** — memory exhaustion via a large response | **Implemented.** Per-source `max_bytes` enforced at read time and again after transport (`touchstone/sources.py:192`, `touchstone/sources.py:235`) |
| T6 | **Hostile JSON** — deep nesting, duplicate keys, float coercion, unexpected shape | **Implemented.** Depth cap (`touchstone/normalize/ustb.py:17`), magic-byte root check, exact-object field sets, decimal-as-text parsing, and duplicate row-date rejection. Numbers never round-trip through binary floats |
| T7 | **Hostile PDF** | **Backlog: T10.** No PDF path exists yet; page, expanded-text and timeout limits are required before USDY lands |
| T8 | **Parser escape** — parsing code executes attacker-influenced logic in the main process | **Mitigated, with a stated limit.** Normalisation runs in a spawned worker with a hard wall-clock timeout (`touchstone/normalize/ustb.py:265`, default 2.0s at `:18`). See **R-3** — this is process isolation, not a sandbox |

### Compilation

| ID | Threat | Disposition |
|---|---|---|
| T9 | **Prompt injection** — instructions embedded in issuer evidence steer the compiler | **Partially implemented, untested.** The model is given no tool surface at all: the request body carries only `model`, `messages` and `temperature` (`touchstone/compiler.py:114`). It has no shell, network, wallet or contract capability to invoke, and its output is only ever a control proposal that must survive schema validation, an adapter/source binding check (`touchstone/compiler.py:379`), a confidence gate (`touchstone/compiler.py:340`) and explicit approval before it can be evaluated. **No prompt-injection test exists.** **Backlog: T5** |
| T10 | **False citation** — a proposed control cites evidence it did not come from | **Implemented, with a structural limit.** The cited span must occur byte-exactly in both the stored artifact and the excerpt shown to the model (`touchstone/compiler.py:325`). See **R-1** for what this does not prove |
| T11 | **Silent model substitution** — output attributed to a model or prompt that did not produce it | **Implemented.** Model id, prompt hash, compiler version, input hash and the exact raw output are all recorded with the compilation |
| T12 | **Overconfident acceptance** — an ambiguous document yields a confident control | **Implemented.** Below-threshold confidence is rejected rather than accepted (`touchstone/compiler.py:340`), and abstention is a first-class outcome |

### Evaluation and state

| ID | Threat | Disposition |
|---|---|---|
| T13 | **Mutable evidence** — the source revises a value after it was observed | **Implemented.** Value controls observe only a row confirmed unchanged across two retained captures at least 24h apart; a revised row is skipped. Documented in `docs/CONTROL-LANGUAGE.md` and `SOURCE_AUDIT.md` |
| T14 | **Retrieval failure mistaken for issuer failure** | **Implemented.** A transient failure is `SOURCE_ERROR`, which preserves the previous state while its evidence deadline holds and only becomes `STALE` after expiry (`touchstone/controls.py:282`). See section 4 |
| T15 | **Missing or conflicting observations** | **Implemented.** Absent or unusable evidence yields `UNEVALUABLE`, which drives `UNVERIFIABLE` rather than a confident result; only a genuine predicate conflict yields `INCONSISTENT` (`touchstone/controls.py:262`) |
| T16 | **Evidence-store tampering** | **Implemented for detection.** The index is a hash chain and every append re-verifies the full chain and re-hashes every referenced object (`touchstone/evidence.py:78`, `:88`). A privileged actor able to rewrite objects *and* recompute the whole chain is not defended against — **R-4** |

### Publication

| ID | Threat | Disposition |
|---|---|---|
| T17 | **Signature or key compromise** | **Partially implemented.** Reports are Ed25519-signed and offline-verifiable; the registry supports onchain publisher rotation with preserved historical attribution. Reporting-key rollover and hardware/multisig custody are **T6/T12**; see **R-5** |
| T18 | **Sequence replay / gap** | **Implemented.** The registry enforces a monotonic per-asset sequence, and the publisher refuses a sequence already onchain or not exactly next (`touchstone/publish.py:381`) |
| T19 | **Duplicate publication after a crash** | **Implemented.** A persisted pending journal plus onchain reconciliation resolves an interrupted send instead of resending it (`touchstone/publish.py:372`). Real subprocess-restart coverage is **T7/T12** |
| T20 | **Chain or deployment mismatch** — publishing to the wrong chain or a wrong contract | **Partially implemented.** The registry stores an immutable expected chain id and compares it on every write (`contracts/contracts/TouchstoneRegistry.sol:72`). Manifest-pinned RPC, chain id, address and runtime-bytecode verification on the client side are **T6** |
| T21 | **RPC failure or dishonest RPC** | **Backlog: T6/T7.** No value is ever invented on failure, but retry, reconciliation-on-restart and endpoint pinning are not yet built |

### Operations and presentation

| ID | Threat | Disposition |
|---|---|---|
| T22 | **Source outage** | **Backlog: T7.** The semantics exist (`SOURCE_ERROR`); the service that records an incident and keeps scheduling does not |
| T23 | **Scheduler crash** | **Backlog: T7.** The current scheduler is one-shot and a failed epoch aborts it |
| T24 | **Backup loss** | **Backlog: T8.** No backup or restore path exists |
| T25 | **Incident deletion** — an embarrassing failure quietly disappears | **Backlog: T7.** Incident history must be append-only and hash-chained; recovery closes an incident with a new event rather than removing it. Not built |
| T26 | **UI claim inflation** — the public surface implies more than the evidence proves | **Backlog: T9.** No public surface exists yet. Requirements: "verified" and "not verified" visually separated, deterministic explanations drawn only from accepted graph data, explorer links absent rather than fabricated when undeployed, and no ordinary NAV movement described as a risk event |

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
| Instructions embedded in evidence are never followed | Partially — structurally prevented; **no test** (T9). Backlog: **T5** |
| Allowlisted URLs | Implemented (`touchstone/sources.py:151`) |
| Blocked redirects | Partially — same-host single redirect still followed. Backlog: **T5** |
| MIME limits | **Not enforced.** Backlog: **T5** |
| Magic-byte limits | Implemented for JSON; PDF/ZIP with **T10** |
| Size limits | Implemented (`touchstone/sources.py:192`) |
| Decompression limits | Not implemented. Backlog: **T5/T10** |
| Page limits | Not applicable until PDFs land. Backlog: **T10** |
| Time limits | Implemented for parsing (`touchstone/normalize/ustb.py:18`); network timeout on fetch |
| Isolated parsing worker | Implemented, with **R-3** stated |
| Model id, prompt hash, compiler version, input hash, raw output recorded | Implemented |

### 5.2 Immediate (hackathon) security ladder

| Requirement | Status |
|---|---|
| Separated deployer / publisher / Ed25519 / operations keys | **Not yet** — policy and templates only. Backlog: **T6**, residual **R-5** |
| No secrets in code, logs, bundles or clients | Holds today; no secret is committed. The E2E now warns when a Hardhat log containing development keys cannot be removed |
| Onchain key rotation | Implemented in the registry, with preserved historical attribution and lineage |
| Monotonic sequences + replay protection | Implemented (registry + `touchstone/publish.py:381`) |
| Allowlisted adapters | Implemented — control adapter must match the source manifest (`touchstone/compiler.py:379`) |
| Sandboxed parsing | Process isolation only — **R-3** |
| Prompt-injection tests | **Missing.** Backlog: **T5** |
| Full model/prompt version recording | Implemented |
| Signed release manifests | Backlog: **T13** |
| Daily backups + tested restore | Backlog: **T8** |
| Public status and incident history | Backlog: **T7/T8/T9** |

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
- **R-6 — Verification bundles carry digests, not artifacts.** An offline verifier can
  confirm signatures, roots, chain integrity and internal consistency, but cannot replay
  normalisation or prove that a reported row occurs inside an artifact it does not have.
- **R-7 — TLS is trusted without pinning.** Evidence integrity in transit rests on the
  platform certificate store.
- **R-8 — Source probes were run from a development machine.** Repeated retrieval from the
  eventual deployment host is unverified and remains parked behind the deployment gate.

## 7. Explicitly out of scope for Phase 1

The Phase 3 formal threat model, independent smart-contract audit, external pipeline
security review, incident-response runbook, multi-region deployment, supply-chain controls
and reproducible builds are not part of this document and are not claimed. Multi-publisher
quorum, challenge mechanics and any staking or slashing design are Phase 4 and do not
exist.
