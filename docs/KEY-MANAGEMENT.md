# Key management

*Scope: PLAN-T6, 2026-08-15. Describes what the code enforces today. Nothing here is
legal, custody, or compliance advice, and nothing here has been reviewed by anyone
outside this project.*

Four identities exist. They are separate because each one fails differently, and keeping
them separate is what makes any single compromise survivable.

| Identity | Holds | Onchain authority | Present on the publishing host |
|---|---|---|---|
| Deployer | secp256k1 | Owns the registry. Authorizes, revokes and rotates publishers. | **No** |
| Publisher | secp256k1 | Append reports only. | Yes |
| Reporter | Ed25519 | None. | Only where reports are signed |
| Operations | secp256k1 | None. | No — it funds the publisher, it does not act for it |

**Deployer.** Constructs `TouchstoneRegistry` and is the only address the contract accepts
for `authorizePublisher`, `revokePublisher` and `rotatePublisher`. Its compromise is the
end of the deployment: an attacker could authorize themselves and publish indefinitely.
Nothing in the runtime needs it, so it is never on the publishing host, and it is never in
`.env`. The deploy script takes the deployer key from the operator at deploy time.

**Publisher.** Signs registry transactions. Its whole onchain authority is to append a
report at the next sequence. A stolen publisher key can publish a false report; it cannot
revoke anyone, cannot rewrite history, and cannot rotate authority. Recovery is the
deployer calling `rotatePublisher`, followed by a correction published at a new sequence
under the new key. The registry preserves the lineage across the rotation, so an
integrator gated on `isPublisherFor` keeps working.

**Reporter.** Signs observation reports with Ed25519 and touches no chain. Its compromise
is different in kind: it forges the *content* of a report rather than its placement, and
the registry cannot detect that — the contract checks who published, never what is true.
That is precisely why this key is never the publisher key. See the rollover section for
how it is replaced.

**Operations.** Funds gas and runs the host. It signs nothing this project publishes. Its
address is recorded in the manifest so top-ups are attributable and so the publisher can
be shown not to be running as it.

## What the code enforces

- The **three EVM role addresses** — publisher, deployer, operations — are required
  manifest fields, and a manifest cannot declare a publisher that is also the deployer or
  the operations address. The reporter is Ed25519 and has no EVM address, so it is not one
  of them; `publisher_identity_address` is publisher lineage rather than a fourth role,
  equals the publisher on a first authorization, and is refused if it is the deployer or
  the operations identity
  (`touchstone/deployment.py`). Requiring them is what makes the separation provable: they
  were optional at first, which quietly made the whole thing optional — a manifest that
  simply omitted the deployer and operations addresses passed every check while
  establishing nothing about either. Every downstream separation rests on this, because
  the publisher key is checked to derive exactly the declared publisher address, and that
  one comparison then guarantees it is neither of the others.
- A run refuses to start if `TOUCHSTONE_SIGNING_SEED` and
  `TOUCHSTONE_PUBLISHER_PRIVATE_KEY` are the same secret. The check is on the raw secret,
  not on the derived identifiers: two algorithms over one 32-byte secret produce two
  unrelated-looking public identities, so nothing downstream would notice.
  **This check only fires where both variables are present.** On the split-host setup
  described above — publishing on one host, reporting on another — neither host can see
  the other's secret, so the same 32 bytes could back both roles and each host would pass
  independently. Nothing detects that, and nothing can from inside one host. Treat it as
  an operator responsibility, not an enforced property.
- A publication verifies against the deployment's **active** reporting key, resolved from
  the manifest rather than supplied by the caller (`PublisherClient`). The rule lives
  there, not in the command-line wrapper, because a rule that only the wrapper applies is
  bypassed by anything that calls the client directly.
- A publication is only accepted as ours if the onchain report's `publisher` is the
  manifest's publisher. Another authorized publisher can place an identical payload at the
  same sequence, and matching content alone would have let reconciliation adopt it.
- A journalled transaction is decoded before it is ever rebroadcast, and its chain,
  destination registry, signer and nonce must match this deployment. Comparing a hash to
  its bytes proves only that they belong together.
- Preflight refuses to publish if the registry's `owner()` is the publisher, or if it is
  not the deployer the manifest declares.
- The deploy script refuses to deploy with the publisher as the deployer, or with the
  operations identity doubling as either.
- **No report publication is ever signed by a node.** There is no unlocked-account path
  in the publishing code, on any network, and a test asserts `.transact(` never reappears
  in `touchstone/publish.py`. This is deliberately narrower than "nothing is ever signed
  by a node": *deployment and owner-administration* calls do use an unlocked signer, in
  `contracts/scripts/deploy.js` under Hardhat and in the local E2E. Those are operator
  actions taken at a keyboard, not unattended ones, and they are not report publications.
- Preflight verifies publisher **lineage**, not only authorization. `publisherIdentity` is
  compared against the manifest, so an owner who authorizes a replacement publisher
  directly — creating a second, unrelated lineage — is refused rather than accepted.

## Environment variables

There is no `.env.example` in the repository: this machine's tooling blocks writing files
matching `.env*`, so the reference lives here instead. `.env` itself is gitignored.

Both key variables are exactly 64 lowercase hexadecimal characters with no `0x` prefix,
and the encoding is strict rather than normalised — a key that differs only by prefix or
case is a different string in every log, secret store and comparison, and quietly
accepting both would hide that.

**Publishing host**

| Variable | Meaning |
|---|---|
| `TOUCHSTONE_PUBLISHER_PRIVATE_KEY` | secp256k1 key. Must derive the manifest's `publisher_address`. |

**Reporting host**

| Variable | Meaning |
|---|---|
| `TOUCHSTONE_SIGNING_SEED` | Ed25519 seed. Must be the manifest's active reporting key. |

**Deployment (owner-operated, not the publishing host)**

| Variable | Meaning |
|---|---|
| `TOUCHSTONE_PUBLISHER_ADDRESS` | Address to authorize as publisher. |
| `TOUCHSTONE_OPERATIONS_ADDRESS` | Required. Recorded, never authorized. |
| `TOUCHSTONE_REPORTER_PUBLIC_KEY` | 32-byte Ed25519 public key, lowercase hex. |
| `TOUCHSTONE_NETWORK` | Manifest network name. |
| `TOUCHSTONE_RPC_URL` | Endpoint recorded in the manifest. |
| `TOUCHSTONE_CONFIRMATIONS` | Confirmation depth a publication must reach. |
| `TOUCHSTONE_MAX_FEE_WEI` | Fee ceiling. Required off the local chain. |
| `TOUCHSTONE_MANIFEST_OUT` | Where to write the generated manifest. |
| `TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID` | Must equal the chain id being deployed to. Leave unset. |

The confirmation variable is a positive statement of the exact chain id rather than a
boolean, because a stale `=1` left in a shell would silently enable a deployment to
whatever network happened to be configured.

## Reporting-key rollover

Rollover is additive, and that is the whole point. `touchstone.keyring.rolled_over`
returns a new manifest in which the outgoing key is `superseded` with the instant it
stopped signing, and the incoming key is `active`. Only the selection of the key for
*future* reports changes.

Nothing already published becomes cryptographically unverifiable. Two independent reasons:

1. Every verification bundle embeds its own published-key record, so `verify_bundle` never
   consults the manifest at all (`touchstone/verify.py`). A bundle from 2026 checks out in
   2030 with no network, no registry, and no knowledge of what has been rotated since.
2. The manifest keeps listing the superseded key, so anything resolving a key id — an
   operator, a dossier — still finds it and still finds it trusted.

**This is a claim about internal consistency, not about current trust, and the difference
matters most for revocation.** Because a bundle carries its own key, a bundle signed by a
*revoked* key still passes `verify_bundle`: the signature really was produced by that key
over those bytes. What revocation withdraws is the statement that the key's signatures
should be *believed*, and that statement lives in the manifest. A consumer that cares must
therefore check the key's state against the deployment manifest; verifying the bundle
alone will not tell them.

The three states are distinct claims:

- **active** — the only key that may sign or publish new reports. Exactly one key is
  active; a manifest with zero or two is refused. Both halves are enforced: signing checks
  the loaded seed against the active kid (`load_identities`), and publication refuses any
  kid that is not the active one (`scripts/publish_epoch.py`).
- **superseded** — no longer signs, still trusted for what it already signed. It cannot
  place a new report; that refusal is what makes rolling over mean anything.
- **revoked** — no longer to be trusted, retroactively, as a manifest-level statement.
  Excluded from `verification_keys()` and refused for publication. It does not and cannot
  invalidate the mathematics of a signature already published.

`revoked()` refuses to revoke the active key. A deployment with no active key cannot sign
anything, so a compromise is handled in two steps that each leave the deployment able to
operate: roll over to a replacement, then revoke the old key. `verification_keys()` drops
revoked keys, and the publishing CLI refuses to place a report signed by a key that is
revoked or absent from the manifest, however well-formed the report is.

**What rollover does not do.** It does not re-sign or re-publish anything, and it does not
mark which already-published reports were signed by a key that was later revoked. Deciding
what a revocation means for reports already onchain is a correction question — a
`publishCorrection` at a new sequence — and no automated path for that exists yet.

## Staged deployment

1. **Local.** `scripts/e2e_local.py` runs the full loop on a private Hardhat chain,
   signing locally exactly as production does. The publisher key is derived from Hardhat's
   published development mnemonic — still key material, and treated as such, but **no
   production secret** is involved and the key it derives controls nothing on any real
   network.
2. **Preflight against a real deployment.** `scripts/publish_epoch.py --preflight` runs
   every chain check and stops before signing. This is how a new deployment is proved
   reachable, correctly authorized and holding the expected bytecode **without sending a
   transaction**.
3. **Testnet, then mainnet.** Both are owner decisions and neither has been taken. The
   deploy script refuses to send until `TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID` names the
   exact chain. As of this document, **no transaction has been sent on any public
   network** — PLAN gate G1.

## Residual risks

- **Keys live in environment variables on the publishing host.** There is no HSM, no
  KMS integration, and no passphrase at rest. Anything that can read the process
  environment can publish. The mitigation today is scope: that key can only append
  reports.
- **No key ceremony, no threshold, no multisig.** The deployer is a single key. Its loss
  is unrecoverable and its theft is unrecoverable; the registry has no owner-rotation
  path.
- **Rotation is not automated.** `rotatePublisher` is called by hand by the deployer, and
  the manifest is updated by hand afterwards. Nothing detects that the two have diverged
  except the next preflight, which refuses — now on lineage as well as authorization.
- **Cross-host role separation is unverifiable from one host.** See the note above: the
  same secret behind both roles is caught only when both variables sit on one machine.
- **The template marker is a tripwire, not provenance.** Deleting the `notes` field makes
  a template's placeholder values load. Only preflight then refuses them, because no
  contract stands at the placeholder address. It stops a half-filled copy, not a
  determined one.
- **Compromise detection does not exist.** Nothing watches for publications from an
  unexpected publisher or reports signed by a retired key. PLAN-T7 and PLAN-T8 own that
  work; until then, detection is manual.
- **The reporting key signs unattended.** Where the epoch runs autonomously the Ed25519
  seed is present on that host for the whole run, so its exposure window is not narrower
  than the publisher key's.
