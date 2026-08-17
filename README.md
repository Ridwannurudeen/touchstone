# Touchstone

Touchstone turns the disclosures behind real-world assets into executable intelligence. AI compiles issuer-published commitments into cited, machine-checkable controls. Deterministic surveillance evaluates those controls against current evidence. Signed results are published to X Layer, where applications can enforce freshness and verification requirements through a shared registry.

Status: **Phase 1 in progress — a testnet registry is live and holds its first published
report.** The registry is at `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on X Layer testnet
(chain 1952), deployed 2026-08-17 at block 38489602 under a recorded owner approval; see
`docs/DEPLOYMENT-G1-EXECUTED.md`. Its predecessor, deployed 2026-08-15, is **superseded**: it
predates the `epochKey` change that makes one-report-per-epoch enforceable on chain, so nothing
may publish to it, and nothing ever did.

**One report has been published, on testnet.** The live registry holds USTB sequence 1,
published 2026-08-17, state `UNVERIFIABLE` — the honest result for a first epoch, because a
value control needs a capture at least 24 hours older and none existed yet. No mainnet
deployment is scheduled — mainnet is
conditional on a proven testnet loop, not on a date, and is additionally blocked until the
deployer and publisher keys sit on separate hosts, which they currently do not.

Touchstone does not issue assets, custody funds, recommend investments, assign credit ratings, or claim facts beyond the evidence class it has actually verified.
