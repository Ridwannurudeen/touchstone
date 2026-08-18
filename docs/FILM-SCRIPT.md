# The ninety-second film — locked script

**The story is the refusal.** Every report Touchstone has published reports `UNVERIFIABLE`,
and the consumer gate on X Layer testnet refuses the asset. That is not the demo failing to
reach a happy ending; it is the product working, and on 2026-08-17 it was working against a
number the issuer went on to change.

`ROADMAP.md` specifies a two-act demo whose second act ends with the gate flipping to
`ACCEPTED`. `docs/DEMO-RUNBOOK.md` says plainly that act cannot be walked, because no report
has ever reached `CONFIRMED`. **This script does not chase it.** Nothing here is staged, no
control set is changed for the camera, and no state is altered off-screen.

---

## The facts on screen

Every one is verifiable by a viewer, and every one was read from the chain or from retained
evidence rather than recalled.

| Fact | Value |
|---|---|
| Testnet registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, chain 1952, `latestSequence` 3 |
| Mainnet registry | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, chain 196, `latestSequence` 2 |
| Consumer gate | `0xAac48DC261B04737FDCB101D5049395121034a83`, testnet — `check()` returns `(false, "status not allowed")` |
| Earlier capture | `4d22989c…7e54`, retrieved 2026-08-17T16:48:32Z |
| Later capture | `f9f87f32…1cd7`, retrieved 2026-08-18T16:28:07Z |
| The revision | `08/17/2026` NAV `11.17883400` → `11.18208300` |
| Unrevised rows | `08/15/2026` and `08/16/2026`, byte-identical across both captures |
| The policy | `ustb-nav-per-share-present`, `minimum_row_age_business_days: 2` |
| Signed bundle | sha256 `914ea892…bfc955`, printed on `/verify` beside its own download |

⚠️ **Do not say the issuer restates every day.** Two captures support one changed row and two
unchanged ones. They do not establish a settlement policy, and claiming one would be the same
kind of overclaim this film is about.

---

## The cut

Timings are targets. Narration word counts assume a measured pace of roughly 150 words per
minute; the whole script is about 215 words, which fits 90 seconds with air around it.

### 1 — The decision, first (0:00–0:10)

**On screen:** `touchstone.gudman.xyz` dossier for `ustb-2026-08-17`. Hold on the
`UNVERIFIABLE` chip. Cut to the gate result panel: `(false, "status not allowed")`.

> A consumer contract on X Layer asks Touchstone whether this tokenised Treasury fund's
> disclosures check out. Touchstone says: not confirmed. So the gate refuses it.

### 2 — What it looked at (0:10–0:30)

**On screen:** the two capture identifiers with their UTC retrieval times, side by side. Then
the source: Superstate's published daily NAV feed for USTB.

> It had fetched the issuer's own daily NAV feed twice, about twenty-four hours apart, and kept
> both responses exactly as they arrived — every byte, hashed.

### 3 — The catch (0:30–0:50)

**On screen:** the row diff. Three rows: 08/15 unchanged, 08/16 unchanged, 08/17 highlighted,
`11.17883400 → 11.18208300`.

> Nine hundred and fifty-seven rows were identical. One was not. The value published for the
> seventeenth of August had changed by the time it was read again the next day.

### 4 — Why that matters (0:50–1:10)

**On screen:** the approved control, showing `minimum_row_age_business_days: 2`, then the
evaluator's rule in plain words: *the newest row that is unchanged across two captures and at
least two business days old.*

> Touchstone never reads the freshest number and calls it verified. It observes a value only
> once a second capture, taken at least a day later, still carries it unchanged. The row that
> moved was skipped — not flagged after the fact, skipped before anything was signed.

### 5 — The proof (1:10–1:24)

**On screen:** the verifier running against the downloaded bundle; the sha256 on `/verify`
matching the file. Then the registry entry on chain. Keep the gate refusal visible.

> The result is signed, published to an append-only registry on X Layer, and checkable offline
> by anyone. The page tells you the hash of the file it is offering you, and the file matches.

### 6 — The thesis (1:24–1:30)

**On screen:** the `UNVERIFIABLE` chip, held.

> This is not a rating, and not a price oracle. It is a machine-checkable refusal to overclaim
> when the evidence is provisional.

---

## What must not appear on screen

Recording exposes whatever is behind the window. Excluded:

- filesystem paths, usernames, shell history, any command line carrying an environment
  variable, `.env` filenames, key material, API tokens, cookies, request headers;
- other browser tabs or windows, bookmarks, password managers, cloud drives, messaging apps,
  wallet extensions, SSH sessions, server dashboards, unrelated vhosts;
- any workspace directory listing — the workspace sits beside the keys;
- devtools, and any transaction-signing prompt.

Use a clean browser profile with no extensions and no bookmark bar. Capture the page, not the
desktop. The evidence panels are pre-rendered HTML for exactly this reason: they show the
retained artifacts without a terminal in frame.

## Narration

**Owner-recorded.** A film about evidence discipline narrated by a synthetic voice invites the
obvious question, and the words `UNVERIFIABLE`, `AssetGate` and `X Layer` are all easy for
speech synthesis to mangle. Record after picture lock, three takes, short sentences. Do not
narrate while clicking — the pauses land in the wrong places.

## Assembly

`scripts/record_film.py` drives a clean browser over the live site and the pre-rendered
evidence panels and writes numbered scene clips. `scripts/build_film_panels.py` renders the
panels from retained evidence — it fetches nothing, so the numbers on screen are the ones that
were captured, not whatever the issuer is serving today.

The result is a silent cut at the timings above. The voiceover is laid over it afterwards.
