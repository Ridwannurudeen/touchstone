# Captured evidence fixtures

These files are byte-for-byte copies of first-party JSON responses. They are immutable golden inputs; tests consume them without rewriting them.

| File | First-party source | Captured | Bytes | SHA-256 |
|---|---|---|---:|---|
| `ustb-nav.json` | `https://api.superstate.com/v1/funds/1/nav-daily` | 2026-08-13 14:16:17Z | 224837 | `4830bc348b621f70682cd41c0d48484987b6b5f3c1a99193e0ca33e7ccba3a25` |
| `ustb-nav-20260814.json` | `https://api.superstate.com/v1/funds/1/nav-daily` | 2026-08-14 17:08:12Z | 225074 | `5b6a53e00de2d0762a122780d08961f37d7d5dc8d71f909c208574e64dfe9fda` |
| `ustb-yield.json` | `https://api.superstate.com/v1/funds/1/yield` | 2026-08-13 14:16:17Z | 122 | `02ef1e14a867a5d034916021fecde3c6e555e78ac830ce78a8a3d6d49e55ce1f` |
| `ustb-yield-20260814.json` | `https://api.superstate.com/v1/funds/1/yield` | 2026-08-14 17:08:12Z | 122 | `7f2a26430697e35096cf2e5d5bb225d6886b78dc25be4b543383fd4524db5a2c` |
| `ustb-holdings.json` | `https://api.superstate.com/v2/funds/1/holdings` | 2026-08-13 14:16:17Z | 5396 | `0c42d7949ccfbcd10256718199361daa4ff1c81ad90ba62c415fa173f8d22bdf` |
| `uscc-nav.json` | `https://api.superstate.com/v1/funds/2/nav-daily` | 2026-08-13 | 180286 | `8ae682980e0f524b3ceb08976b361ccc161d5efa1d0245109b9677b2ba72d8f2` |
| `fobxx-nmfp3-20260731.xml` | `https://www.sec.gov/Archives/edgar/data/1786958/000207169126017542/primary_doc.xml` | 2026-08-15 00:01Z | 167751 | `763d0116755049f97f804ca41b05a2f297f000d10229088658ef45a023995c5a` |

The 2026-08-13 and 2026-08-14 captures are retained as a pair: they are the evidence that
the newest rows of the `nav-daily` feed are provisional and are revised in place. Between
them the 08/12 and 08/13 row-dates were rewritten while all 952 older shared rows stayed
byte-identical. That pair is also what the value controls consume — they observe only a row
confirmed unchanged across both captures — so the two dates together, not either alone,
are the golden input for the evaluator. The holdings response was byte-identical across
both captures and is therefore not duplicated. See `SOURCE_AUDIT.md` for the full finding.

Every fixture belonging to a portfolio asset is declared in `manifests/sources/*.json` with
its byte length and digest, and `tests/test_portfolio_fixtures.py` fails if a declared
fixture is missing or its bytes move. `uscc-nav.json` is deliberately **not** declared: USCC
is a same-issuer spare that is not in the portfolio, and it is retained only as a second
Superstate schema sample.

Two fixtures named by the plan are **absent, deliberately and on the record**. The FOBXX
daily feed could not be captured because the issuer endpoint returns Cloudflare HTTP 403
from this environment, and the USDY attestation could not be captured because the archive is
served only as a single 260 MB zip. Both are recorded in their manifests with the exact
blocker and the human follow-up, rather than left as unexplained gaps.
