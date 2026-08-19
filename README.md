# Touchstone

Touchstone turns the disclosures behind real-world assets into executable intelligence. AI compiles issuer-published commitments into cited, machine-checkable controls. Deterministic surveillance evaluates those controls against current evidence. Results are published to X Layer as publisher-authenticated onchain commitments; the signed report and verification bundle remain offline-verifiable artifacts. A testnet AssetGate can enforce freshness and verification requirements through the shared registry.

Status: **Phase 1 in progress — registries are live on X Layer testnet and mainnet, holding
five published reports between them. Every one of them reports `UNVERIFIABLE`.** The registry is at `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` on X Layer testnet
(chain 1952), deployed 2026-08-17 at block 38489602 under a recorded owner approval; see
`docs/DEPLOYMENT-G1-EXECUTED.md`. Its predecessor, deployed 2026-08-15, is **superseded**: it
predates the `epochKey` change that makes one-report-per-epoch enforceable on chain, so nothing
may publish to it, and nothing ever did.

**Five reports have been published: three on testnet, two on mainnet.** Every one reports
`UNVERIFIABLE` — the honest result, because a value control needs a capture at least 24 hours
older and no run has yet had one that qualified. Nothing has ever reached `CONFIRMED`.

The latest report on each chain is a **correction**. Sequence 1 on both chains was signed
carrying an operational event a first publication cannot have, and signed bytes are not
editable, so each was restated through the registry's `publishCorrection` entry point rather
than edited or replaced. A correction must reproduce the roots of the report it restates.

Mainnet went live on 2026-08-18 at registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`
(chain 196). ⚠️ **That same address is the superseded registry on chain 1952** — same deployer,
nonce 0 — so the chain id, not the address, is what identifies a deployment here. Mainnet
proceeded under owner direction while the deployer and publisher keys still sit on one host,
which this repository said should block it; that deviation is disclosed in
`docs/OPERATIONS.md` rather than resolved.

**No claim of continuous operation is made.** Every slot so far was hand-started.

Touchstone does not issue assets, custody funds, recommend investments, assign credit ratings, or claim facts beyond the evidence class it has actually verified.
