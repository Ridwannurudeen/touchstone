# Touchstone — Phase 0 Source Audit

**Audit date:** 2026-08-13
**Gate:** an asset enters the build only with an attributable official source, repeatable
no-login retrieval, ≥2 honest machine-observable controls, explicit cadence, hashable
evidence, and a sprint-feasible adapter. Abort Touchstone entirely if fewer than two
candidates pass.

All probes below were executed live from this development machine with plain `curl`
(realistic User-Agent, no cookies, no login). VPS re-verification is required at deploy
time; it is recorded per asset as a residual check (R-8 in `docs/THREAT-MODEL.md`, which
also records what retrieval and parsing do and do not defend against).

---

## USTB — Invesco/Superstate Short Duration U.S. Government Securities Fund

**Status: PASS (hero candidate) — verified live 2026-08-13 13:16 UTC**

### Sources (all first-party, all verified this session)

| Source | URL | Result |
|---|---|---|
| Daily NAV history (JSON) | `https://api.superstate.com/v1/funds/1/nav-daily` | HTTP 200, `application/json`, 224,837 bytes, no auth, no anti-bot; identical bytes on immediate re-fetch (repeatable) |
| Yield (JSON) | `https://api.superstate.com/v1/funds/1/yield` | HTTP 200, 122 bytes: `as_of_date: 2026-08-11`, 30-day 0.03506, 7-day 0.03492, 1-day 0.03553 |
| Holdings (JSON) | `https://api.superstate.com/v2/funds/1/holdings` | HTTP 200, 5,396 bytes: `as_of_date: 07/24/2026`, full T-bill schedule (security, cost, maturity, yield, % of fund) |
| API documentation | `https://docs.superstate.com/llms-full.txt` (full docs export) | HTTP 200, text/markdown, 145,985 bytes — documents the endpoints above as public; Swagger at `api.superstate.com/swagger-ui/` (HTTP 200) |

### Sample observed values (2026-08-13)

- NAV/S: **$11.17558800**, AUM **$958,406,746.95**, outstanding shares **85,758,954.871099**
- Full daily history included in one response (built-in historical versions).

### Official onchain surface (from Superstate docs; onchain reads delegated to Agent C)

- USTB Token Proxy (Ethereum): `0x43415eB6ff9DB7E26A15b704e7A3eDCe97d31C4e`
- Superstate USTB Continuous Price Oracle: `0xe4fa682f94610ccd170680cc3b045d77d9e528a8`
- Chainlink-compatible USTB Oracle: `0x289B5036cd942e619E1Ee48670F98d214E745AAC`
  (docs: "has the daily USTB/USCC NAV/S price and can be used like any other Chainlink oracle")
- Docs state NAV/S updates continuously via oracle checkpoints; income accrues on market days.

### Honest quirks recorded

- **The nav-daily tail is provisional and is revised in place — verified 2026-08-14 by
  differing two retained artifacts.** The 08-13 capture (`fixtures/ustb-nav.json`,
  sha256 `4830bc34…`, 954 rows) carried 08/12 and 08/13 rows both holding the 08/11
  values (NAV/S 11.17558800, AUM 958,406,746.95). The 08-14 live artifact
  (sha256 `5b6a53e0…`, 955 rows, retrieved 17:08:12Z) shows both of those row-dates
  rewritten — 08/12 → 11.17666400 / AUM 951,115,028.81, 08/13 → 11.17774800 / AUM
  953,805,376.22 (`outstanding_shares` and `net_income_expenses` revised with them) —
  and a new 08/14 row again carrying forward the prior day's values.
  Consequences: (a) a row existing for today proves the **feed is live**, not that a
  final NAV exists for that date; (b) evidence for a given row-date is **mutable**,
  so an unchanged row-date is not an unchanged fact and re-fetching yields a different
  artifact hash for reasons other than new data; (c) a single snapshot cannot
  distinguish a carry-forward placeholder from a genuine unchanged-NAV day — only
  cross-epoch comparison can. Any value claim keyed to the newest rows must be labelled
  provisional, or restricted to rows older than the observed revision window (≥2
  business days on the evidence to date).
- Holdings bytes were byte-identical across both captures (sha256 `0c42d794…`, 07/24
  as-of) — the holdings endpoint is stable, only nav-daily mutates.
- Holdings lag NAV (07/24 vs 08/13 today): holdings freshness control needs a
  generous grace period (~35-40 days) until cadence is observed longer.
- `subscription_nav_per_share` is null in recent rows — not usable as a control.

### Proposed controls (all empirically supported today)

> **Historical.** This section records what the 2026-08-13 probes supported, and the names
> below are the hand-written control set that was **retired on 2026-08-16**. No control in
> this list exists any longer. The live set is eight controls a model proposed from issuer
> bytes, each bound by digest to the compilation that accepted it and listed in
> `data/compilations/APPROVALS.json`. Kept unedited because it is the record of what the
> source audit found, not a description of the system.

1. `nav-row-freshness`: a nav-daily row exists for a date within N business days (grace for weekends/holidays).
2. `yield-freshness`: `as_of_date` within N business days.
3. `holdings-freshness`: `as_of_date` within ~40 days (provisional until cadence observed).
4. `nav-oracle-consistency`: API NAV/S vs onchain Chainlink oracle answer within tolerance (cross-source INCONSISTENT detector; pending Agent C oracle read).
5. `shares-supply-relationship`: API `outstanding_shares` vs onchain token supply drift tracking (exact relationship to be established conservatively — shares may span book-entry + multiple chains; observation-only until understood).
6. `aum-published`: AUM field present and parseable (observation, not judgment).

### Residual checks before mainnet claims

- **Defect found and remediated 2026-08-14:** the control set read the newest nav-daily
  row, so `aum-published` and `value-vs-expected` would have reported the provisional
  carry-forward row's values as the epoch's observed values — attributing the prior day's
  AUM to today. No live or operational report was published under those semantics (test
  fixtures sign reports routinely). Both controls are now `control_version: 3` and
  observe only a row **confirmed unchanged across two retained captures ≥24h apart**,
  recording its `observed_on`; a first epoch with no predecessor abstains and the asset
  reports `UNVERIFIABLE`. **No approved control declares `minimum_row_age_business_days`.** The retired
  hand-written set used 2; the compiler did not propose it and approval may not add it,
  so confirmation ≥24h apart is currently the only safeguard — and 24 hours can fall
  entirely across a weekend, giving the issuer no business day in which to revise.
  The floor still applies where a control declares one. The offline verifier rejects a value
  with no evidence date, a value dated after the epoch, and a conclusive evaluation with no
  date. It requires a confirmation capture **for nav-daily only**: confirmation is a source
  policy, and the yield and holdings endpoints publish scalars with nothing for an earlier
  capture to confirm.

  **Superseded 2026-08-16.** `aum-published` and `value-vs-expected` no longer exist —
  the hand-written set they belonged to was retired because nothing had compiled it, so a
  report claiming compiler provenance for it claimed something untrue. The defect described
  above is still closed: the replacement NAV value controls observe only a row confirmed
  unchanged across two captures, and none of them was ever published under the old
  newest-row semantics.
  **Residual limitation:** the confirmation window is derived from two captures and
  cannot prove that an older row is never revised; the business-day count ignores
  holidays. Both are recorded in the report's published limitations.
- Re-fetch repeatedly across days from the deployment VPS (scripted, part of sprint).
- API terms-of-use review (docs present the API publicly; confirm no usage restriction).
- Establish the exact outstanding-shares vs onchain-supply relationship before enabling control 5 beyond observation mode.

---

## USCC — Bitwise/Superstate Crypto Carry Fund (same-issuer spare)

**Status: PASS as spare — verified live 2026-08-13**

- `https://api.superstate.com/v1/funds/2/nav-daily`: HTTP 200, 180,286 bytes, same schema.
  2026-08-13: NAV/S $11.67389000, AUM $123,767,874.17, shares 10,602,110.707742.
- Same API family/adapter as USTB (near-zero marginal adapter cost).
- Docs: USCC NAV set once per business day at 4pm ET marks (daily cadence, not continuous);
  NAV **can decline** (basis/mark-to-market) — monotonicity is NOT a valid control here.
- Weakness as a portfolio pick: same issuer as USTB — does not prove cross-issuer
  repeatability; use only as fallback/fourth asset.

---

## OUSG / USDY (Ondo) — **BOTH PASS** (verified 2026-08-13)

> **Superseded 2026-08-16.** This section recorded USDY as selected for the sprint. Both
> assets are now cut: USDY because its retrieval is unbounded (a single 260 MB archive), and
> OUSG because the second-adapter metric was abandoned rather than chased. Phase 1 ships one
> USTB vertical. The Phase-0 findings below stand as the record of what was probed.

**USDY — PASS, selected as second daily asset.** 759 Ankura Trust daily attestation
PDFs in a public Dropbox folder (links embedded in the server-rendered
`ondo.finance/usdy`; `rlkey` param is the credential, `st` param droppable): cookieless
zip download verified (root 260MB; **2026 year-subfolder 35.9MB — fetch that**, hash
re-derived per run via the root listing). Filename pattern
`YYYY/MM Monthname/Ondo USDY LLC_ATCAttest_YYMMDD.pdf` stable since 2023-09. Sample PDF
(260807) fully text-extractable: Token Principal 2,134,875,462.46; Permitted Assets
2,142,264,400.29; **collateralization 1.003461 ≥ 1.000 — a real daily covenant test by
a named third-party verification agent** (the strongest single control in the
portfolio). Cadence: business-day reports, 3-business-day publication lag (held on
23/24 genuine timestamps AND observed live in-session: Mon 08/10 report appeared Thu
08/13 13:32Z). Onchain `getPriceData()` cross-checks PDF Token Value (1.14266759 vs
1.142013). **TRAPS:** (a) the USDY oracle SELF-ACCRUES daily incl. weekends
(~+0.00010916/day) — "oracle changed" is NEVER a freshness signal; (b) no per-file
URLs, no HTTP Range — daily check costs the year-zip pull; (c) `rlkey` can rotate —
re-scrape ondo.finance/usdy for current links every run; (d) attestations cover Ondo
USDY LLC only, not Ondo Global Markets (BVI) — dossier must state this; (e) 126/150
2026 files carry a bulk-reupload mtime (2026-07-09) — mtimes unusable for lag except
the 24 genuine ones; (f) one unexplained missing weekday 2026-03-31.

**OUSG — PASS, second spare.** OndoOracle `getAssetPrice(OUSG)` = 116.204547 (18-dec)
cross-checks the SSR page value `$116.2045 +$0.0109` to 6 s.f. (delta matches prior
business day 116.193670 exactly). Oracle steps ~+0.0107/business-day, FLAT on weekends
(15 samples) — genuinely discrete updates, so staleness IS meaningful here (opposite
of USDY). Portfolio table SSR'd with `As of` date (~1bd lag) incl. full holdings
(State Street 40.06%, BUIDL 26.96%, BENJI 17.24%, FYOXX 15.01%…). **TRAPS:** hero
widget SSRs zeros (anchor on the marketing block / `Portfolio Overview` text, never
CSS hashes); official NAV-Consulting daily financials are login-gated (302→
ServiceLogin — the dossier must say the NAV number is verifiable but the underlying
financials are not); monotonicity is NOT an honest invariant (use business-day-aware
"changed within window"). No JSON API exists for OUSG NAV. Legacy oracle
`0x0502…6abe` is deprecated — never use. RPC notes: eth.drpc.org served archive
calls; merkle.io/publicnode rate-limited historical sampling (429/403).

Findings from the Ondo research agent will be recorded here.

## BENJI/FOBXX (Franklin Templeton) — **PASS** (verified 2026-08-13)

- **Daily NAV (T-1, business days):** the fund page's own config names a same-origin
  GraphQL endpoint `POST https://www.franklintempleton.com/api/pds/price-and-performance`
  (GET returns 403; POST returns 200, JSON, no auth, no anti-bot block). `ProductLookup`
  (ticker FOBXX) → fundid 29386; `PricesHistory` returned 159 business-day rows for 2026
  (latest 2026-08-12: NAV $1.0000, navstd 1.00000000, daily liquidity 64.07%, weekly
  71.57%). Liquidity ratios intermittently blank (08-03/04/06) — blank = no-data, never
  a breach. 7-day yield: COULD NOT VERIFY (schema exists, returns empty for this fund).
- **Regulator second source (SEC EDGAR, all verified 200 no-login):** ticker→CIK via
  `company_tickers_mf.json` (CIK 1786958, S000067043); filings index
  `data.sec.gov/submissions/CIK0001786958.json`; latest N-MFP3 filed 2026-08-06 for
  period 2026-07-31: net assets **$720,928,224.29**, shares 720,931,891.09,
  stablePricePerShare 1.0000, PwC as accountant. **Liquidity corrected 2026-08-15:** the
  filing carries 22 dated rows, one per business day. 67.42%/74.62% are the **2026-07-01**
  values; the **2026-07-31** period-end values are **65.28%/74.55%**. Quoting the first row
  as the filing's figure was a date-attribution error.
  Monthly cadence, ~4-6 day filing lag. **This gives FOBXX a genuine two-source
  cross-check (issuer API vs regulator filing) — the strongest evidence-class pairing
  in the audit.**
- **Key caveat:** the daily feed is an undocumented private endpoint FT could lock down
  without notice; degradation path is monthly EDGAR (never total loss). Onchain: no NAV
  oracle on any chain; supply fragmented across 9 networks + iBENJI class — onchain
  supply observation is out of sprint scope for this asset (dossier discloses this
  explicitly).
- Supported controls: daily NAV-peg (navstd == 1.00000000, row ≤ T-3bd), liquidity
  floors (≥10%/≥30% when present), feed-liveness, monthly N-MFP filing appears ≤10bd
  after month-end with stablePricePerShare 1.0000, FT-vs-SEC liquidity reconciliation,
  ProductLookup schema-drift canary.

## Source re-verification 2026-08-15 (PLAN-T4) — two findings that change the plan

Machine-readable manifests now live in `manifests/sources/`. Re-probing the portfolio while
writing them produced two findings that supersede parts of the 2026-08-13 record.

**🔴 FOBXX daily feed is now blocked.** `POST https://www.franklintempleton.com/api/pds/price-and-performance`
returns **HTTP 403** behind a Cloudflare interstitial — to a plain POST and to one carrying
full browser headers including `Origin` and `Referer`. The public fund page on the same host
also returns 403. The 08-13 audit recorded this endpoint returning HTTP 200 to POST, so
either the vantage differs or Franklin has tightened access. **The risk this audit already
identified — an undocumented private endpoint the issuer could lock down without notice —
has materialised.** FOBXX's monthly regulator path is unaffected: the 2026-07-31 N-MFP3 was
retrieved cleanly and is now a committed fixture. Its net assets 720,928,224.29 and series
S000067043 match this audit; its liquidity figures **corrected** this audit, which had quoted
the 2026-07-01 row as the period-end value.

**🔴 USDY has no bounded retrieval.** This audit recorded fetching a 2026 year-subfolder of
about 35.9 MB instead of the 260 MB root archive. **That is not reproducible.** A HEAD
against the folder URL with and without `subpath=%2F2026` returns the identical
`Content-Disposition` filename and the identical `Original-Content-Length` of
**260,431,605** — the `subpath` parameter is ignored. As things stand one daily observation
costs a 260 MB download, which is not a bounded retrieval and must not be scheduled as one.
A bounded mechanism has to be found and verified before PLAN-T10, or USDY's cost and cadence
re-decided.

**Unaffected:** USDY link rediscovery works — `ondo.finance/usdy` returns HTTP 200 with both
`rlkey` links present in the served HTML, confirming the rotating credential can be
re-scraped each run rather than persisted. All three USTB endpoints remain reachable.

**One correction found by probing:** EDGAR serves the N-MFP3 as `text/xml`, not
`application/xml`. Since PLAN-T5 will enforce MIME against the manifest, the declared value
has been set to what the source actually sends.

## PAXG (Paxos) — **FAIL** (verified 2026-08-13; excluded from the sprint)

- Attestation PDFs are real, text-extractable, and immutable once published (June 30
  2026 report: KPMG, 452,151 PAXG vs 452,355 oz — coverage holds), and there is no
  anti-bot or login. **But there is no stable machine-readable contract:** the
  transparency page HTML contains zero PDF links; report URLs live only inside a
  content-hashed Framer JS bundle that changes on every site redeploy; month labels
  carry no year and no asset name — a **USDG (different product) report was found in
  the PAXG bundle** — so nothing can be attributed without downloading and parsing each
  PDF; Content-Type is inconsistent on the same CDN; no supply/reserve JSON exists
  (docs.paxos.com lists only authenticated trading APIs). Onchain: no issuer-published
  oracle (announced Chainlink PoR feed absent from Chainlink's directory).
- Roadmap abort rule applied: evidence discovery relies on reverse-engineering a
  rebuildable front-end bundle = fragile; controls would claim more stability than the
  source provides. PAXG is parked for Phase 2+ (revisit if Paxos publishes stable
  report URLs or a data API).

## Onchain observability (all five assets) — VERIFIED 2026-08-13 13:15–13:30 UTC

All reads keyless via public JSON-RPC; every address from issuer-official sources; all
values cross-confirmed on two endpoints. Full raw-hex working notes retained by auditor.

**RPC reality (gating infrastructure finding):** `eth.llamarpc.com` (HTTP 521) and
`cloudflare-eth.com` (JSON errors) are UNUSABLE; `rpc.flashbots.net` rejects `eth_call`;
`rpc.ankr.com/eth` is key-walled. **Primary: `ethereum-rpc.publicnode.com`** (0.7–1.4s,
30/30 burst calls OK) **· Fallback: `eth.drpc.org`** (free tier, quota undisclosed). No
rate-limit headers on either — implement retry-with-backoff; pin blockNumber into every
eth_call (endpoints ran one block apart; cross-endpoint comparison only valid at a fixed
block).

| Asset | Token (chain) | Supply (decoded) | Issuer-official oracle | Oracle value @ read |
|---|---|---|---|---|
| USTB | `0x43415eB6…C4e` (ETH) | 68,913,599.947976 (6 dec) | Chainlink-compat `0x289B…AAC` + Continuous `0xe4fa…8a8` (both in Superstate docs) | $11.175588 (updated 08-12 13:13Z) / $11.177405 (current-second extrapolated) |
| OUSG | `0x1B19…e92` (ETH) | 1,403,785.674966 (18 dec) | OndoOracle `0x9Cad…094` — NOT Chainlink-shaped: `getAssetPrice(address)`, Sourcify-verified ABI; **no timestamp in return** (freshness needs underlying `0xadc4…df3`) | $116.204547 |
| USDY | `0x96F6…85C` (ETH) | 970,805,366.407203 (18 dec) | USDYOracleWrapper `0x87b1…F90`: `getPriceData()` → (price, ts) | $1.14266759 @ 13:24:11Z |
| PAXG | `0x4580…F78` (ETH; address only in issuer GitHub README — paxos.com page has NO address) | 436,225.095154 (18 dec) | **NONE issuer-published** (announced Chainlink PoR feed absent from Chainlink directory — COULD NOT VERIFY). Third-party Chainlink PAXG/USD `0x9944…8C3`: $4,393.54 (corroborated class only) | — |
| BENJI | Stellar primary (`BENJI` / issuer `GBHN…IW5`, first-party stellar.toml); ETH `0x3DDc…dc9` | Stellar authorized 485,686,460.099; ETH 48,005,967.446 — fragmented across 9 networks, no aggregate; separate iBENJI class (210.7M) | **NONE on any chain** | — |

**Empirical control confirmation (hero):** USTB API NAV `11.17558800` vs Chainlink oracle
`11.175588` (08-12 update) — independent sources agree to the digit.
`nav-oracle-consistency` is real and live today. **Corrected 2026-08-14:** that API value
was read from the then-newest 08/13 row, which the 08-14 capture revealed to be a
provisional carry-forward of the 08/11 value — the revised 08/13 NAV is `11.17774800`.
The agreement therefore holds between the oracle and the **08/11 row**, which was
unchanged between the two retained captures, and the control must compare an oracle
reading against a confirmed row of the matching date, not against the feed's tail. Note also that the two official oracles legitimately differ
(checkpoint vs extrapolation) — the control must name which oracle is authoritative and
use a tolerance.

**Onchain ranking for a keyless daily monitor:** USTB > OUSG > USDY > PAXG (supply-only)
> BENJI (non-EVM primary, fragmented, no NAV oracle — gate behind an explicit
scope decision; effectively disqualified for the sprint).

**Adapter directives for the builder:** never guess oracle selectors (both Ondo oracles
revert on all Chainlink-style selectors — resolve ABIs via Sourcify v2 only; v1 is in
brownout); store `decimals()` per contract (6/8/18 all occur); pin block numbers;
key contracts by (chain, address) — Superstate reuses addresses across chains.

---

## Portfolio selection — **REOPENED 2026-08-15** (was FINAL 2026-08-13)

**Current standing as of 2026-08-15** — this supersedes the 2026-08-13 selection below it:

- **Hero: USTB (Superstate/Invesco)** — unchanged. Public documented JSON API, verified live
  and bounded; dual issuer-official onchain oracles; digit-level API↔oracle agreement.
- **Second daily asset: VACANT.** USDY held this slot and is **suspended** — its retrieval is
  not bounded.
- **Contrast: FOBXX (Franklin BENJI) — monthly only.** The SEC EDGAR N-MFP3 path is verified
  and keeps FOBXX qualified as a monthly regulator-backed contrast. The daily issuer feed is
  unreachable, so the daily-liveness and issuer-versus-regulator controls are **blocked**.
- **Candidate for the vacant slot: OUSG (Ondo)** — bounded retrieval verified, cross-issuer
  coverage preserved, **not promoted** pending an oracle cross-check that needs the full
  oracle address.
- **Spare: USCC (Superstate)** — same issuer as the hero, so it cannot supply cross-issuer
  proof.

The 2026-08-13 selection, retained for the record and **no longer current**:

- ~~Hero USTB · Second daily USDY · Contrast FOBXX with a daily feed · Spares OUSG, USCC.~~
- **Rejected: PAXG** (FAIL — fragile bundle-scrape discovery, unattributable labels).

**Gate state: REOPENED 2026-08-15.** The 2026-08-13 gate passed on three assets. Two of
those three no longer satisfy the roadmap's requirement of repeatable, bounded, non-manual
retrieval:

- **USDY — SUSPENDED.** Retrieval is not bounded. The archive is served only as a single
  260,431,605-byte zip and the `subpath` parameter is ignored, so one daily observation would
  cost a 260 MB download. PLAN-T10 is not viable as written.
- **FOBXX — DEMOTED to monthly regulator-backed contrast.** The daily issuer feed returns
  Cloudflare 403 from this environment, so daily-liveness and issuer-versus-regulator
  reconciliation cannot be claimed. Its SEC path is verified and unaffected.
- **USTB — unaffected**, all three endpoints reachable and bounded.
- **OUSG — opened as a candidate** for the vacant second-daily slot, and it preserves
  cross-issuer coverage since USTB is Superstate and OUSG is Ondo. Its page retrieval is
  verified bounded at 732 KB. It is **not promoted**: the oracle cross-check is unverified
  because this audit records the oracle address only in abbreviated form.

**The second daily slot is currently unfilled.** Portfolio selection is reopened rather than
quietly preserved, because lowering the evidence standard to keep three assets is precisely
what this audit's own abort rule forbids.
