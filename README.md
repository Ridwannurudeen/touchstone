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
verifies offline; eighteen of twenty are downloadable from the dossier and the two gaps are
stated there plainly.

**No claim of continuous operation is made.** The production host has published its daily
slot unattended since 2026-08-20 — two daily slots are now recorded.

Touchstone does not issue assets, custody funds, recommend investments, assign credit ratings, or claim facts beyond the evidence class it has actually verified.
