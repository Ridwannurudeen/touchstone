# Touchstone Control Language v0

Control Language v0 represents an approved, evidence-bound claim that deterministic
surveillance can evaluate. AI may propose a record, but schema validation, approval,
and evaluation determine state. The record does not assert that an asset is safe,
solvent, compliant, or suitable.

## Control record

The canonical record has exactly these fields, in roadmap order:

| Field | v0 type | Meaning |
|---|---|---|
| `asset_key` | non-empty string | Canonical identity of the observed asset. |
| `control_id` | non-empty string | Stable identifier within the asset's control set. |
| `control_version` | positive integer | Monotonic version of this control. Booleans are not integers. |
| `predicate_type` | non-empty string | Bounded family of claim being evaluated. |
| `subject` | non-empty string | Human-readable subject of the predicate. |
| `source_id` | non-empty string | Stable identifier for the evidence source. |
| `source_authority_class` | non-empty string | Evidence authority classification; for example, issuer disclosure is not independent attestation. |
| `evidence_span` | non-empty string | Exact source-local span supporting the control. |
| `cadence` | non-empty string | Declared publication cadence used by the approved policy. |
| `grace_period` | non-negative integer | Whole calendar days added by the approved freshness policy. Booleans are not integers. |
| `observation_adapter` | non-empty string | Deterministic adapter identifier. |
| `comparison_operator` | closed enum | One of the five operators below. |
| `expected_value` | JSON value | Expected operand. Nested arrays and string-keyed objects are allowed; NaN and infinities are rejected. |
| `effective_from` | `YYYY-MM-DD` | First effective calendar date. |
| `effective_until` | `YYYY-MM-DD` or null | Last effective calendar date, not earlier than `effective_from`. |
| `compiler_confidence` | finite number from 0 through 1 | Compiler confidence; it does not bypass approval. |
| `approval_state` | non-empty string | Workflow state assigned by the approval system. |

The roadmap does not yet freeze vocabularies for predicate type, authority class,
cadence, adapter, or approval state. V0 therefore validates those fields as non-empty
strings and does not invent closed values for them.

Construction is strict. A mapping must contain every field and no unknown fields.
Dates and integer fields are type checked, and expected values are recursively checked
as JSON data. Records are frozen. Nested expected values are made immutable on
construction so later caller mutation cannot change identity.

## Operators

- `exists`: the addressed observation is present.
- `fresh_within`: the dated observation is no older than the approved limit.
- `eq`: observed and expected values are equal under the adapter's typed comparison.
- `within_tolerance`: observed and expected numeric values differ by no more than an
  approved tolerance encoded by the control family.
- `non_decreasing`: the current comparable value is at least the prior comparable
  value. This operator is valid only where the evidence supports monotonicity; it is
  not valid for USCC NAV.

Operator names form a closed allowlist. Adding an operator requires a control-language
version change and evaluator support.

## Cross-capture confirmation of value rows

**Confirmation is a source policy, not a property of an operator.** It applies where a
source publishes provisional records and revises them in place — `superstate-ustb-nav-daily`
does exactly that, carrying the previous values forward under the current date and
rewriting the row later. Reading the newest record there would attribute a value to a date
the source has not finished restating.

On that source, value operators (`exists`, `eq`, `within_tolerance`, `non_decreasing`) read
one dated record and observe only a record **confirmed unchanged across retained captures**:
the evaluator compares this epoch's record against the same dated record in a qualifying
earlier capture and accepts it only when the whole normalized record is identical. A record
revised between captures is skipped and an older unchanged record may be observed instead.
The accepted record's date is reported as the evaluation's `observed_on`, and a report that
carries such a value binds both captures through `capture_role` evidence references.

### Presence on non-provisional sources

`superstate-ustb-yield` and `superstate-ustb-holdings` publish scalars rather than a table
of revisable rows, and the strict normalizer already establishes that a scalar is present
and correctly typed in the capture being evaluated. There is nothing for an earlier capture
to confirm. `exists` on these sources is therefore satisfied from the current capture alone,
and requires no confirmation reference.

An earlier version of this document classified *every* `exists` as requiring confirmation,
and the evaluator implemented that by routing all non-freshness operators through the
NAV-row rule. The effect was that a presence control on either of these sources could never
be decided at all: the compiler proposed five such controls against real evidence and every
one of them evaluated `UNEVALUABLE` forever.

What a presence claim proves is deliberately narrow: **the issuer returned this normalized
field in these hash-bound captured bytes.** It is not evidence that the value is correct,
final, stable, or that it will be published again. Such a control is never `CONTRADICTED` —
a missing required field fails normalization outright, so there is no observation to
contradict.

The addressable fields are a closed set per source, listed in `PRESENCE_FIELDS`
(`touchstone/evaluate.py`). `holdings` is deliberately excluded: it is a collection, and
whether `exists` means "present" or "non-empty" is undefined. That meaning will be decided
explicitly or not at all. The compiler refuses any candidate the evaluator cannot decide, so
an unreachable combination is rejected at compilation rather than accepted and left
permanently unevaluable.

A qualifying earlier capture is the newest retained capture of the same source retrieved
at least 24 hours before the current one, so two captures taken minutes apart — including
either side of midnight — never confirm each other. With no qualifying capture, every
value operator returns `UNEVALUABLE`, so the first epoch a publisher ever runs abstains
on value rather than reporting an unconfirmed one. The resulting asset state is
`UNVERIFIABLE` when the remaining evidence is fresh; expired freshness still takes
precedence and yields `STALE`.

`minimum_row_age_business_days` in `expected_value` is a cheap pre-filter in front of
that comparison, not proof of settlement. Absent the key the window is zero, which admits
any record that is not future-dated; a negative, boolean, or non-integer window is
malformed and the control evaluates as `UNEVALUABLE` rather than falling back. The USTB
nav-daily controls declare two business days, the smallest window under which no record
changed between the 2026-08-13 and 2026-08-14 captures retained in `fixtures/`. Two
captures cannot establish that no older record is ever revised, and the count is
weekday-only — exchange and bank holidays are not modelled.

`fresh_within` is deliberately unaffected: it reads the newest record because its subject
is whether the source is still publishing, not what the record is worth.

During compilation, a candidate's `evidence_span` is checked as a byte-exact substring of
the artifact and of the excerpt shown to the model. That proves the cited bytes occur, but
not that the occurrence is unique or that it denotes the field the adapter consumed;
approved controls and offline verification do not repeat the check. That limitation, and
the adapter-bound structural locator that would close it, are recorded as R-1 in
`docs/THREAT-MODEL.md`.

## Canonical representation and identity

`ControlRecord.canonical_bytes()` produces UTF-8 JSON with lexicographically sorted
object keys, compact separators, unescaped Unicode, and no NaN or infinity. Dates use
ISO `YYYY-MM-DD`; enum fields use their string values. `content_hash` is the lowercase
hexadecimal SHA-256 digest of those exact bytes.

Object input order never affects canonical bytes or identity. Field order in the typed
record remains the roadmap order for schema inspection, while serialization sorts keys
to make hashing deterministic.

Records are compared by `content_hash`. Python `hash()` is not supported when
`expected_value` contains an object because object values are stored as immutable
mappings; this is deliberate, and `content_hash` remains the stable identity mechanism.

## Evidence-store policy

The evidence store intentionally verifies the complete index chain and re-hashes every
referenced object before each append. This gives the current small store a simple,
strong integrity check, with O(n^2) total work as the index grows. Before asset count
grows in Phase 2, the planned performance mode is incremental verification of the chain
tail plus spot-checking stored objects; the complete verifier remains the audit path.

The store accepts absolute `http://` and `https://` source URLs because it is a
permissive persistence boundary, not a network client. All production fetchers enforce
HTTPS and a per-source allowlist in the adapter layer introduced in task 3.

## Evaluation results

Evaluation results apply only to controls accepted by the approval gate:

- `SATISFIED`: the current evidence supports the control predicate.
- `CONTRADICTED`: current evidence is evaluable and conflicts with the predicate.
- `UNEVALUABLE`: the system cannot evaluate the predicate from the available evidence.

An empty result set is also unevaluable. Retrieval failure is not represented as a
control result; it is the separate `SOURCE_ERROR` operational event.

## Asset states

- `CONFIRMED`: every supplied accepted-control evaluation is `SATISFIED`, and evidence
  is within its deadline.
- `STALE`: the evidence deadline has expired without a control contradiction.
- `INCONSISTENT`: at least one accepted control is `CONTRADICTED`.
- `UNVERIFIABLE`: evidence is current but at least one control is `UNEVALUABLE`, or no
  evaluations are available outside source-error preservation.

`UNVERIFIABLE` is abstention, not a negative finding. `STALE` means the verification
evidence expired, not that the issuer or asset failed.

## Operational events

- `RECONFIRMED`: a new evaluation supports the existing evidence-backed conclusion.
- `EVIDENCE_CHANGED`: retrieved evidence bytes or observable values changed; the
  evaluation results determine status.
- `SOURCE_ERROR`: retrieval or source processing failed. It never creates an
  inconsistency by itself.
- `CORRECTION_PUBLISHED`: an append-only correction supersedes an earlier report; its
  evaluation results determine status.

Operational events and asset states are deliberately separate.

## Freshness and transition precedence

Freshness is a pure calculation with an explicit `now` calendar date. No control
function reads the system clock. A deadline is inclusive: evidence is fresh when
`now <= evidence_deadline`. `is_fresh(observed_on, now=..., max_age=...)` additionally
requires `observed_on <= now` and a non-negative whole-day duration; its inclusive rule
is `0 <= now - observed_on <= max_age`.

`transition_state(previous, event, evaluation_results, evidence_deadline, now)` applies
this precedence:

1. Any `CONTRADICTED` accepted control yields `INCONSISTENT`.
2. `SOURCE_ERROR` preserves the previous state while evidence remains fresh. Once the
   deadline expires, it yields `STALE`.
3. Any other event with expired evidence yields `STALE`.
4. An empty result set or any `UNEVALUABLE` result yields `UNVERIFIABLE`.
5. Otherwise, all supplied accepted controls are `SATISFIED`, yielding `CONFIRMED`.

A contradiction has priority because it is an already obtained deterministic control
finding; a concurrent or later source error does not erase it. With no contradiction,
`SOURCE_ERROR` alone never directly flips a fresh status. This distinction prevents a
transient outage from being described as issuer inconsistency.
