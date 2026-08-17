# Submission draft

**DRAFT — NOT SUBMITTED.**

Unpublished text in a file. This is not a submission. It has not been
sent to any organiser, form, portal, or judge. Filling a field below
does not file it. Owner gate G5 in `docs/PHASE-1-PLAN.md` remains open.

Where a URL or address does not exist, the field is `not_deployed`.
Where this repository does not name a value, the field is `unknown`.
No traction, user count, ranking, or outcome is claimed. None has been
observed.

---

## 1. Project

| Field | Value |
|---|---|
| Name | Touchstone |
| Version | `0.1.0` (`pyproject.toml`) |
| Licence | Apache-2.0 |
| One sentence | Touchstone compiles issuer-published RWA disclosures into cited, machine-checkable controls, evaluates them deterministically against retained evidence, and can publish signed results to an append-only registry on X Layer. |
| Public website | `not_deployed` |
| Public dossier | `not_deployed` (PLAN-T9 unbuilt) |
| Demo URL | `not_deployed` |
| Documentation site | `not_deployed` |
| X / social handles | `not_deployed` (owner gate; see `docs/BRAND-CLEARANCE.md`) |
| Domain | `not_deployed` |
| Repository | Private git remote `github.com/Ridwannurudeen/touchstone` exists. Making it public is a separate owner decision. A public clone URL is `not_deployed`. |
| Submission venue | `unknown` — this repository names a hackathon submission as owner gate G5 and does not name the form or organiser. |
| Contact | `unknown` — not recorded in the tracked tree. |

Touchstone does not issue assets, custody funds, recommend investments,
assign credit ratings, or claim facts beyond the evidence class it has
actually verified.

---

## 2. What was built

A single USTB vertical, local and on a testnet registry that has not
yet been published to.

**Evidence and controls.** Source manifests for USTB, USDY and FOBXX.
Golden fixtures where retrieval was bounded. Eight accepted controls,
each a candidate a model proposed from Superstate's own bytes and bound
by digest to the compilation artifact that accepted it
(`data/compilations/APPROVALS.json`). Two further candidates were
declined, with reasons. The compiler has no tool surface. Approval
changes only `approval_state` and `compilation_sha256`.

**Evaluation.** Deterministic evaluator. Asset states `CONFIRMED`,
`STALE`, `INCONSISTENT`, `UNVERIFIABLE`. Value controls on the NAV
source observe only a row confirmed unchanged across two captures at
least 24 hours apart. An unseeded workspace reports `UNVERIFIABLE`.

**Publication path.** Ed25519-signed reports, hash-chained transparency
log, offline verification bundle (bundle v4 carries compilation
artifacts and the approval ledger). Locally signed raw transactions. A
deployment manifest pins chain id, registry address, runtime bytecode
digest, publisher lineage and confirmation depth. Preflight runs in
full immediately before signing.

**Contracts.** `TouchstoneRegistry`: append-only reports, per-epoch
uniqueness (`epochKey` / `epochSequence`), corrections that must name
the epoch they correct, publisher authorise / revoke / rotate,
immutable owner and expected chain id. No custody, no payable, no
token, no proxy, no `delegatecall`, no `selfdestruct`.
`AssetGate`: freshness and publisher checks against the latest report.
Written and tested; deployed only on an ephemeral local chain.

**Operations code.** Unattended daemon (`scripts/run_service.py`),
append-only incidents, heartbeat, watchdog, one HTTPS alert webhook,
gas runway from measured costs, encrypted backup and a restore that
verifies into a fresh directory. Packaging onto a host is
`not_configured`.

**Testnet.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on
X Layer testnet (chain 1952), deployed 2026-08-17 at block 38489602
under a recorded owner approval. Publisher
`0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` authorised. Holds zero
reports. Predecessor `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` is
superseded and must not be published to.

**CI.** `.github/workflows/ci.yml` runs ruff, pytest on Python 3.11 and
3.12, Hardhat (expects 76 passing contract tests), a managed local-
chain E2E, and a mutation harness. The workflow is given no project
secret. Branch protection that would make the aggregate job mandatory
is `unknown` from this tree; the workflow file itself says that on a
private repository without GitHub Pro the protection APIs refuse.

**Release builder.** `scripts/build_release.py` writes an unsigned JSON
document from the tree and from caller-supplied test counts. It does
not invent counts and does not read the clock.

---

## 3. What was missed

Stated as misses, not as near-misses.

| Target (`ROADMAP.md`) | Result |
|---|---|
| ≥2 fully autonomous **live** adapters | **Missed.** One adapter built (USTB), zero proven live. USDY blocked on a 260 MB archive. FOBXX cut as an adapter. OUSG cut. |
| One live consumer contract gating on state | **Missed.** `AssetGate` is `not_deployed` on any persistent chain. |
| One production canary epoch | **Missed.** Nothing has been published to any registry. |
| Living dossier and developer surface | **Unbuilt.** PLAN-T9. |
| Public demo | **`not_deployed`.** The two-act script in `ROADMAP.md` cannot be walked as written. See `docs/DEMO-RUNBOOK.md`. |

Phase 1 ships one USTB vertical. The two-adapter and production-canary
metrics were not retargeted.

---

## 4. Chain and product addresses

| Item | Value |
|---|---|
| X Layer testnet (1952) registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` |
| Testnet publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` |
| Testnet `AssetGate` | `not_deployed` |
| X Layer mainnet (196) registry | `not_deployed` |
| Mainnet publisher | `not_deployed` |
| Mainnet `AssetGate` | `not_deployed` |
| Hero asset (off-chain identity) | USTB `eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e` |
| Published report / explorer tx | `not_deployed` — no report has been published |
| Verification API | `not_deployed` |
| Status page | `not_deployed` |

Mainnet is unscheduled. It is conditional on a proven testnet loop, and
is additionally blocked until the deployer and publisher keys sit on
separate hosts, which they currently do not.

---

## 5. Demo, as it would be described if asked

The specified demo is two acts, 90–120 seconds, in `ROADMAP.md`. It
depends on a dossier that does not exist and an `AssetGate` that is
not on a persistent chain.

What can be shown without a public chain: the managed local-chain
end-to-end test, which deploys a registry and a gate on Hardhat,
evaluates the approved controls against committed Superstate fixtures,
publishes, ages, and corrects. Command: `python -m pytest --strict-markers tests/test_e2e_local.py`.
That is a local rehearsal, not a live fill.

If a live issuer endpoint fails during any attempted live path, the
switch to fixtures must be spoken and logged. Fixture mode is refused
on a public network. Details: `docs/DEMO-RUNBOOK.md`.

---

## 6. AI usage

From `AI_USAGE.md`, in full:

> AI coding assistants are used for implementation, testing, and
> independent review. All generated work is checked against the
> repository specifications and verified by the project owner before
> any submission or deployment.

The product compiler (`scripts/compile_controls.py`) is a separate
fact: it calls an HTTPS chat-completions endpoint to **propose**
controls. It does not approve them, does not run in the serving
runtime, and is not given tools. Model endpoint, name and key are
environment variables, not committed.

This draft is not a submission, so the sentence in `AI_USAGE.md` about
verification "before any submission" has not been discharged by filing
this file.

---

## 7. Claim-to-evidence checklist

Every public sentence a submission might be tempted to use, with the
evidence or the refusal.

| Claim one might want | Honest substitute | Evidence |
|---|---|---|
| "Live on X Layer" | A testnet registry is deployed; nothing has been published to it. | `docs/OPERATIONS.md`, `docs/DEPLOYMENT-G1-EXECUTED.md` |
| "Two live adapters" | One adapter built, zero proven live. | `ROADMAP.md` completion table |
| "Consumer contract in production" | `AssetGate` is `not_deployed` on a persistent chain. | `scripts/e2e_local.py`; no address in `deployments/` |
| "Autonomous canary" | Prepared, not executed. | `docs/CANARY-G1B.md` |
| "Public dossier" | Unbuilt. | PLAN-T9 status empty in `docs/PHASE-1-PLAN.md` |
| "Independently attested" | Not claimed. Issuer API is issuer disclosure. | `manifests/sources/ustb.json` `authority_class: issuer-api` |
| "Regulatory-grade" / "safe" / "solvent" | Banned. Not claimed. | `ROADMAP.md` ambition-theatre list; `docs/LIMITATIONS.md` |
| "Users" / "volume" / "TVL" | None. No product users are recorded. | No such metric exists in the tree |
| "OKX / issuer partner" | Not claimed. Endorsement would need written confirmation. | `ROADMAP.md` |
| "Name is clear" | Two live crypto projects use Touchstone; counsel opinion is not on file. | `docs/BRAND-CLEARANCE.md` |
| "Signed release" | The builder emits unsigned JSON. | `scripts/build_release.py` |
| "Production host" | `not_configured`. | `docs/OPERATIONS.md` |

---

## 8. Attachments a real submission would still need

Prepared in the repository, not attached to anything:

- `docs/LIMITATIONS.md`
- `docs/DEMO-RUNBOOK.md`
- `docs/RELEASE-RUNBOOK.md`
- `docs/CANARY-G1B.md` (canary, not executed)
- `AI_USAGE.md`
- A release document from `scripts/build_release.py` — **not yet cut**
- A signed release manifest — **not implemented**

Not prepared, and not to be invented:

- Public repository URL — `not_deployed`
- Demo video URL — `not_deployed`
- Live explorer link to a publication — `not_deployed`
- Mainnet addresses — `not_deployed`

---

## 9. Owner action still required

This file does not submit. A real filing needs a fresh explicit owner
approval in the then-current context (product principle 8,
`ROADMAP.md`; gate G5). It also needs a venue. This repository does
not name one.

Do not send this draft. Do not paste it into a form. Do not imply that
because the draft exists, the misses above have closed.
