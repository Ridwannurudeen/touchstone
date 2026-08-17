# Canary: one live USTB epoch on X Layer testnet

**Prepared, not executed.** This is critical-path item 6 and an owner gate. Everything below
was verified against the live chain and the committed source on 2026-08-17; nothing here has
been run. The command in §5 is the whole action, and it is the owner's to authorise.

The plan's instruction for this item is explicit: **publish whatever emerges, including
`UNVERIFIABLE`.** That is what makes it a canary rather than a demo, and §4 is the reason it
matters more than it sounds.

---

## 1. What this does

Runs the unattended daemon for exactly one slot against the live Superstate endpoint and the
live registry: retrieve, store evidence, normalise in an isolated worker, evaluate the
approved controls, sign a report, publish it, and write a verifying bundle. `--max-runs 1` is
the canary mode named in `scripts/run_service.py`; omitting it serves until stopped.

It is the first time this system will publish to a public chain.

## 2. Preconditions — verified on chain 2026-08-17

| Fact | Value | How checked |
|---|---|---|
| RPC reachable | `https://testrpc.xlayer.tech/terigon`, head 38516252 | `web3.is_connected()`, `eth.block_number` |
| Chain id | 1952 | `eth.chain_id` |
| Registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` | `eth.get_code` returns 6147 bytes |
| Publisher authorised | `true` | `isPublisherAuthorized(0x86A1…4710)` |
| Publisher identity | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` | `publisherIdentity(0x86A1…4710)` |
| Publisher balance | 0.05 OKB | `eth.get_balance` |
| Publisher nonce | **0 — has never sent a transaction** | `eth.get_transaction_count` |
| Manifest state | `active` | `deployments/xlayer-testnet-2.json` |
| Confirmations | 3 | same manifest |

⚠️ The **superseded** registry `0xc9d58e44…D30d` must never be published to; it has no
`epochSequence`. `deployments/xlayer-testnet.json` is marked `superseded` and `main()` refuses
it before reading any key.

## 3. Cost, and why gas is not a risk here

Gas price read live: **20000001 wei** (~0.02 gwei). One `publish` measured at **279,905 gas**
in the contract suite's own snapshot, on a local chain — indicative, not a promise.

- One publication ≈ **0.0000056 OKB**.
- 0.05 OKB therefore funds on the order of **8,900 publications**.
- The manifest's `max_fee_wei` ceiling is 2000000000000000 wei (**0.002 OKB**) per
  transaction, roughly 350× the expected cost, so the ceiling refuses a runaway fee long
  before the balance is at risk.

Testnet OKB is faucet-only (0.2/day, browser + captcha). Nothing here needs a refill.

## 4. The one thing that decides the outcome: the 24-hour rule

**A value control observes only a row whose whole normalised record is identical in a
qualifying earlier capture, and "qualifying" means retrieved at least
`CONFIRMATION_INTERVAL_SECONDS = 86_400` earlier** (`touchstone/evidence.py:33`, selected by
`confirmation_capture()` at `evidence.py:124-160`). `run_ustb_epoch` resolves that predecessor
**before** appending this epoch's own capture, so a fetch can never confirm itself
(`touchstone/epoch.py:176-193`).

**Consequence: a canary run against an empty workspace will report `UNVERIFIABLE`.** Not
because anything is broken — because nothing has been observed twice yet, and the system
declines to claim otherwise. That is the honest result and the plan accepts it.

If the owner wants a canary that can report a confirmed state, the workspace must be seeded
by a retrieval run **a full day earlier**. Seeding reads the public issuer endpoint and
publishes nothing on chain. It is a separate, smaller decision and is **not** authorised by
this document.

## 5. The command

```sh
python scripts/run_service.py \
  --manifest deployments/xlayer-testnet-2.json \
  --workspace <workspace path> \
  --asset-key eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e \
  --max-runs 1
```

Environment, on the publishing host only:

| Variable | Required | Note |
|---|---|---|
| `TOUCHSTONE_PUBLISHER_PRIVATE_KEY` | yes | **unprefixed 64-hex.** `keyring.from_hex` is deliberately strict and refuses a `0x` prefix |
| `TOUCHSTONE_SIGNING_SEED` | yes | the reporting seed; **must differ from the publisher key** — `assert_role_separation()` (`keyring.py:204-225`) refuses one secret behind two roles |
| `TOUCHSTONE_BACKUP_KEY`, `TOUCHSTONE_BACKUP_DIR` | optional | absence is visible in the heartbeat rather than fatal |
| `TOUCHSTONE_ALERT_WEBHOOK_URL`, `TOUCHSTONE_ALERT_TOKEN` | optional | the credential never appears in a URL, body, exception, incident, argv or repr |

The deployer key is **not** used here and must not be present on the publishing host.

## 6. What refuses the run before it can do harm

These are existing behaviours, not promises added for this document:

- A manifest whose `deployment_state` is not `active` is refused in `main()` **before any key
  is read**. Prose in a notes field refuses nothing; the machine-readable state does.
- `--fixtures` against a public network is refused: fixture mode is a local rehearsal only.
- `preflight()` runs **in full immediately before signing, never from a cached result** —
  authorisation can be revoked between reading the sequence and signing, and a revocation
  during an incident is exactly when this must not go through. It checks the endpoint's chain
  id, that code exists at the registry address, the runtime bytecode digest, the registry's
  own `expectedChainId`, that the publisher is **not** the registry owner, that the owner is
  the manifest's declared deployer, that the publisher is authorised, and that its recorded
  lineage is the identity the manifest was written for.
- The worst-case fee (gas × `maxFeePerGas`, with margin) is refused above the manifest's
  `max_fee_wei`, and refused again if it exceeds the publisher's balance — both before
  signing.
- A pending journal blocks new work until it is reconciled; the daemon resolves what is
  outstanding before it fetches anything.
- The registry refuses a second report for one epoch with `EpochAlreadyPublished`, and the
  daemon asks the chain first so it does not fetch, evaluate and sign only to be refused.

## 7. Abort criteria

Stop and report rather than retrying:

1. `preflight()` fails for any reason other than transport.
2. Any incident opens with kind `PUBLICATION_UNRESOLVED`.
3. The transaction does not reach 3 confirmations within the daemon's wait.
4. The published report cannot be verified from its own bundle afterwards.

**Never re-run a send to "check" it.** One transaction per publication; verify with a read
call. If a run's outcome is unclear, use `--resolve-only`, which settles what is in flight and
signs nothing new.

## 8. Rollback

There is none in the sense of undoing a publication, and that is by design: the transparency
log and the registry are append-only. What exists instead is correction — a later report that
names the epoch it corrects. A canary that publishes `UNVERIFIABLE` needs no rollback, because
`UNVERIFIABLE` is a true statement about an unseeded workspace.

The registry is not upgradeable. Replacing it again would mean another deployment and another
owner gate.

## 9. After the run

- `deployments/`-recorded registry, the transparency log entry, and the bundle should all
  agree; verify the bundle offline with the committed verifier.
- Record the transaction hash, block, epoch id and resulting state here.
- The dossier (PLAN-T9) is meant to be built from this run's real data rather than fixtures.
