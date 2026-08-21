# Demo runbook

> **Historical runbook — superseded after the 2026-08-20 mainnet release.** This file
> preserves the original two-act rehearsal and the gaps that existed when it was written.
> It is not the current demo specification: confirmed policy publications, mainnet
> `AssetGateV2`, and the admission controller now exist. Use `/judge`, `/app`,
> `site2/_data/facts.json`, and `docs/SUBMISSION-DRAFT.md` for the current evidence-backed
> walkthrough. Historical “cannot be performed yet” statements below are retained only as
> dated record.

**Unpublished text in a file.** This is not a performance of the demo, not
a public page, and not authorisation to publish, deploy, or submit. No
step below that touches a public chain has been run from this document.

The two-act shape is taken from `ROADMAP.md`, "The minimum hero demo".
What is pre-staged and what is never pre-staged is copied from there
verbatim, because collapsing that distinction is how a rehearsal becomes
a fabricated one.

Several acts in that script cannot be performed yet. They are named in
§4 rather than walked through as if the UI existed.

---

## 1. The script, as specified

Ninety to one hundred and twenty seconds, two acts. Hero asset: USTB,
`eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e`
(`manifests/sources/ustb.json`).

**Act 1 — AI makes disclosure executable.** Judge opens the hero dossier
(no wallet); sees source, evidence class, limitations; runs "Compile
disclosure" on the labeled testnet demo path; Touchstone fetches the
official source; AI extracts one bounded commitment into a typed
control; the UI highlights the exact supporting span; deterministic
validation confirms span-in-artifact, schema, source identity, operator,
confidence; the accepted control root publishes to testnet. If the
compiler cannot support the control, **it abstains live — never
hidden**.

**Act 2 — current evidence changes contract behaviour.** A demo
`AssetGate` shows `REQUIRES_REFRESH` (its freshness requirement is
deliberately stricter than the last observation age); Touchstone
retrieves the current official daily observation; UI shows previous vs
current values (an ordinary NAV movement is never called a risk event);
report signed; transparency log appends; freshness renews on X Layer;
the gate flips to `ACCEPTED`; judge opens the explorer transaction;
judge asks "why did the gate accept this asset?" and the dossier answers
solely from accepted graph nodes. The gate reacts to **verification
freshness**, never declares the asset safe.

**Pre-staged:** deployed contracts, publisher authorization, funded
account, verified adapters, previous genuine evidence snapshot, previous
signed epoch, approved control set, demo consumer, hash-bound cached
fallback artifacts, recorded fallback walkthrough.

**Never pre-staged:** modified issuer evidence, fabricated reports,
synthetic NAV changes presented as real, hidden manual state changes,
self-funded activity described as user activity. If the live source
fails, switch **visibly** to verified historical replay mode and log
the incident.

Those two lists are the rule. A cached fixture may be shown only if the
switch is visible and the incident is logged. A silent fallback is a
fabricated demo.

---

## 2. What is actually in place

| Item | State |
|---|---|
| Approved USTB control set | Present. Eight controls in `data/compilations/APPROVALS.json`, each bound to a compilation digest. |
| Committed fixtures | Present. The 2026-08-13 and 2026-08-14 Superstate captures, plus FOBXX SEC fixtures. Declared in the source manifests. See `fixtures/README.md`. |
| USTB adapter | Built, wired, tested, and **run once against live sources** on 2026-08-17. |
| Testnet registry | Deployed. `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on chain 1952. Holds **three** reports (sequences 1–3, the last a correction), all `UNVERIFIABLE`. A mainnet registry is also live on chain 196. See `docs/OPERATIONS.md`. |
| Publisher authorised and funded | Yes, on both registries. Publication is a separate owner gate (`docs/CANARY-G1B.md`) and **has now been run five times**. |
| Previous signed epoch on a public chain | **Five**, across two chains. Epochs `ustb-2026-08-17` and `ustb-2026-08-18`; every one `UNVERIFIABLE`. |
| Living dossier (PLAN-T9) | **Built and live** at https://touchstone.gudman.xyz — 22 pages, zero external JavaScript. `/judge` is live and uses one local interaction over retained data. |
| `AssetGate` on a persistent chain | **Live on testnet** at `0xAac48DC261B04737FDCB101D5049395121034a83`; `check(USTB)` returns `(false, "status not allowed")`. `not_deployed` on mainnet. |
| Demo URL | https://touchstone.gudman.xyz |
| Host / supervisor | **Configured.** `touchstone-observer@xlayer-mainnet` and the status timer are active on the shared VPS; the publisher unit remains disabled. |

The pre-staged list in the roadmap is still only partly true today. The
approved control set, hash-bound fixtures, two registries, five legacy v1
reports, a refusing testnet `AssetGate`, the dossier and `/judge` exist.
What does not exist is a confirmed report, a live mainnet `GuardedAction`
pass/refuse pair, a live compiler-to-publication interaction, or the
narrated replacement film.

---

## 3. Fallback if a live source fails

The roadmap rule: switch **visibly** to verified historical replay and
log the incident — never silently.

What the code actually does:

- `scripts/run_service.py --fixtures <dir> --fixture-capture <YYYY-MM-DD>`
  serves committed fixtures instead of the issuer. `--fixture-capture`
  is required; there is no default, because the wrong capture rehearses
  a path that cannot publish, and silently.
- That mode is **refused against a public network**. If the manifest is
  not local, the service prints `SERVICE FAIL: fixture mode is a local
  rehearsal; <network> is a public network and must be served from live
  sources` and exits 1 before reading a key. There is no hidden
  fixture path onto testnet or mainnet.
- On a public network, a live-source failure opens an incident and
  **signs nothing and publishes nothing** (`scripts/run_service.py`).
  Silence is recorded as silence.
- The dossier and `/judge` are live, but the service does not silently
  switch either page to fixtures. A failure still has to be stated and
  logged before a local retained replay is shown.

So the honest fallback today, in front of a person, is:

1. Say out loud that the live source failed.
2. Open or record an incident (`SOURCE_UNAVAILABLE` or `EPOCH_FAILED`).
3. If a rehearsal is still wanted, move to the **local** loop in §5,
   on a local Hardhat chain, using the committed fixtures, and say that
   this is verified historical replay on a private chain — not a live
   testnet publication.
4. Never present fixture bytes as a live Superstate response.

---

## 4. What cannot be performed yet, and why

**Act 1 as written still cannot be performed, but less of it than this
section used to claim.** ⚠️ It read "the judge cannot open a hero dossier,
PLAN-T9 is unbuilt" until 2026-08-18, by which point the dossier had been
live for days and the table in §3 above said so — a self-contradiction
inside one document, which is the exact defect this project treats as
disqualifying.

What is true: the dossier **is** live at
https://touchstone.gudman.xyz/dossier/ustb-2026-08-17 and `/judge` now
provides a wallet-free retained replay with policy selection, cited
evidence, approval metadata, gate refusal and an integration snippet.
It does not call the live compiler, publish a new report, or send a
transaction; those steps remain outside the public page.

The compiler itself exists, as a command, not a UI:

```text
python scripts/compile_controls.py
```

It proposes candidates from the committed 2026-08-13 / 2026-08-14
captures by default. `--live` retrieves from the issuer instead. It
prints accepted candidates and stops. **Nothing is approved.** Approval
is a human act recorded in `data/compilations/APPROVALS.json`. Running
this command in front of a judge would show the compiler abstaining or
proposing; it would not publish a control root, and it would call a
model if `TOUCHSTONE_MODEL_ENDPOINT`, `TOUCHSTONE_MODEL_KEY` and
`TOUCHSTONE_MODEL_NAME` are set. Those variables are not documented
here as values. If they are unset the provider refuses to start.

Re-compiling live is not the approved control set. The eight accepted
controls are already bound to the artifacts under `data/compilations/`.
A new compile produces new digests.

**Act 2 as written cannot be performed, for a different reason than this
section used to give.** ⚠️ It read "`AssetGate` has never been deployed to
a persistent chain … the testnet registry has no report" until
2026-08-18. Both were false by then: the gate is live on testnet at
`0xAac48DC261B04737FDCB101D5049395121034a83` and the testnet registry
holds three reports.

The real obstacle is the ending. Act 2 finishes with the gate flipping to
`ACCEPTED`, and no report has ever reached `CONFIRMED`, so the gate
correctly returns `(false, "status not allowed")`. Performing that act
would require either a confirmed report or a control set changed for the
camera. The second is forbidden; the first has not happened yet.

**A live testnet epoch is not this demo.** It is the canary in
`docs/CANARY-G1B.md`, owner-gated, and it will publish whatever
emerges — including `UNVERIFIABLE` on an unseeded workspace. That is a
true canary, not a two-act product walkthrough. This document does not
authorise it.

**USDY and FOBXX are not demo assets.** USDY has no bounded retrieval. FOBXX now has a
strict offline SEC discovery/N-MFP3 normalizer and committed fixtures, but no live epoch,
policy publication, or production-host route has been claimed.

---

## 5. What can be rehearsed locally, today

This is the path that actually exists. It is a private Hardhat chain.
It is not the hero demo. Say that when you run it.

### 5.1 Managed local-chain end-to-end

The test starts and stops its own node on an ephemeral loopback port.
It deploys a registry and an `AssetGate`, evaluates the approved
controls against the committed fixtures, signs, verifies a bundle,
publishes, ages the chain until the gate refuses, and publishes a
correction of the **same** epoch.

```text
python -m pytest --strict-markers tests/test_e2e_local.py
```

The same loop is also a script. By default it starts and stops its own
Hardhat node. `--use-running-node` requires one already listening on
`http://127.0.0.1:8545` instead.

```text
python scripts/e2e_local.py
python scripts/e2e_local.py --use-running-node
```

The publisher key used here is derived from Hardhat's published
development mnemonic. It controls nothing on any real network. No
production secret is involved.

What this proves: the vertical works on a local chain, including a
consumer gate reacting to freshness. What it does not prove: anything
about testnet, live sources, or a public dossier.

### 5.2 Fixture epoch without a chain

```text
python -m touchstone.epoch --fixtures
```

This evaluates the committed captures. An unseeded store reports
`UNVERIFIABLE` on the first capture and can report a confirmed state
only after the second, because value controls require the 24-hour
confirmation window. The two committed fixtures are dated a day apart
and are the golden input for that path.

### 5.3 Unattended service, fixture mode, local manifest only

```text
python scripts/run_service.py \
  --manifest <local deployment manifest> \
  --workspace <workspace> \
  --asset-key eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e \
  --fixtures fixtures \
  --fixture-capture 2026-08-14 \
  --max-runs 1
```

`--fixtures` against `deployments/xlayer-testnet-2.json` is refused.
That refusal is the control; do not look for a way around it.

The superseded registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`
is refused before any key is read.

---

## 6. If the canary has been run by then

If the owner has authorised and executed `docs/CANARY-G1B.md` since this
file was written, a real testnet report may exist. This runbook does
not assume that. Check the registry and the canary packet's "After the
run" section before claiming a transaction. If nothing has been
published, do not invent an explorer link.

Even then, Act 1 and Act 2 as written still need a live policy-bound
publication and a permitted/refused consumer pair. The dossier,
`/judge`, and a refusing legacy testnet `AssetGate` already exist.

---

## 7. What this document does not claim

- That a judge can walk the two-act script today.
- ~~That a public demo URL exists.~~ One does: https://touchstone.gudman.xyz (since 2026-08-18).
- That fixture evaluation is a live source observation.
- That the local `AssetGate` is the testnet consumer.
- That a canary publication is this demo, or that this demo authorises
  one.
