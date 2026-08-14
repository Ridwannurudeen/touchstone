# Captured evidence fixtures

These files are byte-for-byte copies of first-party JSON responses. They are immutable golden inputs; tests consume them without rewriting them.

| File | First-party source | Captured | Bytes | SHA-256 |
|---|---|---|---:|---|
| `ustb-nav.json` | `https://api.superstate.com/v1/funds/1/nav-daily` | 2026-08-13 14:16:17Z | 224837 | `4830bc348b621f70682cd41c0d48484987b6b5f3c1a99193e0ca33e7ccba3a25` |
| `ustb-nav-20260814.json` | `https://api.superstate.com/v1/funds/1/nav-daily` | 2026-08-14 17:08:12Z | 225074 | `5b6a53e00de2d0762a122780d08961f37d7d5dc8d71f909c208574e64dfe9fda` |
| `ustb-yield.json` | `https://api.superstate.com/v1/funds/1/yield` | 2026-08-13 14:16:17Z | 122 | `02ef1e14a867a5d034916021fecde3c6e555e78ac830ce78a8a3d6d49e55ce1f` |
| `ustb-holdings.json` | `https://api.superstate.com/v2/funds/1/holdings` | 2026-08-13 14:16:17Z | 5396 | `0c42d7949ccfbcd10256718199361daa4ff1c81ad90ba62c415fa173f8d22bdf` |
| `uscc-nav.json` | `https://api.superstate.com/v1/funds/2/nav-daily` | 2026-08-13 | 180286 | `8ae682980e0f524b3ceb08976b361ccc161d5efa1d0245109b9677b2ba72d8f2` |

The two `nav-daily` captures are retained as a pair: they are the evidence that the newest
rows of that feed are provisional and are revised in place. Between them the 08/12 and
08/13 row-dates were rewritten while all 952 older shared rows stayed byte-identical, which
is what `settled_after_business_days` in the USTB value controls is calibrated against.
See `SOURCE_AUDIT.md` for the full finding.
