# Touchstone — Phase 0 Source Audit

**Audit date:** 2026-08-13 · **Auditor:** Claude (Fable 5) + three Opus 5 research agents
**Gate:** an asset enters the build only with an attributable official source, repeatable
no-login retrieval, ≥2 honest machine-observable controls, explicit cadence, hashable
evidence, and a sprint-feasible adapter. Abort Touchstone entirely if fewer than two
candidates pass.

All probes below were executed live from this development machine with plain `curl`
(realistic User-Agent, no cookies, no login). VPS re-verification is required at deploy
time; it is recorded per asset as a residual check.

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

- The 08/13 and 08/12 nav-daily rows carry identical values — controls must key on
  **row-date presence and freshness**, not on value change. Same-value days are normal.
- Holdings lag NAV (07/24 vs 08/13 today): holdings freshness control needs a
  generous grace period (~35-40 days) until cadence is observed longer.
- `subscription_nav_per_share` is null in recent rows — not usable as a control.

### Proposed controls (all empirically supported today)

1. `nav-row-freshness`: a nav-daily row exists for a date within N business days (grace for weekends/holidays).
2. `yield-freshness`: `as_of_date` within N business days.
3. `holdings-freshness`: `as_of_date` within ~40 days (provisional until cadence observed).
4. `nav-oracle-consistency`: API NAV/S vs onchain Chainlink oracle answer within tolerance (cross-source INCONSISTENT detector; pending Agent C oracle read).
5. `shares-supply-relationship`: API `outstanding_shares` vs onchain token supply drift tracking (exact relationship to be established conservatively — shares may span book-entry + multiple chains; observation-only until understood).
6. `aum-published`: AUM field present and parseable (observation, not judgment).

### Residual checks before mainnet claims

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

## OUSG / USDY (Ondo) — agent audit pending

Findings from the Ondo research agent will be recorded here.

## BENJI/FOBXX (Franklin Templeton) — agent audit pending

## PAXG (Paxos) — agent audit pending

## Onchain observability (all five assets) — agent audit pending

---

## Portfolio selection — provisional (finalize after agent reports)

- **Hero:** USTB — strongest machine-readable daily evidence found so far; public
  documented JSON API; dual onchain oracles for cross-source consistency; $958M real AUM.
- **Second daily asset:** target OUSG or USDY (pending Ondo agent) — needed for
  cross-issuer proof. BENJI possible if machine-retrievable.
- **Contrast asset:** PAXG (monthly attestation cadence + staleness semantics), pending
  direct-PDF retrievability from the Paxos agent.
- **Spare:** USCC (verified, same-issuer caveat).

**Gate state: 2 of 2 minimum candidates already PASS (USTB, USCC) — Touchstone proceeds.
Cross-issuer requirement still to satisfy from the pending agent audits.**
