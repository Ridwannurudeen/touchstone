# USDY retrieval: measured, and the bounded path that opens it

`manifests/sources/usdy.json` suspended this asset because one observation required pulling a
260 MB archive. That is still true of the **attestation** archive. It was never true of USDY's
daily portfolio data, and this document records what was measured on 2026-08-18 rather than
what was assumed.

## What was tested, and what each attempt actually returned

| Attempt | Result |
|---|---|
| `GET https://ondo.finance/usdy` | **HTTP 200, 1,544,898 bytes.** Bounded and well under the manifest's 4 MB ceiling. |
| Dropbox folder link, `dl=1`, with `Range: bytes=0-1023` | **HTTP 200 — not 206.** Dropbox ignored the range and returned **260,640,295 bytes**. Range-slicing the archive does not work. |
| Dropbox folder page, `dl=0` | HTTP 200, 222,128 bytes, and **no server-rendered listing** — no member filenames, no `/scl/fi/` per-file links, no listing JSON. The folder index is fetched by client-side script. |

So two candidate shortcuts are closed, and closed by measurement:

- **Byte-range extraction of one PDF from the zip is impossible** — the archive is generated on
  demand and served without range support, so the central directory cannot be read cheaply.
- **Enumerating the folder to fetch a single member is impossible without executing the page's
  scripts** or calling Dropbox's private listing endpoint. A private endpoint is not an
  ingestion contract and does not belong in an evidence pipeline.

The archive stays exactly as the manifest describes it: unbounded, and unusable on a daily
schedule.

## The part that was wrong

The suspension treated USDY as a single source. It is two, with different cadences and very
different retrieval costs, and only one of them is unbounded.

`https://ondo.finance/usdy` serves a **structured portfolio dataset inline in the HTML**, and it
carries considerably more than USTB's sources do. Observed on 2026-08-18, as of `2026-08-14`:

```
asOfDate               2026-08-14
collateralizationRatio 105.29
outstandingValue       2136304243.05555
total                  2154893984.54
averageDuration        160.12986430539013
averageYield           0.03709268004973276
```

alongside per-holding rows, each with `name`, `value`, `yield` and `maturityDays` — parsed
cleanly in the probe, e.g. `Ondo Stocks issued USDY - USD Value` at `10830015.22`, yield `3.6`.
The page also carries a dated series running to 1,082 date occurrences.

## Why this matters beyond unblocking one asset

Every control USTB currently ships is a **presence or freshness** check: a named field exists, a
date is recent. That is honest but thin, and the audit said so.

This source supports a **coverage predicate**: `total >= outstandingValue`, reserves against
liabilities, which the issuer itself summarises as `collateralizationRatio`. That is a claim
about solvency rather than about publication, and it is the first evidence surface in this
project that can carry one. On the measured figures the reserve exceeds the outstanding value by
18,589,741.49.

It should be approved with its limits stated: this is the **issuer's own arithmetic on the
issuer's own page**, not an independent audit, and Touchstone verifying it proves the issuer
published those figures and that they are internally consistent — never that the assets exist.
The attestation archive is what would speak to that, and it remains out of daily reach.

## What this changes in the manifest

`ondo-usdy-page` is described today only as **link rediscovery** for the archive, with
`fixture_disposition: exempt` because the page carries a rotating credential. Both need to
change: it is a data source in its own right, and a fixture can be committed if the Dropbox
links are stripped, since the portfolio payload is not credential-bearing.

The credential rule is unaffected and still binds — the rotating `rlkey` must never be
persisted, and nothing here needs it.

## What is still genuinely out of reach

Daily third-party attestations. They exist only inside the 260 MB archive, and no bounded route
to a single member was found. USDY can therefore ship daily **portfolio and coverage** controls
now, and cannot ship a daily **attestation** control at all. Saying otherwise on the dossier
would be the exact failure this project exists to refuse.

---

# FOBXX: the regulator path was never closed

Recorded here rather than in a second file because it is the same lesson: a status that says
*unavailable* is a claim, and claims get re-measured.

`SOURCE_AUDIT.md` had already verified FOBXX's SEC route and committed a fixture. The coverage
status still read as unavailable, because the **daily issuer feed** returns Cloudflare 403 —
and that was allowed to describe the whole asset.

Re-verified live 2026-08-18:

```
GET https://data.sec.gov/submissions/CIK0001786958.json
-> HTTP 200, 29,354 bytes, FRANKLIN TEMPLETON TRUST
   68 N-MFP3 filings listed
   latest: filed 2026-08-06, period 2026-07-31
```

Bounded, public, no credential, and served by a regulator rather than by the issuer whose
figures are being checked. It is the strongest provenance of any source in this project —
Chainlink's and RedStone's disclosure feeds read issuer APIs, not filings.

What it is not is daily. A money-market fund files N-MFP3 monthly with a four-to-six day lag,
so FOBXX carries **monthly** controls. The daily-liveness control and the
issuer-versus-regulator reconciliation both need the issuer feed and are the only things
actually waiting on it.

Stating that as "unavailable" understated the asset. The correct statement is that FOBXX ships
monthly on filed evidence, and one class of control on it is waiting for a route to the issuer.
