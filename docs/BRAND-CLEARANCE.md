# Brand-collision report — "Touchstone"

**Searches run:** 2026-08-14, 21:27–21:50 UTC · **Compiled:** 2026-08-14

This is a record of searches performed and what they returned. **It is not a legal clearance
opinion and must never be described as one.** Nothing here was registered, purchased,
reserved, filed, or submitted; no accounts were created.

## How to read this document

Every check records three independent fields, which must not be collapsed into each other:

- **`search_execution`** — `complete` · `partial` · `not_completed`
- **`search_result`** — `no_match_found` · `potential_match` · `match_found` · `indeterminate`
- **`legal_status`** — `not_assessed` · `counsel_review_required` · `counsel_opinion_on_file`

**`no_match_found` means only that no matching result appeared for the documented query,
filters, jurisdiction, registry and timestamp.** It never means the name is available,
clear, or free to use. A registry can hold a name in reserve, a package index can block a
name without listing it, and a platform returns "not found" for suspended and reserved
identifiers alike.

**`legal_status` is `not_assessed` for every entry in this document.** No qualified counsel
has reviewed any of it. Affirmative clearance would require a written opinion identifying
its jurisdictions, classes, goods and services, assumptions and date. None exists.

Sources are recorded as **authoritative** (registry of record: RDAP, on-chain contract
call, registry API, USPTO TSDR) or **indicative** (DNS resolution, HTTP probes, rendered
pages, search engines). Indicative evidence never establishes registration status.

---

## 1. Headline findings

Three collisions matter. Two were verified directly against primary sources during
compilation, not merely reported.

### 1.1 An existing product with this project's name *and* architecture

**`touchstone-verify` on PyPI**, published by Touchstone-CV (`touchstone.cv`), v0.5.0 on
2026-07-12. Verified at `https://pypi.org/pypi/touchstone-verify/json`.

Its own summary: *"Touchstone SDK — verify a disclosure (zero deps) and mint one."* Its
description states it re-derives *"that the slice is intact (entry hashes recompute),
attributed (the subject's Ed25519 signature holds, epoch-aware across key rotation), and
ordered."*

That describes Ed25519-signed records, a hash-chained log, key rotation, and dependency-free
offline verification of a *disclosure* — the same primitives this repository implements. The
associated product, `touchstone.cv`, presents itself as *"Touchstone — the black box for your
agents … a tamper-evident, externally-anchored audit log."* Domain registered 2026-06-24
(registrant published as Jack Parnell, Namecheap); GitHub org `Touchstone-CV` created
2026-06-25; npm `@touchstone-cv/mcp` published 2026-07-10.

**This is the most material finding in the report.** It is not a shared dictionary word; it
is the same name attached to substantially the same verification architecture, shipped
first.

### 1.2 A gold-RWA lending brand with this project's name *and* origin story

**Touchstone**, the Morpho vault curator operated by XAUE / Aurise Foundation, announced
2026-07-15. Two vault tokens verified on Ethereum mainnet via Blockscout during compilation:

| Token | Symbol | Contract |
|---|---|---|
| Touchstone XAUT | tsXAUT | `0x98B102d24b5D03F8d840843B0C7Dd98d439ac443` |
| Touchstone USDT | tsUSDT | `0xe7765Dd0aA5D6346d8A2690FD48f0Fcd7da0Ec77` |

Their announcement describes it as *"a Morpho curator built for one job: to be the standard
against which gold-collateralized lending is measured on-chain"*, and explains the name:
*"A touchstone is the small, dark stone that jewelers and assayers have used for millennia
to verify the purity of gold."*

`ROADMAP.md` uses the same rationale. Tokenized gold is a real-world asset, so this sits in
the same category this project addresses. Backed by Antalpha (Nasdaq: ANTA) and Aurelion
(Nasdaq: AURE). The vaults are freshly seeded (2–3 holders), so this is a named product
line four weeks old, not a large deployed protocol — but it is live and indexed by Morpho.

### 1.3 The verifiable-evidence-for-AI lane is crowded, all in 2026

Beyond the two above, PyPI's full index (enumerated, 45MB, authoritative) shows every
project containing "touchstone". The 2026-vintage entries cluster in this project's exact
conceptual space:

| Project | First seen | Self-description |
|---|---|---|
| `touchstone-verify` | 2026-07-10 | verify a disclosure; Ed25519; hash chain |
| `touchstone-compute` | 2026-06-29 | *"Deterministic, verifiable text/code/measurement utilities for AI agents"* (`touchstone.locomot.io`) |
| `touchstone-mcp` | 2026-05 → 07 | *"a cheap, deterministic first-pass filter for unsupported claims in LLM output"*; publishes its own `touchstone-1.0` standard |
| `touchstone-platform` | 2026-05-24 | *"AI with receipts"* |
| `touchstone-prover` | 2026-06-18 → 07 | *"An SMT-based verifier for Python with a machine-checked trust base"*, 219 releases |

`touchstone.locomot.io` was fetched directly during compilation and is live: *"verified
reality — and a trust layer — for agents"*, 16 priced endpoints settling via x402 on Base
(chain 8453).

### 1.4 A factual correction owed to `ROADMAP.md`

`ROADMAP.md` states: *"Verified the same day: no existing crypto/web3 project uses the
name."* Dated 2026-08-13. The Morpho announcement (2026-07-15) and the mainnet contracts
(deployed 2026-07-07) predate it. **That line is false and should be corrected by the
owner.** It is left unedited here because it is owner-authored text.

---

## 2. Trademark registries

`legal_status: not_assessed` for every row. USPTO records were verified individually at
TSDR (authoritative); no full-text enumeration interface was reachable, so **the mark list
is a verified sample, not a complete set of Touchstone marks on the register.**

### 2.1 USPTO — United States

- `official_url`: `https://tsdr.uspto.gov/statusview/sn<serial>`, `https://ttabvue.uspto.gov/ttabvue/v?pnam=Touchstone`
- `query`: word mark "touchstone"; per-record lookups by serial/registration; TTAB party-name search. **No class filter could be applied** — the filtering UI is inside an unreachable single-page app.
- `timestamp_utc`: 2026-08-14 21:29–21:38Z
- `search_execution`: **partial** · `search_result`: **match_found**
- `failure`: `tmsearch.uspto.gov` search results render client-side only (returned header text, no rows); `search-information` endpoint returned `ETIMEOUT`; the backing API is POST-only and returned HTTP 404 to GET; the assignment API returned `ETIMEOUT`/`ENOTFOUND`.
- `alternate_interface_checked`: yes — TSDR statusview **worked** (server-rendered per record; intermittent HTTP 403 under parallel load, resolved by pacing). TTABVUE **worked**.

**Live registrations in relevant classes:**

| Mark | Owner | Reg. | Class | Goods and services | Status |
|---|---|---|---|---|---|
| TOUCHSTONE | AIR Worldwide Corporation (Verisk), Boston MA | rn 4359357 | **009** | *"Computer simulation software for use in the insurance field, namely, computer software that performs risk analysis and loss estimate calculations"* | LIVE, renewed 2023-10-24 |
| TOUCHSTONE | ProTec Solutions, LLC | rn 5389852 | **042** | Online document management for mortgage banking; secured-access website; electronic document storage | LIVE, §8 and §15 accepted |
| TOUCHSTONE INVESTMENTS | Touchstone Advisors, Inc., Cincinnati OH | rn 3586203 | **036** | Financial portfolio management; mutual fund distribution and investment | LIVE, renewed 2018 |
| TOUCHSTONE FUNDS | Touchstone Advisors, Inc. | rn 3229602 | **036** | Mutual fund investment, advisory, distribution; portfolio management | LIVE, renewed 2017 |

**Not live:** TOUCHSTONE INVESTMENTS rn 2792701 (cl 36) **cancelled 2024-06-21**;
TOUCHSTONE VARIABLE ANNUITY rn 1978786 cancelled 2003; KF TOUCHSTONE sn 97761815 (cl 42,
Korn Ferry SaaS) **abandoned 2025-09-01**; TOUCHSTONE PATHWAYS sn 87011924 (cl 9, financial
modelling software) abandoned 2017. One pending: TOUCHSTONE sn 98003609 (cl 9, Audeo LLC,
wearables/social software), third extension granted 2026-06-16.

**Two aggregator errors corrected by going to the registry.** A search summary attributed
serial 98609243 ("blockchain SaaS", class 42) to a Touchstone mark; TSDR shows it is a
**design-only mark with no word element**, owned by EchoConsortia LLC — excluded. A
secondary index reported rn 2792701 as renewed; TSDR shows it cancelled.

**TTABVUE:** party name "Touchstone" returns 23 proceedings. Party name "Touchstone
Advisors" returns *"No documents match the query"*.

**Unresolved.** Touchstone fund prospectuses state that *"Touchstone, Touchstone Funds and
Touchstone Investments are federal service mark registrations and applications owned by IFS
Financial Services, Inc."* A plain **TOUCHSTONE** service-mark registration in financial
services could not be located or verified, and the asserted owner (IFS Financial Services)
differs from TSDR's owner of record on all four marks above (Touchstone Advisors, Inc.).
**A human must resolve both points via full-text search.**

### 2.2 Registries that could not be searched

| Registry | `official_url` | `search_execution` | `search_result` | `failure` | `alternate_interface_checked` |
|---|---|---|---|---|---|
| WIPO Global Brand Database | `https://branddb.wipo.int/en/` | **not_completed** | indeterminate | Serves an Altcha CAPTCHA bot-check, not the database (`<altcha-widget challengeurl="https://api.branddb.wipo.int/captcha">`); `quicksearch` returned `ETIMEOUT` | Yes — WIPO Madrid Monitor reachable but served only help documentation, no query interface |
| EUIPO / TMview | `https://www.tmdn.org/tmview/`, `https://euipo.europa.eu/eSearch/` | **not_completed** | indeterminate | TMview results render client-side (blank page to a fetcher); TMview API returned `ECONNRESET`; eSearch returned the marketing shell only | Yes — both official routes plus the API; the EUIPO REST API needs OAuth credentials, which were not created |
| UK IPO | `https://trademarks.ipo.gov.uk/ipo-tmtext/start` | **not_completed** | indeterminate | **HTTP 403 Forbidden** on all paths including the bare landing page — the host blocks automated clients before any query | Yes — `gov.uk/search-for-trademark` fetched successfully and confirmed the correct endpoint for a human |
| Nigeria (IPO Nigeria) | `https://iponigeria.fmiti.gov.ng/` | **not_completed** | indeterminate | No public search exists. The site's "Search Now" button targets `https://portal.iponigeria.com/auth/` — the same authentication portal used for filings; not proceeded past, per the no-accounts constraint | Yes — followed the 301 from `iponigeria.com`; same auth wall |

**A human must run these four in a browser.** Nigeria realistically requires an accredited
agent or a manual search at the Abuja registry.

---

## 3. Package and code namespaces

All authoritative (registry APIs). `legal_status: not_assessed`.

| Registry | Name | `search_result` | Holder / detail |
|---|---|---|---|
| npm | `touchstone` | **match_found** | v0.0.3, maintainer `leetreveil`, created 2013-04-07. Test-result collection server. Abandoned but occupied |
| PyPI | `touchstone` | **match_found** | v2.0.3, `gmaybrun`, IoC framework, releases 2019–2020 |
| crates.io | `touchstone` | **match_found** | v0.14.2, `iancleary`, **actively published** (2026-08-02), 6,484 downloads. RF S-parameter parser |
| RubyGems | `touchstone` | **match_found** | v0.5.4, Robin Fisher, 2012, 35,292 downloads |
| GitHub | login `touchstone` | **match_found** | User account id 7890668, created 2014-06-14, dormant. Logins are a shared user/org namespace |
| GitHub | org `Touchstone-CV` | **match_found** | Created 2026-06-25 — see §1.1 |
| GitHub | org `touchstonejs` | **match_found** | Created 2014-12-11; `touchstonejs/touchstonejs` has 3,269 stars |

**GitHub repository search:** `total_count` = **613** repositories named touchstone,
including a NeurIPS 2024 CT benchmark (`MrGiovanni/Touchstone`, 140★) and a vision-language
model evaluation (`OFA-Sys/TouchStone`, 84★).

**Variants** `touchstone-labs`, `touchstonelabs`, `touchstone-rwa`, `touchstone-finance`:
`no_match_found` on npm, PyPI, crates.io, RubyGems and GitHub (HTTP 404 in each case,
queried 21:30–21:38Z). PyPI's full `/simple/` index was enumerated (authoritative for
project existence) and contains exactly 10 `touchstone*` projects, none of them these
variants. **GitHub 404s are the weakest signal here** — suspended, deleted and reserved
logins also return 404.

`search_execution: partial` for **npm enumeration** — npm publishes no full index
equivalent to PyPI's, so the npm picture is prominent-not-exhaustive.
`search_execution: not_completed` for **PyPI's web search UI** — returned an anti-bot page
(*"A required part of this site couldn't load"*); superseded by the full-index enumeration.

Note: "Touchstone" is the standard `.sNp` S-parameter file format in RF engineering, which
is why the name recurs across language registries independently of any brand.

---

## 4. Domains

RDAP is **authoritative** for registration status; DNS and HTTP are **indicative** only.
RDAP endpoints were resolved from the IANA bootstrap (`data.iana.org/rdap/dns.json`,
publication 2026-07-23) and each was control-tested with a known-registered and a
known-unregistered name before any 404 was trusted. `.io` is absent from the IANA bootstrap
(ccTLD); the Identity Digital endpoint was used and control-validated —
**authoritative-in-practice, not IANA-bootstrapped.**

### Registered (`search_result: match_found`)

| Domain | Registrar | Created | Expires | Indicative content |
|---|---|---|---|---|
| `touchstone.xyz` | Squarespace Domains II | 2021-06-19 | 2030-06-19 | Afternic nameservers — aftermarket parking |
| `touchstone.io` | Cloudflare | 2012-12-23 | 2026-12-23 | 301 → `bizbudding.com/mai-hosting/` |
| `touchstone.dev` | Sav.com | 2026-04-04 | 2027-04-04 | Spaceship "domain for sale" listing |
| `touchstone.app` | IONOS | 2018-05-08 | **2035-05-08** | No A record; Microsoft 365 nameservers — defensive/corporate hold |
| `usetouchstone.com` | Squarespace Domains II | 2023-07-25 | 2027-07-25 | Live product: *"TouchStone — Agentic Commerce Command Center"* |
| `gettouchstone.com` | DropCatch.com 1293 | 2026-06-21 | 2027-06-21 | NameBright aftermarket infrastructure |
| `touchstone.cv` | Namecheap | 2026-06-24 | 2027-06-24 | Live: *"Touchstone — the black box for your agents"* — see §1.1. Registrant **published**: Jack Parnell |

**Registrant identity is redacted under ICANN policy for all six gTLD domains** —
`search_execution: partial`, `search_result: indeterminate` for registrant identity
specifically. `.cv` publishes its registrant.

### No registry record (`search_result: no_match_found`)

| Domain | RDAP endpoint | `timestamp_utc` | Evidence |
|---|---|---|---|
| `touchstone.finance` | `rdap.identitydigital.services` | 2026-08-14T21:33:22Z | `errorCode 404 / "Object not found"`; controls `yahoo.finance` 200, `nic.finance` 200 |
| `touchstonelabs.xyz` | `rdap.centralnic.com/xyz/` | 2026-08-14T21:33:23Z | `errorCode 404`; controls `touchstone.xyz` 200, `zzqq-nope-8281.xyz` 404 |
| `touchstone-rwa.com` | `rdap.verisign.com/com/v1/` | 2026-08-14T21:33:52Z | HTTP 404 empty body (Verisign's documented behaviour), confirmed on three requests; controls `google.com` 200, nonexistent 404 |

**These are "no registry record at the stated timestamp", not "available".** A registry 404
does not rule out registry-reserved, blocked, collision-list, or premium-priced status, nor
a registration made since. **A human must confirm at a registrar.**

`search_execution: partial` for HTTP content of `touchstone.xyz`, `gettouchstone.com` and
`touchstone.app` (no HTTP response after four retries) and `not_completed` for the
`touchstone.io` redirect destination (HTTP 403). Registration status is unaffected — that
was settled by RDAP.

---

## 5. ENS

Authoritative — on-chain `eth_call` at Ethereum mainnet block **25,756,018**, contract
addresses taken from `ensdomains/ens-contracts` deployments and cross-checked (the `.eth`
registry owner equals the deployed BaseRegistrar).

| Name | `search_result` | Detail |
|---|---|---|
| `touchstone.eth` | **match_found** | `available` = false; expires **2027-04-23T15:51:47Z** (grace to 2027-07-22). Wrapped; beneficial owner `0x3dDF19947022fd6aceb5b079E158dA69464Ad658`. All text records (`url`, `com.twitter`, `description`, `email`) empty — no public identity attached |
| `touchstonelabs.eth`, `touchstone-labs.eth`, `touchstonerwa.eth`, `touchstone-rwa.eth`, `touchstonefinance.eth` | `no_match_found` | `nameExpires` = 0, `available` = true, registry owner `0x0` at that block |

Caveat: `available()` reflects registrar state only; the ETHRegistrarController separately
enforces label validity and pricing. All five labels are ≥3 characters so the answer should
hold, but this was not verified against the controller.

---

## 6. Crypto trackers and explorers

| Source | `search_execution` | `search_result` | Detail |
|---|---|---|---|
| CoinGecko search + full coin list (18,412 coins parsed) | complete | `no_match_found` | No coin named or symbolled Touchstone/TSTONE |
| CoinGecko inactive/delisted | **not_completed** | indeterminate | HTTP 401 — pro-API only. Delisted tokens unchecked |
| CoinMarketCap (5 endpoints + web) | **not_completed** | **indeterminate** | HTTP 404, *"system is busy"*, 503 `no healthy upstream`, DNS blocked, `ETIMEOUT` |
| DefiLlama protocols (8,056 parsed) | complete | `no_match_found` | — |
| DefiLlama raises / curators | **not_completed** | indeterminate | HTTP 402 (paid) / HTTP 404. **The live collision is a Morpho *curator*, so this surface is unchecked** |
| Dexscreener (4 queries) | complete | `no_match_found` | No pool-bearing Touchstone token on indexed chains |
| Blockscout Ethereum | complete | **match_found** | The two vault tokens in §1.2 |
| Blockscout Base | complete | **match_found** | Four dormant tokens: TCHN `0x911e…dd17`, TCHSTN `0x8480…8b07`, "Touchstone Demo Cover", "Midas Touchstone" — 2–6 holders each, no price, no liquidity |
| Etherscan / Basescan / Arbiscan / BscScan token search | complete | `no_match_found` | **See the calibration warning below** |
| Apple App Store (36 results) | complete | match_found | None crypto: Touchstone Investments Mobile, Touchstone Fireplace, Touchstone Connect, Touchstone Golf, etc. |
| Google Play | complete | match_found | None crypto: Touchstone Funds, Touchstone CRM, Touchstone Recovery, etc. |
| DappRadar | **not_completed** | indeterminate | HTTP 404 on the search route (homepage returns 200) |

**Calibration warning that governs this whole section.** Etherscan's token search returned
**empty** for "touchstone" while two Touchstone-named tokens demonstrably exist on Ethereum,
and Basescan returned empty while four exist on Base. Those indexes cover labelled or priced
tokens only. **This is direct proof, within this report, that `no_match_found` on a tracker
does not mean the name is unused on that chain.**

### X Layer — the intended deployment chain, and the weakest coverage here

| Source | `search_execution` | `failure` |
|---|---|---|
| OKLink X Layer explorer | **not_completed** | 301 → `oklink.com/x-layer/evm/search`, then **HTTP 404**; results render client-side |
| OKX Web3 X Layer explorer | **not_completed** | **HTTP 404**; the "touchstone" strings in the HTML are query echoes in `og:url`, not results |
| OKX official X Layer token lists | complete | `no_match_found` — but the lists hold only 10 and 2 tokens (curated); near-zero coverage |
| GeckoTerminal (x-layer) | complete | `no_match_found` — coverage proven (20 live pools returned), but covers pool-bearing tokens only |
| Routescan chain 196 | **not_completed** | `400 BLOCKCHAIN_NOTFOUND` — does not index X Layer |

**An unlisted contract named Touchstone on X Layer would be invisible to every check that
completed.** Before X Layer is treated as clear, this must be re-run by a method that does
not depend on a JavaScript explorer — an OKLink API key, or an indexed scan of ERC-20
`name()`/`symbol()` on chain 196.

---

## 7. Social handles

`legal_status: not_assessed`. Checks ran 2026-08-14 21:28–21:41Z.

| Platform | Handle | `search_result` | Holder / state |
|---|---|---|---|
| Farcaster | `touchstone` | **match_found** | fid 3339303, fname registered 2026-06-29, owner `0x5fd7…600a`. Bio: *"Verify before you pay… x402 on Base"* → `touchstone.locomot.io`. Account dormant (6 casts, last 2026-06-30); product live |
| Telegram | `touchstone` | **match_found** | Username broker — bio *"Wanna buy? DM: @kalloc"*, 10 subscribers. Confirmed independently on Fragment: **status "For sale", 200 TON** |
| LinkedIn | `touchstone` | **match_found** | 301 → `/company/boundlessxtouchstone` — "Touchstone (powered by Boundless)", advertising agency, Ohio, 5,359 followers |
| X | `touchstonehq` | **match_found** | Dormant since 2012, 0 tweets, 1 follower |
| X | `usetouchstone` | **match_found** | Parked 2023, branded avatar, 0 tweets, 0 followers |
| X | `touchstonelabs` | **match_found** | Dormant since 2011, 0 tweets |
| X | `touchstone`, `touchstone_xyz`, `touchstonerwa` | `no_match_found` | Do not resolve — **see caveat** |
| Farcaster | five variants | `no_match_found` | Empty fname registry, API 404 |
| Telegram | five variants | **indeterminate** | `t.me` returns a bare shell identical to a known-nonexistent control; also returned by private accounts. Not on Fragment |
| Discord | all six | `no_match_found` | Vanity-invite namespace only — **structurally weak**: vanities need Boost Level 3, and server names are not anonymously searchable |
| LinkedIn | five variants | `no_match_found` | HTTP 404, matching control |

**Caveat that applies to every non-resolving handle.** X returns 404 for never-registered,
**reserved**, deactivated and suspended handles alike. "Touchstone" is a common English word
and an existing commercial brand, so it is a plausible reserved name. **Only a human
attempting registration can distinguish "free" from "reserved" — and that is gated.**

Method note: anonymous fetches to x.com return HTTP 402, so X profile lookups used the
owner's existing logged-in burner session. **Read-only lookups only** — no follow, post,
like, or bookmark. X's keyword search returned HTTP 404 from that tool, so **no systematic
sweep of X for accounts *using* the name was possible** — only the six exact handles.

---

## 8. Similar and adjacent marks encountered

Verified at TSDR: KF TOUCHSTONE, TOUCHSTONE PATHWAYS, TOUCHSTONE FUNDS, TOUCHSTONE
INVESTMENTS, TOUCHSTONE VARIABLE ANNUITY (statuses in §2.1).

Secondary sources only, **unverified**: TOUCHSTONES, TOUCHSTONE CAPITAL, TOUCHSTONE
BENCHMARKING, DIAL TESTER BY TOUCHSTONE RESEARCH, TOUCHSTONE CLOSING & ESCROW, TOUCHSTONE
STRATEGIC LAW, TOUCHSTONE DISTRIBUTING, TOUCHSTONE VACATION RETREAT, TOUCHSTONE (cl 41,
motion pictures, cancelled), TOUCHSTONE SOFTWARE CORPORATION QUALITYTESTED (cancelled 2005).

No misspelling variants ("Touchston", "Toneston") surfaced in any search.

**Prominent unregistered or trading uses:** MIT Touchstone (institutional single sign-on —
the closest non-crypto neighbour to an identity/verification product), Touchstone Pictures
and Touchstone Television (Disney), Touchstone IQ (building-compliance SaaS), TouchStone
(retail fraud detection), Touchstone by AEGIS (healthcare FHIR conformance testing),
Touchstone Energy, Touchstone Exploration, Touchstone Medical Imaging, Touchstone
Semiconductor, and the Cambridge University Press "Touchstone" English course.

---

## 9. What a human must do

**Searches this environment could not complete:**

1. **USPTO full-text**, `https://tmsearch.uspto.gov/` — query "touchstone", filter classes
   9 / 35 / 36 / 42, live only. This is the enumeration that was impossible here, and where
   the missing plain **TOUCHSTONE class 36** registration and the **IFS Financial Services
   vs. Touchstone Advisors** ownership question get resolved.
2. **UK IPO**, `https://trademarks.ipo.gov.uk/ipo-tmtext/start` (403 to automated clients).
3. **TMview** `https://www.tmdn.org/tmview/` and **EUIPO eSearch plus**
   `https://euipo.europa.eu/eSearch/` (client-side rendering).
4. **WIPO Global Brand Database**, `https://branddb.wipo.int/en/` — pass the CAPTCHA in a
   real browser.
5. **Nigeria**, `https://iponigeria.fmiti.gov.ng/` → search sits behind
   `https://portal.iponigeria.com/auth/`; realistically needs an accredited agent or a
   manual search at the Abuja registry.
6. **X Layer**, by a non-JavaScript method — API key or an indexed `name()`/`symbol()` scan
   on chain 196.
7. **Domain availability** for the three no-record domains, confirmed at a registrar.
8. **Handle availability** on X and Telegram, by attempting registration (gated — owner
   only).

**Counsel questions, for the trademark opinion `ROADMAP.md` already requires before
commercial launch:**

- Does use of "Touchstone" for verification software applied to tokenized financial assets
  create a likelihood of confusion with **AIR Worldwide's class 9 registration** for risk-analysis
  software in insurance, or with **ProTec's class 42** mortgage-document SaaS?
- Does it create confusion with the **Touchstone Advisors class 36** financial-services
  family, given that this project's subject matter is financial disclosure?
- What is the significance of the unlocated plain **TOUCHSTONE** service mark asserted in
  Touchstone fund prospectuses, and of the owner discrepancy between the prospectus (IFS
  Financial Services) and TSDR (Touchstone Advisors)?
- What weight do the **unregistered but active** uses carry — specifically Touchstone-CV,
  whose product overlaps this project's function most closely, and the XAUE Morpho curator
  in the RWA space?
- Which jurisdictions matter, given operation from Nigeria and a global user base?

---

## 10. Conclusion of fact

The name "Touchstone" is **in active use by at least two 2026-vintage crypto or AI projects
whose function overlaps this project's**, is the subject of **at least three live US
trademark registrations** including one for risk-analysis software in the insurance field,
is **taken on every package registry checked**, on **ENS**, on **six of nine checked
domains**, and on **every social platform where a public check was possible**.

**No conclusion about legal risk or freedom to use is drawn here, and none may be inferred.
`legal_status` is `not_assessed` throughout.** Whether to keep or change the name is the
owner's decision, and any trademark conclusion requires qualified counsel.
