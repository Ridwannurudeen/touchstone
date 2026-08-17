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

**Host:** `not_configured`. **Supervisor units:** `not_configured` — packaging is PLAN-T13.

**Deployment:**

| Network | State |
|---|---|
| X Layer **testnet** (chain 1952) | **ACTIVE** — registry `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` (block 38489602), deployed 2026-08-17 under a recorded owner approval. Manifest `deployments/xlayer-testnet-2.json`, `deployment_state: active`, publisher authorized. **Holds zero reports** |
| X Layer testnet — **predecessor** | **SUPERSEDED** — registry `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` (block 38369203) predates the `epochKey` change and cannot enforce one report per epoch. Its manifest declares `deployment_state: superseded` and the service refuses it before reading any key. It published nothing, verified by a full log scan from its deployment block to head |
| X Layer **mainnet** (chain 196) | `not_deployed` — owner-gated, and additionally blocked until the deployer and publisher keys sit on separate hosts (`docs/DEPLOYMENT-G1-EXECUTED.md`) |

Testnet publisher `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710`, authorized on the active
registry with its identity mapped to itself. The live manifest is
`deployments/xlayer-testnet-2.json`, written by the deploy script and accompanied by
`xlayer-testnet-2.json.attempt.json` — the append-only journal, which is the only local record
of the deployment and authorization transaction hashes. **Keep the journal.** The superseded
registry's manifest `deployments/xlayer-testnet.json` is retained; its note distinguishes the
fields read from the chain from the ones that are reconstructed configuration.

**No report has ever been published to any registry.** The active registry holds zero reports;
`latestSequence` is zero for every asset key. The next publication is a single USTB canary
epoch, which the owner approved separately from the deployment — the deployment approval
explicitly did not cover it.

The superseded predecessor is refused before any key is read, because its manifest declares
`deployment_state: superseded`. That refusal is what stops a publication reaching it; its
recorded runtime digest matches its own deployed code, so a preflight bytecode comparison
would *agree* with it rather than reject it.

---

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

Mainnet publisher: `not_deployed`.

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

Everything here needs explicit approval. One has been given and executed; the rest have not.

- [x] **Testnet deploy (original)** — approved and executed 2026-08-15. Registry
      `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, block 38369203. **Now superseded**: it
      predates the `epochKey` change, is marked `deployment_state: superseded`, and is
      refused by the publishing boundary. It published nothing.
- [ ] **Testnet deploy (replacement)** — see `docs/DEPLOYMENT-G1.md`, which is the
      authorization packet and is itself marked DRAFT. The design is approved **in
      principle**;
      **execution authorization has not been granted.** No replacement registry exists, no
      gas has been spent on one, and `docs/DEPLOYMENT-G1.md` must be complete and approved
      before it is requested. Tracked separately from the entry above because they are two
      different deployments and conflating them is how "approved" spreads to something
      nobody approved
- [ ] **Mainnet deploy + canary** — after a proven testnet loop only, and nothing has
      published to testnet yet
- [ ] **Submission** — owner-handled
- [ ] **Domains and handles** — deliberately last; see `docs/BRAND-CLEARANCE.md`
- [ ] **Public git remote**
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
daemon. They have **not** been proven on production hardware, because there is no production
host yet.

A live testnet registry is not a running system. Nothing has published to it, no epoch has
been produced unattended, and the schedule in section 2 describes what this service is built
to do rather than what it has done. Until an epoch is produced and published without a
person present, every reliability figure here is a property of the tests.
