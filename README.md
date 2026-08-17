# Touchstone

Touchstone turns the disclosures behind real-world assets into executable intelligence. AI compiles issuer-published commitments into cited, machine-checkable controls. Deterministic surveillance evaluates those controls against current evidence. Signed results are published to X Layer, where applications can enforce freshness and verification requirements through a shared registry.

Status: **Phase 1 in progress — a testnet registry is live, and nothing has been published to
it yet.** The registry is at `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on X Layer testnet
(chain 1952), deployed 2026-08-17 at block 38489602 under a recorded owner approval; see
`docs/DEPLOYMENT-G1-EXECUTED.md`. Its predecessor, deployed 2026-08-15, is **superseded**: it
predates the `epochKey` change that makes one-report-per-epoch enforceable on chain, so nothing
may publish to it, and nothing ever did.

**No report has been published to any registry.** The live registry holds zero reports; the
first will be a single USTB testnet canary. No mainnet deployment is scheduled — mainnet is
conditional on a proven testnet loop, not on a date, and is additionally blocked until the
deployer and publisher keys sit on separate hosts, which they currently do not.

Touchstone does not issue assets, custody funds, recommend investments, assign credit ratings, or claim facts beyond the evidence class it has actually verified.
