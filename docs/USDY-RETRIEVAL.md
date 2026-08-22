# USDY retrieval research: one bounded candidate source, suspension unchanged

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

## What the second source changes — and what it does not

The first suspension analysis treated USDY as a single source. It has two surfaces, with
different cadences and retrieval costs. Finding a bounded issuer page makes that page a
research candidate; it does not lift the asset suspension because the third-party attestation
route remains unbounded and no USDY parser, fixture, approved control or adapter exists.

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

**Correction, 2026-08-18, found in audit.** The paragraph above originally said these figures
are "internally consistent". They are not, and the claim was mine, not the issuer's.

```
total / outstandingValue = 2154893984.54 / 2136304243.05555 = 100.87018%
issuer's published collateralizationRatio       = 105.29
```

A 4.42 percentage-point gap. For 105.29% to hold against that total, the liability base would
have to be about 2,046,627,395 — roughly 89.7 million below the published `outstandingValue`.
The probe also parsed only three holding rows totalling about 11.3 million against a stated
total of 2.15 billion, so the extraction was incomplete as well.

Two possibilities, and this document does not get to pick between them without evidence: the
fields are scoped differently than their names suggest, or the published ratio does not
reconcile with the published components. **Either way, a control asserting consistency cannot
be written until the field definitions are established from the issuer's own documentation.**

That is the finding, and it is a better argument for the product than the one it replaces: the
first serious look at a new issuer's numbers turned up a discrepancy in their own published
figures. It is also exactly why `total >= outstandingValue` must not be sold as a solvency
proof. At most it could show that two published fields satisfy an inequality. It would not show
that reserves exist, are eligible, are unencumbered, are scoped to these liabilities, or cover
them — and none of that is reachable from a page the issuer renders.

The limits stand as originally written and are worth restating plainly: this is the issuer's own
arithmetic on the issuer's own page, not an independent audit. The attestation archive is what
would speak to the assets existing, and it remains out of daily reach.

**What this source needs before any control is approved on it:** a strict parser rather than a
regex, a committed sanitised fixture, drift detection against the payload's shape, field
definitions taken from issuer documentation rather than inferred from field names, and the
provenance limit stated on the dossier. It is a candidate bounded source. It is not yet a
retrieval contract.

## What this changes in the manifest

`ondo-usdy-page` is described today only as **link rediscovery** for the archive, with
`fixture_disposition: exempt` because the page carries a rotating credential. Both need to
change: it is a data source in its own right, and a fixture can be committed if the Dropbox
links are stripped, since the portfolio payload is not credential-bearing.

The credential rule is unaffected and still binds — the rotating `rlkey` must never be
persisted, and nothing here needs it.

## What is still genuinely out of reach

Daily third-party attestations. They exist only inside the 260 MB archive, and no bounded route
to a single member was found. The bounded issuer page may support future portfolio-control
research only after its field discrepancy is resolved and a strict parser, sanitised fixture
and approved controls exist. USDY remains suspended, with no adapter or live publication.

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

That 2026-08-18 measurement later became one input to the FOBXX adapter. The current
publication status is rendered from `manifests/sources/fobxx.json` above; this section preserves
the research sequence rather than restating today's status by hand.
