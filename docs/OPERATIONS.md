# Operations

How Touchstone is run between **Aug 21 and Sept 1 2026**, what each alarm means, and what
an operator does about it.

Every address and destination below that is not yet decided is written `not_deployed` or
`not_configured`. Those are not placeholders to be quietly filled in later — they are the
honest state, and a runbook that invented them would be worse than one that admits them.

---

## 1. What runs

| Piece | Module | Entry point | Cadence |
|---|---|---|---|
| Epoch service | `scripts/run_service.py` | daemon | one slot per day per asset |
| Heartbeat | `touchstone/heartbeat.py` | written by the daemon | every 60s |
| Watchdog | `touchstone/watchdog.py` | `scripts/check_watchdog.py` | every 60s |
| Alerts | `touchstone/alerts.py` | `scripts/send_alert.py` | on transition |
| Gas runway | `touchstone/gas.py` | `scripts/check_gas_runway.py` | daily |
| Backup | `touchstone/backup.py` | `scripts/backup_workspace.py` | daily |
| Restore | `touchstone/backup.py` | `scripts/restore_workspace.py` | rehearsal only |

**Host:** `75.119.153.252`, shared, under the disclosed custody deviation. **Supervisor units:** `touchstone-observer@` active since 2026-08-18, `touchstone-status@` on a five-minute timer, and `touchstone-publisher@xlayer-mainnet` **enabled 2026-08-20 under the owner's release of the §3c gate** — its first attempt failed closed on a parse timeout the loaded host could not meet, the timeout was corrected, and the 2026-08-20 and 2026-08-21 slots each published the mainnet asset and both policy reports unattended, all `CONFIRMED`. The slot fires daily around 02:47 UTC.

**Deployment:**

| Network | State |
|---|---|
| X Layer **testnet** (chain 1952) | **ACTIVE** — registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` (block 38489602), deployed 2026-08-17 under a recorded owner approval. Manifest `deployments/xlayer-testnet-2.json`, `deployment_state: active`, publisher authorized. Holds USTB sequences 1–4 (`latestSequence` 4, `CONFIRMED`) plus both policy keys at sequence 1. Registry v2 `0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720` (block 38699818) holds both policies' attestations |
| X Layer testnet — **predecessor** | **SUPERSEDED** — registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` **on chain 1952** (block 38369203) predates the `epochKey` change and cannot enforce one report per epoch. Its manifest declares `deployment_state: superseded` and the service refuses it before reading any key. It published nothing, verified by a full log scan from its deployment block to head. **This address is not unique to it — see the collision note below** |
| X Layer **mainnet** (chain 196) | **ACTIVE** — registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` (block 68291416), deployed 2026-08-18 under a recorded owner approval. Manifest `deployments/xlayer-mainnet.json`, `deployment_state: active`, publisher authorized. Holds USTB sequences 1–5 (`latestSequence` 5, `CONFIRMED`) plus both policy keys at sequence 3. Registry v2 `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` (chain 196, block 68389940) holds both policies through sequence 3; the 2026-08-21 attestations are `0x5750373e…e5c52` and `0x4c8fdd97…39391`. `AssetGateV2` `0x8641CF6d40524AC55aBd0a02601AfBd374EFB059` and `RWAAdmissionController` `0x5C5265392701A99cbB137aF8116E0F97f630329A` consume them. Two independent RPCs returned `allowed` from the gate after sequence 3 was confirmed. Canonical mainnet history lives on the VPS at `/var/lib/touchstone/xlayer-mainnet/ustb` since the 2026-08-19 workspace migration. **The key separation recorded in `docs/DEPLOYMENT-G1-EXECUTED.md` §"Deviation" is still not real** — the laptop holds both deployer and publisher keys, and the publisher's key now also lives on the shared production host (root-owned `0600`, read by systemd before privileges drop, never readable by the service account). Disclosed rather than resolved |

> **Address collision — read before pointing any tool at `0xc9d58e…D30d`.** The same deployer at
> nonce 0 produced the same contract address on both chains, so this one address is the
> **superseded** testnet predecessor on chain 1952 *and* the **live** mainnet registry on chain
> 196. An address alone does not identify a deployment here; the chain id is the only on-chain
> discriminator. A tool that resolves this address without pinning the chain will read a dead
> registry and a production one as if they were the same thing.

Testnet publisher `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710`, authorized on the active
registry with its identity mapped to itself. The live manifest is
`deployments/xlayer-testnet-2.json`, written by the deploy script and accompanied by
`xlayer-testnet-2.json.attempt.json` — the append-only journal, which is the only local record
of the deployment and authorization transaction hashes. **Keep the journal.** The superseded
registry's manifest `deployments/xlayer-testnet.json` is retained; its note distinguishes the
fields read from the chain from the ones that are reconstructed configuration.

**The canary ran on 2026-08-17 at 16:49 UTC, under its own owner authorisation, and it is the
first report Touchstone has ever published.** The table below is that first era — the five
v1 asset reports through 2026-08-18, kept because its footnote records a correction lesson.
It is **not** the current publication state: as of 2026-08-21 there are 17 published reports
across both chains, 12 of them `CONFIRMED`, spanning the asset key and both policy keys with
eight Registry v2 attestations. The per-report table with every transaction is the dossier at
https://touchstone.gudman.xyz/dossier, generated from `site2/_data/facts.json`, which is the
canonical record:

| Chain | Seq | Epoch | State | Event | Block | Transaction |
|---|---|---|---|---|---|---|
| 1952 | 1 | `ustb-2026-08-17` | `UNVERIFIABLE` | `RECONFIRMED` † | 38526525 | `0x5107140c…` |
| 1952 | 2 | `ustb-2026-08-18` | `UNVERIFIABLE` | `RECONFIRMED` | 38611710 | `0x6a2832db…` |
| 1952 | 3 | `ustb-2026-08-17` | `UNVERIFIABLE` | `CORRECTION_PUBLISHED` | 38617112 | `0x1cdf45d0…` |
| 196 | 1 | `ustb-2026-08-18` | `UNVERIFIABLE` | `RECONFIRMED` † | 68292878 | `0xfa4b7992…` |
| 196 | 2 | `ustb-2026-08-18` | `UNVERIFIABLE` | `CORRECTION_PUBLISHED` | 68307118 | `0x363539ad…` |

† **Wrong in the signed bytes, and corrected rather than edited.** A first publication has
nothing to reconfirm, so `RECONFIRMED` on sequence 1 asserted a history that did not exist; the
same reports also carried a limitation reading "This local-only report" while sitting on a public
chain. Signed bytes cannot be edited, so each was restated through `publishCorrection`. Sequence
2 on chain 1952 is a genuine reconfirmation and was left alone.

Each correction reproduces its original's `control_set_root`, `evidence_root` and
`approval_ledger_sha256` exactly — `scripts/build_correction.py` refuses to sign otherwise — and
`epochSequence` still points at each epoch's *first* publication, so a correction restates an
epoch without opening one. Verified from chain after publication: `latestSequence` 3 and 2, both
`Corrected` events naming sequence 1, both transparency logs re-verifying end to end.

Every asset key other than USTB's and its two policy keys is still zero on both chains.

### Measured publication window

`docs/OPERATIONS-METRICS-2026-08-19.json` was derived from each transparency log independently
for 2026-08-17 through 2026-08-18. Testnet records 2 scheduled, 2 completed, 0 missed and
1 corrected publication. Mainnet records 2 scheduled calendar slots, 1 completed, 0 missed,
1 corrected publication and 1 unaccounted slot because the mainnet workspace began on Aug 18.
The histories were not concatenated. This is a publication-history measurement, not proof of
continuous unattended operation. Unattended operation itself began 2026-08-20, when the
enabled publisher unit ran the mainnet slots on its own on both 2026-08-20 and 2026-08-21.
Two days prove the path and continuity across one interval, not a long-term reliability record;
the measured window predates them.

The reproducible command is:

```text
python scripts/build_operations_metrics.py --workspace <testnet-workspace> --workspace <mainnet-workspace> --start 2026-08-17 --through 2026-08-18 --out docs/OPERATIONS-METRICS-YYYY-MM-DD.json
```

**The state is `UNVERIFIABLE` throughout, and that is the correct answer rather than a failure.**
A value control observes only a row confirmed by a capture at least 24 hours older. On a first
run there is no such capture. On chain 1952 sequence 2 there was one, but the run went out at
16:28:07 UTC against a 16:48:32 UTC capture — **twenty minutes inside the 24-hour interval,
because the operator read a local-time clock as UTC** — so `ustb-nav-per-share-present` was
`UNEVALUABLE` and the state held. The confirmation window is a real gate, and it refused a run
that was short by twenty minutes. The next attempt must be at or after 16:48:32 UTC against the
same workspace, because that is where the earlier capture lives.

The superseded predecessor is refused before any key is read, because its manifest declares
`deployment_state: superseded`. That refusal is what stops a publication reaching it; its
recorded runtime digest matches its own deployed code, so a preflight bytecode comparison
would *agree* with it rather than reject it.

---

## 1a. 2026-08-19: first CONFIRMED states, v2 registries, and the permit/refuse pair

Published and verified from chain:

| Chain | What | Where |
|---|---|---|
| 1952 | asset seq 4 **CONFIRMED**; policies `disclosure-freshness:1`, `nav-settlement:1` seq 1 **CONFIRMED** | v1 registry `0x0dAb4A5B…352C` |
| 196 | asset seq 3 **CONFIRMED**; both policies seq 1 **CONFIRMED** | v1 registry `0xc9d58e…D30d` |
| 1952 | RegistryV2 (block 38699818), freshness-pinned AssetGate `0x0bc5c0cc…8eE1`, GuardedAction pair — permitted `0x5b6e65b9…` status 1, refused `0xfc9bcc47…` status 0 | |
| 196 | RegistryV2 (block 68389940), freshness-pinned AssetGate (block 68389983), GuardedAction pair — permitted `0x8b4b6c85…` status 1, refused `0x2b106907…` status 0 | |

The confirmed NAV row is `11.18208300` — the value the system refused on the 18th, now
settled unchanged across two captures. The refusal and the confirmation are one mechanism.

A relayer identity was minted (`0x5b4e381C…faFCe`) because the v2 deploy script requires it
distinct from owner, publisher and operations; its key sits with the others in the gitignored
env file and it has never held funds.

🚨 **The address collision hazard now has four instances.** The deployer's nonce sequence
replays across chains, so: `0x0dAb4A5B…352C` is the **v1 registry on 1952** and the
**RegistryV2 on 196**; `0xAac48DC2…4a83` is the **v1 asset gate on 1952** and the
**freshness-pinned gate on 196**; `0xBaE680e6…7720` is the **RegistryV2 on 1952** and a
**GuardedAction on 196**; and `0xc9d58e…D30d` remains superseded-on-1952 / v1-live-on-196.
An address means nothing here without a chain id. Ever.

## 1c. 2026-08-19: workspace split resolved

The VPS publishing workspace is now the canonical mainnet history. Executed with backups on
both sides first (`pre-migration-20260819T172940Z-*` local and on the host):

1. observer stopped; its accumulated tree preserved whole at
   `/var/lib/touchstone/xlayer-mainnet/ustb-observer-history` — two independent evidence hash
   chains are never concatenated;
2. the local published-history workspace copied to `/var/lib/touchstone/xlayer-mainnet/ustb`,
   ownership restored to the split-identity layout (root `2750 touchstone`, evidence
   `2770 touchstone-observer`);
3. observer restarted against the canonical store — capturing fresh evidence into the same
   index the publisher would confirm against, which is the point of sharing it.

If the publisher unit is ever enabled on the host, it now publishes from the history that
backs the public reports. Known, documented gap: the canonical log lacks mainnet sequence 3
and the two policy records, which live interleaved in the local testnet workspace log per
§1b; the chain is authoritative and unaffected.

## 1b. Incident 2026-08-19: mainnet publications journaled into the testnet workspace

The first CONFIRMED states published to both chains on 2026-08-19 — testnet asset sequence 4
plus two policy sequence 1s (16:36 UTC), then mainnet asset sequence 3 plus two policy
sequence 1s (16:46 UTC). Every report is genuine, evaluates from qualifying evidence, and its
bundle verifies offline. The chains are correct.

The local records are not where they belong. The mainnet run was launched by a runner edited
with an unverified stream edit that silently failed to change the workspace root, so it ran
with the mainnet manifest against the **testnet** workspace — twice, because the corrected
rewrite also failed silently and the old file ran again. Consequences, stated precisely:

- the testnet workspace's transparency logs now interleave chain-1952 and chain-196 entries
  (each log's own hash chain remains intact; the chain id of each entry is determined by its
  transaction, which the paired registry resolves unambiguously);
- the mainnet workspace's local log is missing mainnet sequence 3 and both policy records —
  the chain is authoritative and unaffected;
- the 16:36 testnet policy bundle *files* were overwritten by the same-named 16:46 mainnet
  ones; their full signed reports remain embedded in the transparency logs.

The logs are append-only and stay as they are: rewriting a transparency log to tidy history
is exactly what this project must never do. The workspace reorganisation planned as the
"workspace split" resolution now also covers this interleaving. Two working rules follow:
**never launch a publishing runner whose configuration was edited without asserting every
substitution applied**, and **a runner must print its workspace root and manifest before
waiting, so a mismatch is visible while there is still time to kill it**.

## 2. Daily schedule

Per the roadmap's operations calendar, and reproduced here so an operator needs one file:

| Date | System activity |
|---|---|
| Fri Aug 21 | **Testnet** RC target: first live testnet epoch and initial dossier roots. **No mainnet action** — see the owner gates below |
| Sat Aug 22 | Weekend re-observation — **no business-day NAV is promised** |
| Sun Aug 23 | Reconfirmation, or an honest source-health incident |
| Mon Aug 24 | First post-weekend publication windows |
| Tue Aug 25 | Daily surveillance and state renewal |
| Wed Aug 26 | Daily surveillance |
| Thu Aug 27 | Daily surveillance |
| Fri Aug 28 | Weekly reliability summary |
| Sat–Sun Aug 29–30 | Weekend reconfirmations; snapshot-readiness check |
| Mon Aug 31 | Business-day and month-end monitoring |
| Tue Sep 1 | Fresh snapshot-day epoch; archived verification bundle |

**A weekend slot is a real slot.** It reconfirms rather than observing a new NAV, and it
costs gas. Nothing about a weekend excuses a missing epoch: it is either completed or
recorded as an incident.

---

## 3. Thresholds

| Setting | Value | Why this value |
|---|---|---|
| Heartbeat refresh | 60s | |
| Heartbeat expiry | 180s | Three refreshes. One missed write is a slow disk, not a death |
| Watchdog check | 60s | Worst-case detection under four minutes, inside the five-minute requirement |
| Restart deadline | 15 min | After which `RESTART_FAILED` |
| Confirmations (testnet) | 3 | |
| Confirmations (mainnet) | 12 | |
| Gas gate | funded through **Sept 3** | Two days past the window, so a shortfall is visible before it bites |

**Health is never stored.** The heartbeat records facts and an expiry; the verdict is
computed at read time against the reader's clock. A dead daemon cannot leave a green flag
behind, because nothing remains running to write one.

**Liveness and epoch health are separate.** A daemon writing perfect heartbeats while its
source has been unreachable for two days is *alive and unhealthy*. The watchdog reports both
and exits nonzero for either.

---

## 4. Alerts

One HTTPS webhook. The credential is read from `TOUCHSTONE_ALERT_TOKEN`, sent only in an
`Authorization` header, and never appears in a URL, a body, an exception, a fingerprint, a
repr, or any durable record.

| Code | Severity | Meaning | First action |
|---|---|---|---|
| `HEARTBEAT_STALE` | CRITICAL | No heartbeat inside its expiry | Check the process; restart |
| `RESTART_FAILED` | CRITICAL | Replacement did not become healthy in 15 min | Manual intervention |
| `EPOCH_MISSED` | CRITICAL | A scheduled slot passed with no epoch and no incident | Open an incident; never backfill |
| `PUBLICATION_UNRESOLVED` | CRITICAL | Publication state cannot be established safely | **Do not restart blindly** — see §6 |
| `VERIFICATION_FAILED` | CRITICAL | A log, chain or signature does not verify | Stop publishing; investigate before any write |
| `PUBLISHER_STATE_UNEXPECTED` | CRITICAL | Wrong publisher lineage, or a retired key signed | Stop; treat as possible compromise |
| `GAS_RUNWAY_SHORT` | WARNING | Funding does not reach Sept 3, **or is UNKNOWN** | Top up manually |
| `BACKUP_MISSING` | WARNING | No archive, or the newest is over 24h old | Take one; check the destination |
| `RESTORE_REHEARSAL_FAILED` | WARNING | A rehearsal did not verify | Investigate before trusting any archive |
| `RECOVERED` | INFO | A previously unhealthy condition cleared | None |

Alerts carry codes, an asset, timestamps and hashes — **never** exception text and never
source URLs. A delivery failure goes to the supervisor journal and never becomes another
webhook call, which would loop precisely when the endpoint is what broke.

**Not promised:** guaranteed delivery, retry-until-success, paging escalation, failover.

---

## 5. Gas

Runway is `balance ÷ largest measured cost`, where the cost is `gas_used ×
effective_gas_price` from publications that actually succeeded. No fee oracle, no estimate,
no configured ceiling standing in for a measurement.

If any operand is missing the answer is **`UNKNOWN`**, and unknown **fails the gate**.
Treating "we could not tell" as "we are fine" is the same error in a different costume.

**Top-up is manual.** There is no automatic funding, no treasury logic, no price conversion.

Testnet publisher: `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710`, funded with 0.05 OKB on
2026-08-15 from the deployer. Testnet OKB comes only from the faucet at
`web3.okx.com/xlayer/faucet` (0.2 OKB per day) and cannot be bought or bridged. A registry
deploy measured 1,284,548 gas at 20,000,001 wei — about 0.0000257 OKB — so one faucet claim
covers the operating window many times over.

Mainnet publisher: `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` — the same identity as testnet, authorized on the mainnet registry, holding roughly 0.005 OKB. Mainnet OKB is bought, not faucet-claimed, so the runway there is finite in a way testnet's is not.

---

## 6. Incidents

An incident is **opened by appending and closed by appending**. Nothing is ever edited or
deleted — the value of the record is precisely that it cannot be tidied afterwards.

| Kind | When |
|---|---|
| `SOURCE_UNAVAILABLE` | The source would not answer, or answered unusably |
| `EPOCH_FAILED` | The epoch ran and could not produce a report |
| `SLOT_MISSED` | A scheduled slot passed unrun |
| `PUBLICATION_UNRESOLVED` | Publication state cannot be established safely |
| `SCHEDULE_UNUSABLE` | The schedule can no longer name its next slot |

**A source outage is not asset inconsistency.** If the feed is down, the asset's state ages
toward `STALE` on its own and an incident records the outage. It is never rendered as an
observation, and never as a finding about the issuer.

**Never backfill.** An epoch is a statement about a particular day's evidence. Running
yesterday's slot today retrieves *today's* evidence and files it under yesterday. The slot
is recorded missed and skipped.

**On `PUBLICATION_UNRESOLVED`:** do not restart repeatedly and do not clear the journal by
hand. Startup reconciliation settles in-flight publications; deleting the journal is how one
report becomes two on-chain.

---

## 7. Backups

Daily, encrypted, AES-256-GCM. Key in `TOUCHSTONE_BACKUP_KEY`, which must not equal the
reporting seed or the publisher key.

**A second process must never copy a live workspace.** The daemon holds the workspace lock
for its whole serving lifetime, so the standalone command takes that same lock or refuses.
An archive assembled from files read at three different moments restores into a state the
service was never in.

Contains: both logs, the incident head, operations state and any pending operation, the
evidence index and every referenced object. Excludes: the lock, the heartbeat, temporary
files and every secret.

Retain every archive through **at least Sept 3**. No pruning.

**Restore is a rehearsal, not a recovery button.** It verifies into a fresh directory and
stops; moving it into place is a separate operator decision. Rehearse at least once before
Aug 21 and confirm the restored chains verify.

Destination: `not_configured`.

---

## 8. Daily operator checklist

1. `scripts/check_watchdog.py` — exit 0.
2. `scripts/check_gas_runway.py` — covers Sept 3, and is not `UNKNOWN`.
3. Yesterday's epoch either published or has an incident. No silent gaps.
4. A backup exists from the last 24 hours.
5. Incident log verifies; every open incident is one someone is actually working.
6. Weekly (Fri): reliability summary — scheduled vs completed, incidents, corrections.

---

## 9. Owner gates

Completed owner-gated actions and the decisions still open are recorded together here. A
checked deployment means that exact deployment was executed; it grants no approval to publish
an epoch, deploy on another network, or make a private repository public.

- [x] **Testnet deploy (original)** — approved and executed 2026-08-15. Registry
      `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, block 38369203. **Now superseded**: it
      predates the `epochKey` change, is marked `deployment_state: superseded`, and is
      refused by the publishing boundary. It published nothing.
- [x] **Testnet deploy (replacement)** — digest-bound approval granted and executed
      2026-08-17. Registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, block
      38489602; publisher authorized with identity mapped to itself; actual spend
      `28981261449063` wei. `docs/DEPLOYMENT-G1.md` remains the unchanged, pre-execution
      approval packet, so its `AWAITING DIGEST-BOUND APPROVAL` banner is historical rather
      than current status. `docs/DEPLOYMENT-G1-EXECUTED.md` is the execution record
- [x] **Live USTB testnet epoch** — done 2026-08-17 under its own owner authorisation, and
      repeatedly since. Testnet holds four asset reports and both policy keys
- [x] **Mainnet deploy + canary** — done 2026-08-18 under a recorded owner approval. Mainnet
      now holds five asset reports and both policy keys at sequence 3. Note this was authorized
      while the key separation this document requires still did not exist; see the
      deployment table above
- [ ] **Continuous operation** — the publisher unit ran the 2026-08-20 and 2026-08-21
      mainnet slots unattended. That is a two-day operating record, not yet the sustained
      measured window required to close this item
- [ ] **Submission** — owner-handled
- [ ] **Domains and handles** — deliberately last; see `docs/BRAND-CLEARANCE.md`
- [x] **Git remote** — `github.com/Ridwannurudeen/touchstone`, **public since 2026-08-16**
      by the owner's decision (GitHub's PublicEvent, 2026-08-16T14:10:51Z, no later
      visibility change on record), `main` protected by the CI aggregate check
- [ ] **Any public post**

---

## 9a. Rolling the reporting key

The daemon reads its deployment manifest and its signing key **once, at startup**. There is
no hot reload, so a rollover is a stop, an edit, and a start — in that order, and never
overlapping:

1. **Stop the daemon and confirm it is stopped.** Not "send the signal": confirm. A slot that
   begins between the two edits below signs with one key against a manifest that expects the
   other.
2. **Roll the manifest** with `rolled_over(...)`, which supersedes the active reporting key
   and keeps the outgoing one listed. Rollover is additive: every bundle the retired key
   signed stays verifiable, and only the selection for *future* reports changes.
3. **Install the new signing key** on the host.
4. **Start the daemon** and confirm the first heartbeat.

**Both edits or neither**, and what a mismatch actually does depends on which half is wrong:

- **Restarted with a signing key the manifest does not name:** the daemon **refuses to
  start.** `scripts/run_service.py` derives the kid from the seed, compares it with the
  manifest's active reporting key, prints `SERVICE FAIL: the signing seed derives … but the
  manifest's active reporting key is …` and exits 1 **before serving anything**. No daemon is
  left running, no incident is opened, and no slot is attempted. It is loud, and it is the
  outcome to want.
- **A running daemon whose key file changes underneath it:** nothing happens. The signer is
  read once at startup and there is no hot reload, so it keeps signing with the key it
  already holds. This is why step 1 is stop-and-confirm rather than edit-in-place.

An earlier version of this section claimed the mismatch left the daemon running and opened a
`PUBLICATION_UNRESOLVED` incident. That is **not** what the configured daemon does. The
rollover regression test does not exercise a mismatch either: it rolls the manifest to the
succeeding public key before constructing the next day's service, publishes successfully,
and proves that the manifest's retained key records verify both days.

`PUBLICATION_UNRESOLVED` means the service cannot safely establish publication state. It is
recorded when durable startup state is unreadable or cannot be reconciled, when a prior
operation or journal remains unresolved, when the registry cannot say whether an epoch is
already published before `produce()` runs, and when an already-signed report reaches
publication but publication cannot be completed. The incident does not by itself prove that
a report was signed or a transaction was journalled; its detail identifies which boundary
failed.

**Never discard a retired key's public record.** It is what verifies everything it signed.

---

## 10. What this document does not claim

There is no HSM, no KMS, no multisig, no threshold signing. Keys are environment variables
on the publishing host, and `docs/KEY-MANAGEMENT.md` says what that does and does not
protect. Key rotation is manual.

There is no multi-region failover, no leader election, and no second publisher. A single
host runs a single daemon, and the honest availability claim is bounded by that.

The workspace lock stops a **second process** from copying a live workspace, and the `Held`
capability stops trusted in-process code from taking a backup without holding it. Neither is
a security boundary against code able to alter the locking module's own state, or to close
its descriptor and reuse the number. One such bypass is known and documented in
`touchstone/locking.py` rather than papered over: closing the descriptor releases the kernel
lock while its integer remains registered, and this platform reuses that integer on the next
open. Closing it properly needs a separate lock-owning process and IPC, which is out of
proportion to what it protects. Reviewed and accepted 2026-08-16.

Detection and recovery timings are proven in a local subprocess harness that kills a real
daemon. They have **not** been re-proven on the production host — restart and recovery
there is still an open demonstration.

Epochs have now been produced and published without a person present: the enabled
publisher unit ran the 2026-08-20 and 2026-08-21 mainnet slots on its own, after one earlier
attempt failed closed. That converts the reliability figures from pure test properties into
claims with two production data points. They earn the word "measured" only after a sustained
unattended window exists.
