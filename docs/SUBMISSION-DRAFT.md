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
| Public website | **LIVE** — https://touchstone.gudman.xyz — 20 pages, zero JavaScript, self-hosted fonts |
| Public dossier | **LIVE** — https://touchstone.gudman.xyz/dossier/ustb-2026-08-17 — the published report in full, every control and digest |
| Demo URL | `not_deployed` — no video recorded yet |
| Documentation site | **LIVE** — https://touchstone.gudman.xyz/docs — 1,803 lines of the project's committed documentation |
| X / social handles | **`@touch__stone`** — created by the owner 2026-08-18. The post mentioning @XLayerOfficial is still outstanding, and the rules require the account be *kept active*, which cannot be backdated. |
| Domain | `gudman.xyz` subdomain, TLS via Let's Encrypt, certificate to 2026-11-16 |
| Repository | `github.com/Ridwannurudeen/touchstone` — **private until after the 2026-08-21 deadline, by owner decision on 2026-08-18.** The reason is disclosure timing, not concealment: the approach is novel and the owner does not want it copied inside the submission window. Judging happens after the deadline, so the code is readable when it is read. Anyone following the link before then gets a 404, which is an accepted cost. |
| Submission venue | **OKX AI Season Hackathon**, X Layer. Google Form, deadline **2026-08-21 23:59 UTC**. |
| Contact | `unknown` — not recorded in the tracked tree. |

## Competition requirements, checked against the tree on 2026-08-18

| Requirement | State |
|---|---|
| AI in the product design | **Met.** A model compiles issuer disclosures into controls citing byte-exact spans. It never runs in the serving path, which is the point — the daily result is deterministic. |
| Deployed on X Layer testnet | **Met.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, chain 1952, one published report at block 38526525. |
| Launched on X Layer mainnet | **NOT MET.** `deployments/xlayer-mainnet.template.json` holds placeholder addresses. Mainnet is reachable (chain 196) and gas is 0.02 gwei, so registry + `AssetGate` + one report costs about 0.000073 OKB — a fraction of a cent. All three role addresses hold 0 OKB at nonce 0, so the requirement is one small transfer and one deploy run, not an engineering problem. |
| Dedicated X account, kept active | **Account created:** `@touch__stone`. "Kept active" is a continuing obligation, not a one-time step. |
| Post mentioning @XLayerOfficial | **NOT MET.** The account now exists, so this is unblocked. |
| Google Form by 2026-08-21 23:59 UTC | **NOT SUBMITTED.** The owner holds the form. Nothing is submitted without explicit approval. |

Touchstone does not issue assets, custody funds, recommend investments,
assign credit ratings, or claim facts beyond the evidence class it has
actually verified.

---

## 2. What was built

A single USTB vertical, local and on a testnet registry carrying one
published report: sequence 1, state `UNVERIFIABLE`, 2026-08-17.

**Evidence and controls.** Source manifests for USTB, USDY and FOBXX.
Golden fixtures where retrieval was bounded. Five approved controls,
each a candidate a model proposed from Superstate's own bytes and bound
by digest to the compilation artifact that accepted it
(`data/compilations/APPROVALS.json`). Five further candidates passed the
deterministic gates and were declined anyway, with reasons — three as
duplicate presence checks on a document an approved control already
reads, one at zero confidence margin. That is below the ≥6 target in
`ROADMAP.md`, and `LIMITATIONS.md` records it as missed rather than
padding the set to meet it. The compiler has no tool surface. Approval
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
| ≥2 fully autonomous **live** adapters | **Missed — one, not zero.** USTB ran the unattended daemon against the live issuer on 2026-08-17 and published sequence 1. This row said "zero proven live" until 2026-08-18, contradicting both `LIMITATIONS.md` and the published report. One live slot is also not continuous operation. USDY's daily page is bounded and measured but has no approved control; FOBXX has a live bounded SEC N-MFP3 regulator route, monthly; OUSG is the ruled next adapter. |
| One live consumer contract gating on state | **Missed.** `AssetGate` is `not_deployed` on any persistent chain. |
| One production canary epoch | **Met on testnet**, 2026-08-17: USTB sequence 1, block 38526525, state `UNVERIFIABLE`. Unmet for mainnet, which is unscheduled. |
| Living dossier and developer surface | **Shipped 2026-08-18.** Live at https://touchstone.gudman.xyz — 20 pages, zero JavaScript, an offline verifier, a coverage page, and 1,803 lines of the repository's documentation. |
| Public demo | **`not_deployed`.** The two-act script in `ROADMAP.md` cannot be walked as written. See `docs/DEMO-RUNBOOK.md`. |

Phase 1 ships one USTB vertical. The two-adapter and production-canary
metrics were not retargeted.


## What the contract does not do

Recorded here because it was found in review and was written down nowhere else, and because a
submission that omits it would be claiming a guarantee the code does not provide.

**`TouchstoneRegistry` does not verify the Ed25519 report signature.** The contract contains no
signature check of any kind — no `ecrecover`, no Ed25519 verification. It accepts a status and
the roots from any address the owner has authorised as a publisher. The Ed25519 signature is
real and is what the offline bundle verifies, but on chain the guarantee is narrower than it
looks: **an authorised address asserted this commitment**, not *this report was signed by the
reporting key*.

The honest description of the registry is therefore append-only publication integrity with
bounded authority — publisher lineage is preserved across rotation, sequence replay is refused,
epochs cannot be double-published, and history cannot be rewritten. It is not on-chain
attestation and must not be described as trustless.

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
| Published report / explorer tx | USTB sequence 1, block 38526525, tx `0x5107140c5c9c755026de5e3193e14b9863aacc2962f78b8516bf00075be6b869`, state `UNVERIFIABLE`. X Layer testnet |
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
| "Live on X Layer" | A testnet registry is deployed and holds one published report. Testnet only; mainnet is unscheduled. | `docs/OPERATIONS.md`, `docs/DEPLOYMENT-G1-EXECUTED.md` |
| "Two live adapters" | One adapter, one live run. Not two, and not continuous. | `ROADMAP.md` completion table |
| "Consumer contract in production" | `AssetGate` is `not_deployed` on a persistent chain. | `scripts/e2e_local.py`; no address in `deployments/` |
| "Autonomous canary" | Prepared, not executed. | `docs/CANARY-G1B.md` |
| "Public dossier" | Live since 2026-08-18 at touchstone.gudman.xyz. | The site itself; `docs/DEPLOY-T9.md` |
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
