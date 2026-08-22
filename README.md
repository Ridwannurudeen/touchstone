# Touchstone

**RWA policy infrastructure for X Layer.** Touchstone compiles issuer-published
disclosures into cited, machine-checkable controls, evaluates them deterministically
against retained evidence, and publishes signed results to an append-only on-chain
registry — where consumer contracts, wallets and AI agents act only when the required
evidence policy is satisfied.

Status: **live on X Layer testnet and mainnet — 20 published reports, 15 `CONFIRMED` since 2026-08-19, v1 and v2 registries, pinned gates and admission controllers on both chains.**

- **Website:** https://touchstone.gudman.xyz · [dossier](https://touchstone.gudman.xyz/dossier) · [Policy Terminal](https://touchstone.gudman.xyz/app) · [the 90-second proof](https://touchstone.gudman.xyz/judge)
- **X:** [@touch__stone](https://x.com/TOUCH__STONE) — [launch post](https://x.com/TOUCH__STONE/status/2090844839055159485)
- **Release:** [`v0.1.0`](https://github.com/Ridwannurudeen/touchstone/releases/tag/v0.1.0), Ed25519-signed, reproducible
- **License:** Apache-2.0

Touchstone is not a price oracle, credit rating, or legal-compliance oracle. It
determines whether a specific, predeclared evidence policy is currently supported and
gives applications an enforceable answer. It does not issue assets, custody funds,
recommend investments, or claim facts beyond the evidence class it has actually
verified.

## How it works

```
issuer disclosures ──▶ AI control compiler ──▶ human approval (EIP-712 signed ledger)
      │                    (proposes only; never in the serving path)
      ▼
evidence store ──▶ deterministic evaluator ──▶ Ed25519-signed report
 (hash-chained          (refuses when                │
  captures)              evidence does               ▼
                         not qualify)      X Layer registries (v1 + v2)
                                                     │
                              ┌──────────────────────┤
                              ▼                      ▼
                        AssetGateV2          RWAAdmissionController
                  (pins policy, control      (admits, executes, or
                   root, approval digest)     refuses on the gate's word)
```

- A language model **proposes** controls citing byte-exact spans of the issuer's own
  disclosure. A human approves or declines each; every decision carries the approver's
  EIP-712 signature, and the ledger digest is bound into every report and pinned by the
  mainnet gate. The model never runs in the serving path — the daily result is
  deterministic.
- A value is `CONFIRMED` only when a qualifying capture at least 24 hours older still
  carries it unchanged. The system **refused** a provisional NAV on 2026-08-18 and
  confirmed the same value, `11.18208300`, on 2026-08-19 once the evidence qualified.
  Refusal and confirmation are one mechanism.
- Two consumer policies — `disclosure-freshness:1` and `nav-settlement:1` — publish
  their own verdicts under their own registry keys on both chains; Registry v2 carries
  relayer-submitted EIP-712 attestations whose publisher is recovered on chain.
- The production host has published its daily mainnet slot unattended since
  2026-08-20. No claim of long-run continuous operation is made — the recorded
  unattended window is stated, not extrapolated.

## Live deployments

Chain id is the **only** discriminator for several addresses below — the same address
can be a different live contract on the other chain. Full tables with every
transaction: [network addresses](https://touchstone.gudman.xyz/developers) and the
[dossier](https://touchstone.gudman.xyz/dossier).

| Contract | X Layer mainnet (196) | X Layer testnet (1952) |
|---|---|---|
| Registry v1 | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` |
| Registry v2 | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` | `0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720` |
| AssetGateV2 | `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` | `0xE1e2C897A43674bba6c3fbE6584a703a09939930` |
| RWAAdmissionController | `0x5C5265392701A99cbB137aF8116E0F97f630329A` | `0x1822Cde72cD1aB560d8fdD795Ac6971b122BbA28` |

All live contracts are source-verified on OKLink. The admission story is real
transactions on both chains: propose, activate on the gate's word, execute, and a
refused activation left on chain as a citable revert.

## Verify a report yourself

The bundle is the record; the site is only a rendering of it.

```sh
# offline, from a downloaded bundle (no network, no trust in this repository's site)
python -m touchstone.verify eip155-196-ustb-2026-08-21-5.json
```

Or drop any bundle onto the [Policy Terminal](https://touchstone.gudman.xyz/app): the
checks run in your browser — Ed25519 signature, complete canonical equality, the signed
approval ledger, policy identity, compilation binding, and the Registry v2 attestation
compared against what the chain actually stores. The panel names what it does **not**
check. Eighteen of the twenty published reports have a downloadable bundle on the
[dossier](https://touchstone.gudman.xyz/dossier); the two 2026-08-19 testnet policy
artifacts overwritten before filenames became chain-aware are identified there
explicitly, with their signed reports retained in the transparency logs.

## Quickstart (development)

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest -q --strict-markers      # full engine suite

(cd contracts && npm ci && npm test)      # Hardhat contract suite
(cd sdk && npm ci && npm test)            # TypeScript SDK + Policy Terminal tests
python scripts/mutation_check.py          # mutation harness (clean tree required)
```

CI runs the same matrix on every push — pytest on 3.11 and 3.12, contracts, SDK and
Terminal tests, a managed local-chain E2E, ruff, the mutation harness, and a
public-truth gate that fails the build if any public page or document disagrees with
the canonical chain facts. `main` requires the aggregate check.

## Repository layout

| Path | What lives there |
|---|---|
| `touchstone/` | the engine: evidence store, evaluator, signing, publication, verification |
| `contracts/` | Solidity: registries, gates, admission controller, Hardhat suite |
| `sdk/` | TypeScript SDK (`@touchstone/sdk`): clients, indexer, ERC-8021 attribution |
| `scripts/` | operational entry points — the daemon, publishers, release builder, gates |
| `site2/` | the public site, generated from sources + one canonical facts file |
| `docs/` | runbooks, threat model, limitations, audit traceability, submission draft |
| `data/` | approved control sets, signed approval ledger, policy manifests |
| `deployments/` | chain deployment manifests (machine-validated, state-declaring) |
| `tests/` | the Python suite |

Key documents: [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) (what is deliberately not
claimed), [`docs/OPERATIONS.md`](docs/OPERATIONS.md) (how it runs, incidents included),
[`docs/AUDIT-RESPONSE.md`](docs/AUDIT-RESPONSE.md) (five external reviews, finding by
finding), [`docs/KEY-MANAGEMENT.md`](docs/KEY-MANAGEMENT.md).

## Release and integration proof

- [`v0.1.0`](https://github.com/Ridwannurudeen/touchstone/releases/tag/v0.1.0) is a
  reproducible release of commit `c6908f00058c44f57251ca1dab446cbc16300ce6`. Its
  release set is Ed25519-signed by the active reporter identity recorded in the X Layer
  mainnet deployment manifest; the release includes the signature, checksums, deterministic
  source archive, project state, and CI-bound manifest.
- [Blvck Protocol PR #1](https://github.com/anyathebrand-prog/blvck_protocol/pull/1)
  proposes an independently testable Touchstone evidence adapter. The open PR verifies
  retained bundles against an out-of-band trusted key and rejects tampering, self-signed
  attacker bundles, unsupported versions, and expired reports. It is integration proof, not
  a claim of adoption or endorsement while the PR remains unmerged.
- Builder Code `f0axgs7smtk2nfa7` is active in the live Terminal. Its first attributed
  mainnet admission execution — transaction `b48cf6182b7bf87d…61fa9a1e` — succeeded,
  emitted `AssetUsed`, and is labelled with the code in the
  [admission controller's transaction history](https://www.oklink.com/xlayer/address/0x5C5265392701A99cbB137aF8116E0F97f630329A)
  on the X Layer explorer.

## Honesty rules

Every number above is rendered from one canonical facts file that CI checks against the
chain-derived project state; a hand-edited page or a stale count fails the build. Where
something is unproven, the documentation says so — `docs/LIMITATIONS.md` records every
missed target as missed rather than restated. The registry provides append-only
publication integrity with bounded publisher authority; it does not verify the Ed25519
report signature on chain. That check is what the offline bundle performs, on your
machine, against bytes this site cannot alter.
