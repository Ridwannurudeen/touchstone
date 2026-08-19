# The ninety-second film — locked script

**The story is refusal that earns confirmation.** On the 17th the issuer published a NAV; on
the 18th it revised that row, and Touchstone had already declined to certify it. On the 19th a
fresh capture carried the revised value unchanged for over a day — and the same mechanism that
refused it confirmed it, on both chains, with two policy verdicts beside it and a consumer
gate that flipped without a single control changing.

Earlier cuts ended on the refusal because the confirmation had not happened yet, and one cut
falsely narrated a "skip" that never occurred — both corrections are in the git history. This
cut is the complete arc, and every number on screen comes from a retained artifact or a live
page.

## The facts on screen

| Fact | Value |
|---|---|
| The revision | `08/17/2026` NAV `11.17883400` → `11.18208300`, between captures a day apart |
| The refusal | captures 23h39m35s apart — 20m25s short of the interval; `UNEVALUABLE`, asset `UNVERIFIABLE` |
| The confirmation | 2026-08-19: all 5 controls SATISFIED; testnet seq 4 blk 38698679, mainnet seq 3 blk 68389082 |
| Policy verdicts | `disclosure-freshness:1` and `nav-settlement:1`, both CONFIRMED, own registry keys, both chains |
| The gate | refused for two days; now `(true, "allowed")`; a never-verified key still refused |
| The pair | permitted `status 1` / reverted `status 0` on each chain (testnet `0x5b6e65b9…`/`0xfc9bcc47…`, mainnet `0x8b4b6c85…`/`0x2b106907…`) |
| Bundles | all retained under `/data/`, digests printed beside their downloads |

## The cut

Eight beats, ninety seconds. Narration ≈205 words at a measured pace.

### 1 — Open on the verdict (0:00–0:10)
**On screen:** the live homepage, CONFIRMED card.
> This tokenised Treasury fund is confirmed on X Layer — by a system whose whole design is
> refusing to say that until the evidence earns it.

### 2 — What it watches (0:10–0:22)
**On screen:** the two capture identifiers and UTC times.
> Touchstone captures the issuer's own published feeds and keeps every byte, hashed.

### 3 — The issuer moved a number (0:22–0:36)
**On screen:** the row diff, 08/17 highlighted.
> Between two captures a day apart, the value published for August seventeenth changed.
> Nine hundred and fifty-seven other rows did not.

### 4 — It refused (0:36–0:50)
**On screen:** the interval panel — 23h39m elapsed, 24h required, `UNEVALUABLE`.
> A value counts only once a capture at least a day older still carries it. These were twenty
> minutes short, so it did not evaluate the number at all — the asset stayed unverifiable
> rather than round its own rule down.

### 5 — It confirmed (0:50–1:06)
**On screen:** the confirmation panel — same value refused and confirmed, controls changed:
none; gate allowed; the executed and reverted actions.
> A day later, the same value, unchanged. Every control passed and the first confirmed state
> published to both chains. The gate that refused for two days flipped on its own — and a
> contract action that couldn't run before ran, while one bound to an unverified asset still
> reverted.

### 6 — Ask it your question (1:06–1:18)
**On screen:** the judge page, both policy panels CONFIRMED.
> Different consumers need different evidence, so verdicts publish per policy: is the issuer
> still disclosing, and has the NAV actually settled — each signed, each on its own key.

### 7 — Check it yourself (1:18–1:25)
**On screen:** `/verify`, the digest beside the bundle.
> Every report is a downloadable bundle you verify offline. The page tells you its hash.

### 8 — The thesis (1:25–1:30)
**On screen:** the status page, hold.
> Not a rating. A machine-checkable answer that says no until it can prove yes.

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
