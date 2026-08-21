# Touchstone

Touchstone is the policy and control plane for tokenized assets. Its current USTB vertical
compiles issuer-published disclosures into cited, machine-checkable controls, evaluates them
deterministically against retained evidence, and publishes signed results to X Layer. The
product is designed to connect issuer, regulator, custodian, oracle and onchain evidence to
the contracts, wallets and AI agents that act on it.

**Grant wedge:** Touchstone makes RWA liquidity conditional on verifiable evidence.

Touchstone is not a price oracle, credit rating, or legal-compliance oracle. It determines
whether a specific, predeclared evidence policy is currently supported and gives applications
an enforceable answer.

Status: **live on X Layer testnet and mainnet — 20 published reports, 15 `CONFIRMED` since 2026-08-19, v1 and v2 registries on both chains.** The registry is at `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on X Layer testnet
(chain 1952), deployed 2026-08-17 at block 38489602 under a recorded owner approval; see
`docs/DEPLOYMENT-G1-EXECUTED.md`. Its predecessor, deployed 2026-08-15, is **superseded**: it
predates the `epochKey` change that makes one-report-per-epoch enforceable on chain, so nothing
may publish to it, and nothing ever did.

**Twenty reports have been published — fifteen of them `CONFIRMED`.** The system refused a
provisional NAV on the 18th and confirmed the same value, `11.18208300`, on the 19th once a
second capture at least a day older still carried it unchanged: refusal and confirmation are
one mechanism. Two consumer policies — `disclosure-freshness:1` and `nav-settlement:1` —
publish their own verdicts under their own registry keys on both chains, RegistryV2 carries
relayer-submitted EIP-712 attestations whose publisher is recovered on chain, and a
GuardedAction pair on each chain shows the consequence: one permitted transaction and one
genuine on-chain revert. On mainnet, `AssetGateV2` pins the approved policy, control-set
root and signed approval-ledger digest, and `RWAAdmissionController` consumes it — propose,
activate, execute and a refused activation, all real transactions. Every report's bundle
verifies offline; eighteen of twenty are downloadable from the dossier. Fresh chain-aware
testnet policy bundles are available for the 2026-08-21 publications; only the two historical
2026-08-19 testnet policy artifacts overwritten before chain-aware filenames remain unavailable,
and the dossier identifies them explicitly.

**No claim of continuous operation is made.** The production host has published its daily
slot unattended since 2026-08-20 — two daily slots are now recorded.

Touchstone does not issue assets, custody funds, recommend investments, assign credit ratings, or claim facts beyond the evidence class it has actually verified.

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
  [mainnet admission execution](https://web3.okx.com/explorer/xlayer/tx/0xb48cf6182b7bf87df78817401c7fefc2e8a319b341b96e572552775361fa9a1e)
  succeeded, emitted `AssetUsed`, and is labelled with the code on the X Layer explorer.
