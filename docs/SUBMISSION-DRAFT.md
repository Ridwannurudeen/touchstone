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
| One sentence | Touchstone turns cited RWA evidence into deterministic policy decisions enforced today by X Layer contracts; wallet and AI-agent enforcement integrations are planned, not yet demonstrated. |
| Public website | **LIVE** — https://touchstone.gudman.xyz — 26 routes, self-hosted fonts, no external JavaScript except the vendored ethers bundle on `/app` |
| Public dossier | **LIVE** — https://touchstone.gudman.xyz/dossier/ustb-2026-08-17 — the published report in full, every control and digest |
| Demo URL | **LIVE** — https://touchstone.gudman.xyz/judge — retained replay and refusal path; the narrated replacement film is not uploaded |
| Documentation site | **LIVE** — https://touchstone.gudman.xyz/docs — the project's committed documentation rendered in full |
| X / social handles | **`@touch__stone`** — Premium account. Launch post published 2026-08-21 and retained at https://x.com/TOUCH__STONE/status/2090844839055159485; it mentions `@XLayerOfficial`. Keeping the account active remains a continuing obligation. |
| Domain | `gudman.xyz` subdomain, TLS via Let's Encrypt, certificate to 2026-11-16 |
| Repository | `github.com/Ridwannurudeen/touchstone` — **PUBLIC since 2026-08-16**, per GitHub's own PublicEvent record (2026-08-16T14:10:51Z, no later visibility change), with `main` protected by the CI aggregate check (protection verified via the API on 2026-08-20). The private-until-deadline plan was superseded by the owner's decision to open it early. |
| Submission venue | **OKX AI Season Hackathon**, X Layer. Google Form, deadline **2026-08-21 23:59 UTC**. |
| Contact | `unknown` — not recorded in the tracked tree. |

## Competition requirements, checked against the tree on 2026-08-20

| Requirement | State |
|---|---|
| AI in the product design | **Met.** A model compiles issuer disclosures into controls citing byte-exact spans. It never runs in the serving path, which is the point — the daily result is deterministic. |
| Deployed on X Layer testnet | **Met.** Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, chain 1952, USTB sequences 1–5 (sequence 5 is `CONFIRMED`), plus both policy keys at sequence 2 with Registry v2 attestations on `0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720`. `AssetGateV2` at `0xE1e2C897A43674bba6c3fbE6584a703a09939930` pins the approved policy, control-set root and signed approval digest and answered `(true, "allowed")` on deployment day; `RWAAdmissionController` at `0x1822Cde72cD1aB560d8fdD795Ac6971b122BbA28` consumes it with propose, activate, execute and a refused activation on chain. The legacy `AssetGate` and the freshness-pinned gate remain live beside them. |
| Launched on X Layer mainnet | **MET, 2026-08-18.** Registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, chain 196, deployed at block 68291416 under a recorded owner approval; manifest `deployments/xlayer-mainnet.json`, `deployment_state: active`. USTB sequences 1–5 (sequence 5 is `CONFIRMED`, published unattended on 2026-08-21), both policy keys at sequence 3 with Registry v2 attestations on `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` (chain 196). ⚠️ Both registry addresses recur across chains — same deployer, aligned nonces — so the chain id is the only thing that identifies a deployment. `AssetGateV2` is live on mainnet at `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` (block 68427105), pinned to the approved policy, control-set root and signed approval-ledger digest; two independent RPCs returned `allowed` after the sequence-3 attestations. `RWAAdmissionController` at `0x5C5265392701A99cbB137aF8116E0F97f630329A` consumes the gate with permit and refusal transactions on chain. |
| Dedicated X account, kept active | **Account created:** `@touch__stone`. "Kept active" is a continuing obligation, not a one-time step. |
| Post mentioning @XLayerOfficial | **PUBLISHED.** https://x.com/TOUCH__STONE/status/2090844839055159485 — the exact URL is retained for the form. |
| Google Form by 2026-08-21 23:59 UTC | **NOT SUBMITTED.** The owner holds the form. Nothing is submitted without explicit approval. |

Touchstone does not issue assets, custody funds, recommend investments,
assign credit ratings, or claim facts beyond the evidence class it has
actually verified.

---

## 2. What was built

A single USTB vertical across testnet and mainnet: 20 published reports
across both chains, 15 of them `CONFIRMED`, spanning the asset key and two
policy keys, with ten Registry v2 attestations and eight enforcement
transactions. The first `CONFIRMED` state landed 2026-08-19; the
2026-08-20 and 2026-08-21 mainnet slots were published unattended by the production host.

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
sequences 1–5 plus both policy keys at sequence 2; Registry v2 at
`0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720` holds their attestations.
Predecessor `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` is
superseded and must not be published to.

**CI.** `.github/workflows/ci.yml` runs ruff, pytest on Python 3.11 and
3.12, Hardhat, a managed local-chain E2E, a public-truth gate against
the canonical project state, and a mutation harness. The workflow is
given no project secret. The repository is public and `main` requires
the aggregate `required` check. The verified local result at this
revision is 1,990 passed / 1 skipped, 111 contract tests, 15 SDK tests,
and 125/125 mutants killed.

**Signed release.** [`v0.1.0`](https://github.com/Ridwannurudeen/touchstone/releases/tag/v0.1.0)
binds clean commit `c6908f00058c44f57251ca1dab446cbc16300ce6`, deterministic source and
project-state artifacts, and successful CI run `32517792979`. The release set is Ed25519-signed
by the active reporter identity recorded in the mainnet deployment manifest. The release was
re-downloaded after publication; all checksums and the signature verified. The annotated Git
tag itself is not cryptographically signed, and the reporter signature is not an independent
security audit.

**External integration proposal.** [Blvck Protocol PR #1](https://github.com/anyathebrand-prog/blvck_protocol/pull/1)
adds optional ingestion of Touchstone bundles through Blvck's content-addressed source path.
It requires an independently supplied reporter key and rejects tampering, attacker-controlled
self-signed bundles, unsupported versions, and expired reports. Its package suite passed 85
tests and a live retained USTB bundle verified. The PR is open: this is public integration
proof, not adoption, endorsement, a partnership, or a deployed third-party consumer.

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
| Published reports | 20 across both chains, 15 `CONFIRMED`. First: USTB sequence 1, block 38526525, tx `0x5107140c…5be6b869`, X Layer testnet. Latest mainnet asset report: sequence 5, `CONFIRMED`, published unattended 2026-08-21. Full per-report table with transactions: https://touchstone.gudman.xyz/dossier |
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
| "Live on X Layer" | Registries v1 and v2 are live on both chains: 20 reports, 15 `CONFIRMED`. Live and verified-against-evidence are both accurate; "trustless" is not. | `docs/OPERATIONS.md`, https://touchstone.gudman.xyz/dossier |
| "Two live adapters" | One adapter. Not two assets. Its daily slot has run unattended on the production host since 2026-08-20 — two daily slots are recorded, not a track record. | `ROADMAP.md` completion table |
| "Consumer contract in production" | `AssetGateV2` and `RWAAdmissionController` are live on **mainnet** with permit and refusal transactions; gates are live on testnet. No third party consumes them — every consumer is this project's own contract. | `contracts/scripts/deploy_gate.js`, `deploy_admission.js` |
| "Autonomous canary" | The 2026-08-20 and 2026-08-21 mainnet slots were published unattended by the production host. The first attempt failed closed on a parse timeout the loaded host could not meet; the timeout was corrected and both recorded daily slots then published on their own. Two unattended days prove the path, not sustained continuity. | `docs/OPERATIONS.md` |
| "Public dossier" | Live since 2026-08-18 at touchstone.gudman.xyz. | The site itself; `docs/DEPLOY-T9.md` |
| "Independently attested" | Not claimed. Issuer API is issuer disclosure. | `manifests/sources/ustb.json` `authority_class: issuer-api` |
| "Regulatory-grade" / "safe" / "solvent" | Banned. Not claimed. | `ROADMAP.md` ambition-theatre list; `docs/LIMITATIONS.md` |
| "Users" / "volume" / "TVL" | None. No product users are recorded. | No such metric exists in the tree |
| "OKX / issuer partner" | Not claimed. Endorsement would need written confirmation. | `ROADMAP.md` |
| "Name is clear" | Two live crypto projects use Touchstone; counsel opinion is not on file. | `docs/BRAND-CLEARANCE.md` |
| "Signed release" | `v0.1.0` has a reporter-signed release set binding its manifest, project state, source archive, exact commit and successful CI run. The tag is annotated rather than cryptographically signed, and no independent audit is implied. | https://github.com/Ridwannurudeen/touchstone/releases/tag/v0.1.0 |
| "Production host" | Observer and status timer active; publisher enabled on the same shared VPS since 2026-08-20, with two unattended daily slots recorded. That proves the path, not a reliability window. | `docs/OPERATIONS.md` |

---

## 8. Attachments a real submission would still need

Prepared in the repository, not attached to anything:

- `docs/LIMITATIONS.md`
- `docs/DEMO-RUNBOOK.md`
- `docs/RELEASE-RUNBOOK.md`
- `docs/CANARY-G1B.md` (historical canary runbook; testnet and mainnet canaries were executed under separate owner approvals)
- `AI_USAGE.md`
- Reporter-signed release `v0.1.0`, including the release manifest, bound release set,
  Ed25519 signature, checksums, deterministic source archive and project state
- Public third-party integration proposal: https://github.com/anyathebrand-prog/blvck_protocol/pull/1

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
`ROADMAP.md`; gate G5). The named venue is the **OKX AI Season Hackathon
X Layer Google Form**, with the recorded deadline **2026-08-21 23:59
UTC**. The form receipt and final submitted text remain unknown because
no submission has been made from this repository.

Do not send this draft. Do not paste it into a form. Do not imply that
because the draft exists, the misses above have closed.
