# AI usage

Two different things in this project are called AI, and only one of them is part of the
product. This file used to describe only the other one — three sentences about coding
assistants — which left a reader to conclude that AI here meant autocomplete. That was a poor
description of a system whose central component is a bounded AI compiler.

---

## 1. AI in the product: the control compiler

An issuer publishes disclosures as prose and loosely-typed JSON. A smart contract cannot read
them. The gap between "the fund says its NAV is dated the fourteenth" and a predicate a
deterministic engine can evaluate is a *semantic* problem, and it is the one thing here that a
model is genuinely better at than code.

So a model proposes controls. It does not decide anything.

### What the model is allowed to do

Propose typed candidate controls from the issuer's own retrieved bytes. Each candidate must
carry a subject, an adapter, a comparison operator, an expected value, and **an exact byte span
quoted from the source artifact**.

### What the model is not allowed to do

- It cannot approve a control. Approval is a separate human decision recorded in
  `data/compilations/APPROVALS.json`.
- It cannot decide an asset's status. Runtime state is computed by `touchstone/evaluate.py`
  from approved controls and retained evidence. **The model is never consulted at evaluation
  time**, and nothing in the serving path calls a provider.
- It cannot widen its own remit. `expected_value` may not carry keys its operator does not
  define; a freshness window must equal the source manifest's declared policy; `grace_period`
  must be zero for every non-freshness operator.
- It cannot smuggle instructions. Proposals matching an instruction-like pattern
  (`ignore`, `disregard`, `override`, `execute`, `fetch`, `curl`, …) or carrying URLs to
  unexpected hosts are treated as suspect (`touchstone/compiler.py:44`).

### The deterministic gates every proposal must pass

A proposal is not a control. Before it can even be offered for approval, code checks that the
cited span occurs in the retrieved artifact byte-for-byte; that the source and adapter
combination is one the evaluator can actually decide; that the schema is exact; that the
declared confidence clears 0.8; and that the asset binding matches. A candidate that fails any
gate is recorded as rejected or abstained, with its reason, in a durable artifact.

### Measured, from the artifacts in this repository

18 compilation artifacts, 72 candidate outcomes:

| Outcome | Count | Meaning |
|---|---|---|
| Accepted by the gates | 55 | passed every deterministic check — *eligible for human review*, not live |
| Abstained | 15 | compiler confidence below the 0.8 threshold |
| Rejected | 2 | the evaluator cannot decide that source and operator combination |

Requested model identity matched the returned model identity in **72 of 72** outcomes
(`claude-opus-5`). A response from a different model than the one asked for is a provenance
failure, so the returned identity is recorded rather than assumed.

Of the accepted candidates, a human approved **5** and declined **5**. The declines are the
interesting half, because they show the boundary doing work a model would not have applied to
itself:

- two declined as *redundant* — a second control reading the same field a live control already
  reads;
- one declined for clearing the confidence gate **by zero margin** (0.80 against a gate of
  < 0.80);
- two declined as additional scalar coverage on a document already covered, rather than breadth.

Approving those five would have doubled the control count and added no evidence. The published
completion table records the control target as *missed* at five rather than met at ten, because
counting duplication as breadth is the kind of number that makes a metric worthless.

### Provenance recorded for every compilation

Requested and returned model identity, provider endpoint and response id, SHA-256 of the
prompt, of the input evidence, and of the raw provider response, the compiler version, and the
retrieval instant. An approved control is bound by digest to the compilation that produced it,
so a control cannot be silently re-attributed to a different model run.

### What this does not claim

The model's self-reported confidence is not a calibrated probability. An exact span proves the
bytes occur in the artifact, not that they denote the field the adapter consumes — that is
recorded as **R-1** in `docs/THREAT-MODEL.md`. A schema-valid proposal can still be steered by
a dishonest source. And the approval that stops all of this is currently an unauthenticated
field: it records a decision without proving who made it. That is a known gap, not a solved
problem.

---

## 2. AI outside the product: coding assistants

AI coding assistants were used for implementation, testing and review, as they are on most
software written in 2026. Generated work is checked against the repository's specifications and
verified by the project owner before any submission or deployment.

**This has nothing to do with the compiler above.** No coding assistant runs at compile time,
evaluation time, signing time or publication time. Nothing in `touchstone/` calls a model except
`touchstone/compiler.py`, and that path runs only when a human deliberately recompiles controls
— never while an epoch is being served.
