# Final owner-controlled actions

Repository work can make technical claims checkable. It cannot create third-party adoption,
register account-bound identifiers, use keys it does not hold, publish a social post, or submit
a form without the owner's explicit approval and account access.

## Eligibility — mandatory

- [x] Dedicated Premium project X account created: `@touch__stone`.
- [x] Published the reviewed launch post mentioning `@XLayerOfficial`:
  https://x.com/TOUCH__STONE/status/2090844839055159485
- [x] Recorded the exact live X post URL in `docs/SUBMISSION-DRAFT.md`.
- [ ] Add the owner's contact email and prize wallet to `docs/SUBMISSION-DRAFT.md`.
- [x] Fresh mainnet policy publication confirmed 2026-08-21: both policy keys reached v1 and
  Registry V2 sequence 3, and two independent RPCs returned `allowed` from the live gate.
- [ ] Submit the official Google Form before **2026-08-21 23:59 UTC**.
- [ ] Preserve the form receipt and final submitted text.

## X Layer ecosystem attribution

- [ ] Register a 16-character X Layer Builder Code in the OKX developer portal.
- [ ] Put the exact registered value in `site2/_pages/app.html`; do not invent one.
- [ ] Rebuild and deploy the site.
- [ ] Execute one legitimate mainnet admission action through the Terminal.
- [ ] Verify attribution on the explorer and link the transaction from `/judge`.

The Terminal intentionally sends ordinary, unattributed calldata while `builderCode` is
`null`, and rejects malformed configured values.

## External adoption

Public integration proposal prepared and opened:
https://github.com/anyathebrand-prog/blvck_protocol/pull/1. The adapter verifies Touchstone
bundles against an independently supplied reporter key and passed 85 package tests. This is
verifiable integration work, not adoption, endorsement, or a partnership while the PR is open.

Still obtain one independently operated integration. Strong evidence is another X Layer project
importing `ITouchstoneGate` or `@touchstone/sdk`, operating its own consuming contract and
wallet, performing a policy-bound action, and announcing the integration. Another wallet
controlled by Touchstone is not external adoption.

## Signed release

- [x] Published reporter-signed release `v0.1.0`:
  https://github.com/Ridwannurudeen/touchstone/releases/tag/v0.1.0
- [x] Re-downloaded every release asset, verified every checksum, verified the Ed25519
  signature over the exact release set, and matched its key to the active mainnet reporter.
- [ ] Obtain an independent release or contract audit; a reporter signature proves origin and
  integrity, not independent review.

## Operations and security

- [x] First successful unattended mainnet publication.
- [ ] Demonstrate restart and recovery before another scheduled slot.
- [ ] Establish a multi-day measured publication window.
- [ ] Move owner and publisher authority to stronger custody, preferably multisig plus
  managed signing.
- [ ] Separate proposer/operator and approval roles.
- [ ] Obtain an independent security review; judge-perspective critiques are not audits or
  certifications.

## Product breadth

- [x] Published fresh chain-aware testnet policy bundles for both current policies on
  2026-08-21. The two overwritten 2026-08-19 testnet artifacts remain historical gaps and are
  identified in the dossier; the Terminal no longer depends on them for the current policy state.
- [ ] Complete a second live asset with a materially different authority class; FOBXX
  regulator filings remain the strongest documented candidate.
- [ ] Resolve the documented Touchstone brand collisions before treating the name as a
  permanent grant-funded identity.
