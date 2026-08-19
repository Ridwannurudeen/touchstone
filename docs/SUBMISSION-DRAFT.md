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
| Public website | **LIVE** — https://touchstone.gudman.xyz — 22 pages, zero external JavaScript, self-hosted fonts |
| Public dossier | **LIVE** — https://touchstone.gudman.xyz/dossier/ustb-2026-08-17 — the published report in full, every control and digest |
| Demo URL | **LIVE** — https://touchstone.gudman.xyz/judge — retained replay and refusal path; the narrated replacement film is not uploaded |
| Documentation site | **LIVE** — https://touchstone.gudman.xyz/docs — 2,009 lines of the project's committed documentation |
| X / social handles | **`@touch__stone`** — created by the owner 2026-08-18. The owner reports the @XLayerOfficial post as published; it is not machine-verifiable from this repository, and its URL belongs in the submission form. The rules require the account be *kept active*, which cannot be backdated. |
| Domain | `gudman.xyz` subdomain, TLS via Let's Encrypt, certificate to 2026-11-16 |
| Repository | `github.com/Ridwannurudeen/touchstone` — **private until after the 2026-08-21 deadline, by owner decision on 2026-08-18.** The reason is disclosure timing, not concealment: the approach is novel and the owner does not want it copied inside the submission window. Judging happens after the deadline, so the code is readable when it is read. Anyone following the link before then gets a 404, which is an accepted cost. |
| Submission venue | **OKX AI Season Hackathon**, X Layer. Google Form, deadline **2026-08-21 23:59 UTC**. |
| Contact | `unknown` — not recorded in the tracked tree. |

## Competition requirements, checked against the tree on 2026-08-18

| Requirement | State |
|---|---|
| AI in the product design | **Met.** A model compiles issuer disclosures into controls citing byte-exact spans. It never runs in the serving path, which is the point — the daily result is deterministic. |
| Deployed on X Layer testnet | **Met.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, chain 1952, three published reports (sequences 1–3; sequence 3 is a correction). `AssetGate` is also live there at `0xAac48DC261B04737FDCB101D5049395121034a83` and correctly refuses USTB. |
| Launched on X Layer mainnet | **MET, 2026-08-18.** Registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, chain 196, deployed at block 68291416 under a recorded owner approval; manifest `deployments/xlayer-mainnet.json`, `deployment_state: active`. Two published reports (sequence 1, and sequence 2 correcting it). ⚠️ That address is *also* a superseded registry on chain 1952 — same deployer, nonce 0 — so the chain id is the only thing that identifies this deployment. `AssetGate` is deliberately **not** on mainnet: `requiredControlSetRoot` is immutable and the approved control set is still moving. |
| Dedicated X account, kept active | **Account created:** `@touch__stone`. "Kept active" is a continuing obligation, not a one-time step. |
| Post mentioning @XLayerOfficial | **Owner reports this as done.** Not machine-verifiable from this repo, and the post URL is not recorded here — it belongs in the submission form field, not in a document that cannot check it. |
| Google Form by 2026-08-21 23:59 UTC | **NOT SUBMITTED.** The owner holds the form. Nothing is submitted without explicit approval. |

Touchstone does not issue assets, custody funds, recommend investments,
assign credit ratings, or claim facts beyond the evidence class it has
actually verified.

---

## 2. What was built

A single USTB vertical across testnet and mainnet, carrying five legacy
v1 reports: testnet sequences 1–3 and mainnet sequences 1–2. The latest
report on each chain is `UNVERIFIABLE`.

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
artifacts and the approval ledger). The registry entry is a
publisher-authenticated onchain commitment; the signed report and bundle
are the offline-verifiable artifacts. Locally signed raw transactions. A
deployment manifest pins chain id, registry address, runtime bytecode
digest, publisher lineage and confirmation depth. Preflight runs in
full immediately before signing.

**Contracts.** `TouchstoneRegistry`: append-only reports, per-epoch
uniqueness (`epochKey` / `epochSequence`), corrections that must name
the epoch they correct, publisher authorise / revoke / rotate,
immutable owner and expected chain id. No custody, no payable, no
token, no proxy, no `delegatecall`, no `selfdestruct`.
`AssetGate`: freshness and publisher checks against the latest report;
live on testnet and currently refusing USTB. `GuardedAction` and
`TouchstoneRegistryV2` are built and tested locally but are not deployed.

**Operations code.** Unattended daemon (`scripts/run_service.py`),
append-only incidents, heartbeat, watchdog, one HTTPS alert webhook,
gas runway from measured costs, encrypted backup and a restore that
verifies into a fresh directory. The production observer and status
timer are active on the shared VPS; the publisher unit remains disabled.

**Testnet.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on
X Layer testnet (chain 1952), deployed 2026-08-17 at block 38489602
under a recorded owner approval. Publisher
`0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` authorised. Holds three
reports. Predecessor `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` is
superseded and must not be published to.

**CI.** `.github/workflows/ci.yml` runs ruff, pytest on Python 3.11 and
3.12, Hardhat, a managed local-
chain E2E, and a mutation harness. The workflow is given no project
secret. Branch protection that would make the aggregate job mandatory
is `unknown` from this tree; the workflow file itself says that on a
private repository without GitHub Pro the protection APIs refuse.
The verified local result is 1,877 passed / 1 skipped, 82 contract tests,
and 125/125 mutants killed; a public Actions run remains open.

**Release builder.** `scripts/build_release.py` writes an unsigned JSON
document from the tree and from caller-supplied test counts. It does
not invent counts and does not read the clock.

---

## 3. What was missed

Stated as misses, not as near-misses.

| Target (`ROADMAP.md`) | Result |
|---|---|
| ≥2 fully autonomous **live** adapters | **Missed — one, not zero.** USTB ran the unattended daemon against the live issuer on 2026-08-17 and published sequence 1. This row said "zero proven live" until 2026-08-18, contradicting both `LIMITATIONS.md` and the published report. One live slot is also not continuous operation. USDY's daily page is bounded and measured but has no approved control; FOBXX has a live bounded SEC N-MFP3 regulator route, monthly; OUSG is the ruled next adapter. |
| One live consumer contract gating on state | **Met on testnet**, 2026-08-18. `AssetGate` at `0xAac48DC261B04737FDCB101D5049395121034a83` returns `(false, "status not allowed")` for USTB — it refuses, which is the correct behaviour against an `UNVERIFIABLE` report. Not on mainnet, and no third party consumes it. |
| One production canary epoch | **Met on both chains.** Testnet 2026-08-17 (sequence 1, block 38526525) and mainnet 2026-08-18 (sequence 1, block 68292878). Both `UNVERIFIABLE`. |
| Living dossier and developer surface | **Shipped 2026-08-18.** Live at https://touchstone.gudman.xyz — 22 pages, zero external JavaScript, an offline verifier, `/judge`, a coverage page, and 2,009 lines of the repository's documentation. |
| Public demo | **Live** at https://touchstone.gudman.xyz. The two-act script in `ROADMAP.md` still cannot be walked as written; see `docs/DEMO-RUNBOOK.md`. |

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
| Testnet `AssetGate` | `0xAac48DC261B04737FDCB101D5049395121034a83` |
| X Layer mainnet (196) registry | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` — ⚠️ the same address is a *superseded* registry on chain 1952; pin the chain id |
| Mainnet publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` (same identity as testnet) |
| Mainnet `AssetGate` | `not_deployed` — deliberately; `requiredControlSetRoot` is immutable and the approved set is still moving |
| Hero asset (off-chain identity) | USTB `eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e` |
| Published report / explorer tx | Five reports, all `UNVERIFIABLE`. First: USTB sequence 1, block 38526525, tx `0x5107140c…5be6b869`, X Layer testnet. Latest testnet: sequence 3 (a correction), block 38617112. Latest mainnet: sequence 2 (a correction), block 68307118 |
| Verification API | `not_deployed` |
| Status page | https://touchstone.gudman.xyz/status — regenerated every five minutes from the observer's log; states its own generation time and that it may be stale |

Further mainnet publication is unscheduled. The registry exists, but the
publisher is disabled and the deployer and publisher keys do not sit on
separate hosts.

---

## 5. Demo, as it would be described if asked

The specified demo is two acts, 90–120 seconds, in `ROADMAP.md`. `/judge`
now provides the retained replay and refusal path, and a legacy
`AssetGate` is live on testnet. The live policy-bound publication,
permitted/refused mainnet pair and narrated replacement film do not exist.

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
| "Live on X Layer" | Registries are live on testnet (3 reports) and mainnet (2 reports), all `UNVERIFIABLE`. Live is accurate; "verified" is not. | `docs/OPERATIONS.md`, `docs/DEPLOYMENT-G1-EXECUTED.md` |
| "Two live adapters" | One adapter, several live runs. Not two assets, and **not continuous** — every run was hand-started. | `ROADMAP.md` completion table |
| "Consumer contract in production" | `AssetGate` is live on **testnet** at `0xAac48DC2…`, and refuses USTB. It is `not_deployed` on mainnet, and no third party consumes it. | `contracts/scripts/deploy_gate.js` |
| "Autonomous canary" | Five reports were published, but every publication was hand-started; unattended publication is not proven. | `docs/OPERATIONS.md` |
| "Public dossier" | Live since 2026-08-18 at touchstone.gudman.xyz. | The site itself; `docs/DEPLOY-T9.md` |
| "Independently attested" | Not claimed. Issuer API is issuer disclosure. | `manifests/sources/ustb.json` `authority_class: issuer-api` |
| "Regulatory-grade" / "safe" / "solvent" | Banned. Not claimed. | `ROADMAP.md` ambition-theatre list; `docs/LIMITATIONS.md` |
| "Users" / "volume" / "TVL" | None. No product users are recorded. | No such metric exists in the tree |
| "OKX / issuer partner" | Not claimed. Endorsement would need written confirmation. | `ROADMAP.md` |
| "Name is clear" | Two live crypto projects use Touchstone; counsel opinion is not on file. | `docs/BRAND-CLEARANCE.md` |
| "Signed release" | The builder emits unsigned JSON. | `scripts/build_release.py` |
| "Production host" | Observer and status timer active; publisher installed but disabled on the same shared VPS. | `docs/OPERATIONS.md` |

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

Prepared since this list was written:

- Live explorer link to a publication — testnet tx `0x5107140c…5be6b869` (block 38526525) and
  mainnet tx `0xfa4b7992…6bef0b85` (block 68292878)
- Mainnet addresses — registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` on chain 196,
  publisher `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710`
- Demo video — an 89-second silent cut exists; **no public URL yet**, so the field below stays
  unfilled rather than pointing at a local file

Not prepared, and not to be invented:

- Public repository URL — `not_deployed` until the owner makes the repository public
- Demo video URL — `not_deployed` until the narrated cut is uploaded

---

## 9. Owner action still required

This file does not submit. A real filing needs a fresh explicit owner
approval in the then-current context (product principle 8,
`ROADMAP.md`; gate G5). It also needs a venue. This repository does
not name one.

Do not send this draft. Do not paste it into a form. Do not imply that
because the draft exists, the misses above have closed.
