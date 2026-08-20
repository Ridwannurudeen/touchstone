# Touchstone

Touchstone turns the disclosures behind real-world assets into executable intelligence. AI compiles issuer-published commitments into cited, machine-checkable controls. Deterministic surveillance evaluates those controls against current evidence. Results are published to X Layer as publisher-authenticated onchain commitments; the signed report and verification bundle remain offline-verifiable artifacts. A testnet AssetGate can enforce freshness and verification requirements through the shared registry.

Status: **live on X Layer testnet and mainnet — 11 published reports, 6 `CONFIRMED` since 2026-08-19, v1 and v2 registries on both chains.** The registry is at `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on X Layer testnet
(chain 1952), deployed 2026-08-17 at block 38489602 under a recorded owner approval; see
`docs/DEPLOYMENT-G1-EXECUTED.md`. Its predecessor, deployed 2026-08-15, is **superseded**: it
predates the `epochKey` change that makes one-report-per-epoch enforceable on chain, so nothing
may publish to it, and nothing ever did.

**Fourteen reports have been published — nine of them `CONFIRMED`.** The system refused a
provisional NAV on the 18th and confirmed the same value, `11.18208300`, on the 19th once a
second capture at least a day older still carried it unchanged: refusal and confirmation are
one mechanism. Two consumer policies — `disclosure-freshness:1` and `nav-settlement:1` —
publish their own verdicts under their own registry keys on both chains, RegistryV2 carries
relayer-submitted EIP-712 attestations whose publisher is recovered on chain, and a
GuardedAction pair on each chain shows the consequence: one permitted transaction and one
genuine on-chain revert. On mainnet, `AssetGateV2` pins the approved policy, control-set
root and signed approval-ledger digest, and `RWAAdmissionController` consumes it — propose,
activate, execute and a refused activation, all real transactions. Every report's bundle
verifies offline; twelve of fourteen are downloadable from the dossier and the two gaps are
stated there plainly.

**No claim of continuous operation is made.** The production host has published its daily
slot unattended since 2026-08-20 — exactly one day of that record exists.

Touchstone does not issue assets, custody funds, recommend investments, assign credit ratings, or claim facts beyond the evidence class it has actually verified.
