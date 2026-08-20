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
| Public website | **LIVE** — https://touchstone.gudman.xyz — 26 routes, self-hosted fonts, no external JavaScript except the vendored ethers bundle on `/app` |
| Public dossier | **LIVE** — https://touchstone.gudman.xyz/dossier/ustb-2026-08-17 — the published report in full, every control and digest |
| Demo URL | **LIVE** — https://touchstone.gudman.xyz/judge — retained replay and refusal path; the narrated replacement film is not uploaded |
| Documentation site | **LIVE** — https://touchstone.gudman.xyz/docs — the project's committed documentation rendered in full |
| X / social handles | **`@touch__stone`** — created by the owner 2026-08-18. The owner reports the @XLayerOfficial post as published; it is not machine-verifiable from this repository, and its URL belongs in the submission form. The rules require the account be *kept active*, which cannot be backdated. |
| Domain | `gudman.xyz` subdomain, TLS via Let's Encrypt, certificate to 2026-11-16 |
| Repository | `github.com/Ridwannurudeen/touchstone` — **PUBLIC since 2026-08-16**, per GitHub's own PublicEvent record (2026-08-16T14:10:51Z, no later visibility change), with `main` protected by the CI aggregate check (protection verified via the API on 2026-08-20). The private-until-deadline plan was superseded by the owner's decision to open it early. |
| Submission venue | **OKX AI Season Hackathon**, X Layer. Google Form, deadline **2026-08-21 23:59 UTC**. |
| Contact | `unknown` — not recorded in the tracked tree. |

## Competition requirements, checked against the tree on 2026-08-20

| Requirement | State |
|---|---|
| AI in the product design | **Met.** A model compiles issuer disclosures into controls citing byte-exact spans. It never runs in the serving path, which is the point — the daily result is deterministic. |
| Deployed on X Layer testnet | **Met.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, chain 1952, USTB sequences 1–4 (sequence 4 is `CONFIRMED`), plus both policy keys at sequence 1 with Registry v2 attestations on `0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720`. The legacy `AssetGate` at `0xAac48DC261B04737FDCB101D5049395121034a83` and a freshness-pinned gate at `0x0bc5c0cc879CE1b5AD23aEdA8fC42dB414eB8eE1` are live there. |
| Launched on X Layer mainnet | **MET, 2026-08-18.** Registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, chain 196, deployed at block 68291416 under a recorded owner approval; manifest `deployments/xlayer-mainnet.json`, `deployment_state: active`. USTB sequences 1–4 (sequence 4 is `CONFIRMED`, published unattended on 2026-08-20), both policy keys at sequence 2 with Registry v2 attestations on `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` (chain 196). ⚠️ Both registry addresses recur across chains — same deployer, aligned nonces — so the chain id is the only thing that identifies a deployment. `AssetGateV2` is live on mainnet at `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` (block 68427105), pinned to the approved policy, control-set root and signed approval-ledger digest; `RWAAdmissionController` at `0x5C5265392701A99cbB137aF8116E0F97f630329A` consumes it with permit and refusal transactions on chain. |
| Dedicated X account, kept active | **Account created:** `@touch__stone`. "Kept active" is a continuing obligation, not a one-time step. |
| Post mentioning @XLayerOfficial | **Owner reports this as done.** Not machine-verifiable from this repo, and the post URL is not recorded here — it belongs in the submission form field, not in a document that cannot check it. |
| Google Form by 2026-08-21 23:59 UTC | **NOT SUBMITTED.** The owner holds the form. Nothing is submitted without explicit approval. |

Touchstone does not issue assets, custody funds, recommend investments,
assign credit ratings, or claim facts beyond the evidence class it has
actually verified.

---

## 2. What was built

A single USTB vertical across testnet and mainnet: 14 published reports
across both chains, 9 of them `CONFIRMED`, spanning the asset key and two
policy keys, with six Registry v2 attestations and six enforcement
transactions. The first `CONFIRMED` state landed 2026-08-19; the
2026-08-20 mainnet slot was published unattended by the production host.

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
live on testnet. `TouchstoneRegistryV2`: policy-aware reports carrying
the signed approval-ledger digest; deployed on both chains and holding
attestations for both policies. `AssetGateV2`: additionally pins exact
policy identity, policy root and approval digest at construction; live
on mainnet at `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059`.
`GuardedAction` permit/refuse pairs and the `RWAAdmissionController`
admission sequence (propose, activate on the gate's word, execute, and a
refused activation left on chain) are real transactions on both their
networks.

**Operations code.** Unattended daemon (`scripts/run_service.py`),
append-only incidents, heartbeat, watchdog, one HTTPS alert webhook,
gas runway from measured costs, encrypted backup and a restore that
verifies into a fresh directory. The production observer, status timer
and publisher unit are all active on the shared VPS; the publisher was
enabled 2026-08-20 under the owner's release of that gate, and its first
unattended slot published the mainnet asset and both policy reports,
all `CONFIRMED`.

**Testnet.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on
X Layer testnet (chain 1952), deployed 2026-08-17 at block 38489602
under a recorded owner approval. Publisher
`0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` authorised. Holds USTB
sequences 1–4 plus both policy keys at sequence 1; Registry v2 at
`0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720` holds their attestations.
Predecessor `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` is
superseded and must not be published to.

**CI.** `.github/workflows/ci.yml` runs ruff, pytest on Python 3.11 and
3.12, Hardhat, a managed local-chain E2E, a public-truth gate against
the canonical project state, and a mutation harness. The workflow is
given no project secret. The repository is public and `main` requires
the aggregate `required` check. The verified local result at this
revision is 1,978 passed / 1 skipped, 111 contract tests, 15 SDK tests,
and 125/125 mutants killed.

**Release builder.** `scripts/build_release.py` writes an unsigned JSON
document from the tree and from caller-supplied test counts. It does
not invent counts and does not read the clock.

---

## 3. What was missed

Stated as misses, not as near-misses.

| Target (`ROADMAP.md`) | Result |
|---|---|
| ≥2 fully autonomous **live** adapters | **Missed — one, not zero.** USTB runs the unattended daemon against the live issuer; since 2026-08-20 the production host publishes its daily slot on its own. Still one asset, not two. USDY's daily page is bounded and measured but has no approved control; FOBXX has a live bounded SEC N-MFP3 regulator route, monthly; OUSG is the ruled next adapter. |
| One live consumer contract gating on state | **Met on both chains.** Testnet `AssetGate` at `0xAac48DC261B04737FDCB101D5049395121034a83` (2026-08-18) refused USTB while it was `UNVERIFIABLE` and the freshness-pinned gates flipped to `(true, "allowed")` when confirmation landed. Mainnet `AssetGateV2` at `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` (2026-08-20) pins policy, control-set root and approval digest, and `RWAAdmissionController` consumes it on chain. No third party consumes either — both consumers are this project's own contracts. |
| One production canary epoch | **Met on both chains.** Testnet 2026-08-17 (sequence 1, block 38526525) and mainnet 2026-08-18 (sequence 1, block 68292878), both honestly `UNVERIFIABLE` on an unseeded workspace; `CONFIRMED` reached on both chains 2026-08-19 once a qualifying 24-hour-old capture existed. |
| Living dossier and developer surface | **Shipped 2026-08-18, rebuilt 2026-08-20.** Live at https://touchstone.gudman.xyz — 26 routes, zero external JavaScript except the vendored ethers bundle on `/app`, an offline verifier, `/judge`, a live Policy Terminal, and the repository's documentation rendered in full. |
| Public demo | **Live** at https://touchstone.gudman.xyz. `/app` walks gate checks and the admission action against both live chains; the narrated film is still not uploaded. |

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
| X Layer testnet (1952) registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` — ⚠️ the same address is the *Registry v2* on chain 196; pin the chain id |
| Testnet Registry v2 | `0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720` |
| Testnet publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` |
| Testnet `AssetGate` (legacy) / freshness gate | `0xAac48DC261B04737FDCB101D5049395121034a83` / `0x0bc5c0cc879CE1b5AD23aEdA8fC42dB414eB8eE1` |
| X Layer mainnet (196) registry | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` — ⚠️ the same address is a *superseded* registry on chain 1952; pin the chain id |
| Mainnet Registry v2 | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` (chain 196, block 68389940) |
| Mainnet publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` (same identity as testnet) |
| Mainnet `AssetGateV2` | `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` — block 68427105, pins policy id, policy root, control-set root and the signed approval-ledger digest |
| Mainnet `RWAAdmissionController` | `0x5C5265392701A99cbB137aF8116E0F97f630329A` — block 68427148; propose, activate, execute and a refused activation are all on-chain transactions |
| Hero asset (off-chain identity) | USTB `eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e` |
| Published reports | 14 across both chains, 9 `CONFIRMED`. First: USTB sequence 1, block 38526525, tx `0x5107140c…5be6b869`, X Layer testnet. Latest on each chain: asset sequence 4, `CONFIRMED` — the mainnet one published unattended 2026-08-20. Full per-report table with transactions: https://touchstone.gudman.xyz/dossier |
| Verification API | `not_deployed` — verification is offline and in-browser, by design |
| Status page | https://touchstone.gudman.xyz/status — regenerated every five minutes from the observer's log; states its own generation time and that it may be stale |

Mainnet publication is scheduled: the production host's publisher unit
has run its daily slot unattended since 2026-08-20. The custody
deviation stands — the deployer and publisher keys still do not sit on
separate hosts, and the publisher's key now also lives on the shared
production host, root-owned and never readable by the service account.

---

## 5. Demo, as it would be described if asked

The specified demo is two acts, 90–120 seconds, in `ROADMAP.md`. `/judge`
provides the retained replay and refusal path; `/app` walks the live
gates and the admission action against both chains. The policy-bound
publications exist (both policies, both chains, `CONFIRMED`), and the
permitted/refused pair is on mainnet as real transactions. What does not
exist is the narrated film — an 89-second silent cut is on file.

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
| "Live on X Layer" | Registries v1 and v2 are live on both chains: 14 reports, 9 `CONFIRMED`. Live and verified-against-evidence are both accurate; "trustless" is not. | `docs/OPERATIONS.md`, https://touchstone.gudman.xyz/dossier |
| "Two live adapters" | One adapter. Not two assets. Its daily slot has run unattended on the production host since 2026-08-20 — exactly one day of that record exists, not a track record. | `ROADMAP.md` completion table |
| "Consumer contract in production" | `AssetGateV2` and `RWAAdmissionController` are live on **mainnet** with permit and refusal transactions; gates are live on testnet. No third party consumes them — every consumer is this project's own contract. | `contracts/scripts/deploy_gate.js`, `deploy_admission.js` |
| "Autonomous canary" | The 2026-08-20 mainnet slot was published unattended by the production host. Its first slot failed closed on a parse timeout the loaded host could not meet; the timeout was corrected and the next slot published on its own. One unattended day is proof of the path, not of continuity. | `docs/OPERATIONS.md` |
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

- Demo video URL — `not_deployed` until the narrated cut is uploaded. The
  public repository URL, by contrast, exists: https://github.com/Ridwannurudeen/touchstone

---

## 9. Owner action still required

This file does not submit. A real filing needs a fresh explicit owner
approval in the then-current context (product principle 8,
`ROADMAP.md`; gate G5). It also needs a venue. This repository does
not name one.

Do not send this draft. Do not paste it into a form. Do not imply that
because the draft exists, the misses above have closed.
