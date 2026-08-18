# The ninety-second film — locked script

**The story is the refusal.** Every report Touchstone has published reports `UNVERIFIABLE`,
and the consumer gate on X Layer testnet refuses the asset. That is not the demo failing to
reach a happy ending; it is the product working, and on 2026-08-17 it was working against a
number the issuer went on to change.

`ROADMAP.md` specifies a two-act demo whose second act ends with the gate flipping to
`ACCEPTED`. `docs/DEMO-RUNBOOK.md` says plainly that act cannot be walked, because no report
has ever reached `CONFIRMED`. **This script does not chase it.** Nothing here is staged, no
control set is changed for the camera, and no state is altered off-screen.

> ⚠️ **Corrected after an audit, and the correction matters.** The first cut of this film said
> the revised row "was skipped" in favour of an older settled one. That is what the rule does
> in general. It is **not** what happened on this run. These two captures are 23h39m35s apart,
> short of the twenty-four-hour confirmation interval, so **no predecessor qualified and the
> value control never compared any row at all** — the published sequence-2 report records
> `ustb-nav-per-share-present` as `UNEVALUABLE` with no observed value. Narrating a skip over
> that evidence asserted a cause that did not occur. The cut now says what the run actually
> did, which is a better story anyway: it declined because it was twenty minutes short of its
> own rule.

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
| Gap between the captures | **23h 39m 35s** — short of the 24h confirmation interval by 20m 25s |
| What the run actually returned | `ustb-nav-per-share-present` → `UNEVALUABLE`, no observed value; asset `UNVERIFIABLE` |
| Signed bundle | sha256 `914ea892…bfc955`, printed on `/verify` beside its own download |

⚠️ **Do not say the issuer restates every day.** Two captures support one changed row and two
unchanged ones. They do not establish a settlement policy, and claiming one would be the same
kind of overclaim this film is about.

---

## The cut

Seven beats, ninety seconds. Narration is about 210 words, which fits a measured 150 wpm with
air around it.

### 1 — The decision, first (0:00–0:10) · 10s

**On screen:** the live dossier for `ustb-2026-08-17`. Hold on the `UNVERIFIABLE` chip.

> A consumer contract on X Layer asks Touchstone whether this tokenised Treasury fund's
> disclosures check out. Touchstone says: not confirmed. So the gate refuses it.

### 2 — What it looked at (0:10–0:24) · 14s

**On screen:** the two capture identifiers with their UTC retrieval times, side by side.

> It had fetched the issuer's own daily NAV feed twice, a day apart, and kept both responses
> exactly as they arrived — every byte, hashed on receipt.

### 3 — The issuer moved a number (0:24–0:42) · 18s

**On screen:** the row diff — 08/15 unchanged, 08/16 unchanged, 08/17 highlighted,
`11.17883400 → 11.18208300`.

> Nine hundred and fifty-seven rows were identical. One was not. The value published for the
> seventeenth of August had changed by the time the feed was read again the next day.

### 4 — The rule that exists for exactly that (0:42–0:58) · 16s

**On screen:** the approved control, `minimum_row_age_business_days: 2`, and the rule in words.

> So Touchstone never reads the freshest number and calls it verified. A value counts only
> once a capture at least twenty-four hours older still carries it, unchanged.

### 5 — What it actually did (0:58–1:16) · 18s

**On screen:** the interval panel — 23h 39m 35s elapsed, 24h required, short by 20m 25s; and
`ustb-nav-per-share-present → UNEVALUABLE`, no observed value.

> On this run it never got that far. The two captures were twenty minutes short of the
> interval, so nothing qualified to confirm against and the control did not evaluate the value
> at all. The asset stayed unverifiable — not because the number looked wrong, but because the
> system would not vouch for a check it had not been able to make.

### 6 — Checkable by anyone (1:16–1:24) · 8s

**On screen:** `/verify`, the published sha256 beside the bundle it describes.

> The result is signed, published to an append-only registry on X Layer, and checkable
> offline. The page tells you the hash of the file it is handing you.

### 7 — The thesis (1:24–1:30) · 6s

**On screen:** the live status page, then hold.

> Not a rating. Not a price oracle. A machine-checkable refusal to overclaim when the evidence
> is provisional.

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

**Pin the capture pair.** The observer adds captures continuously, so an unpinned render can
quietly become a different comparison than the one narrated above. Render with:

    --workspace <the workspace holding both artifacts>
    --earlier-sha256 4d22989cec6a7aa2763e2bc8cdfa705572997613e4366cc69e41528169307e54
    --later-sha256   f9f87f321e6342ea5b31f2181ad506495233cb0558b6b60f8556eabbf51d1cd7

The builder refuses if either is absent. Both live in the **testnet** workspace; the mainnet
workspace holds only the later one, so rendering from mainnet is not a substitute.

The result is a silent cut at the timings above. The voiceover is laid over it afterwards.
