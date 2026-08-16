# G1 — Replacement testnet registry deployment

> # AWAITING DIGEST-BOUND APPROVAL — NOT EXECUTION AUTHORIZATION
>
> The owner granted a general downstream approval on 2026-08-15 — *"you have my approval for
> the everything downstream work"* — and delegated the spend ceiling: *"you and codex should
> pick the best option"*. §5 was set under that delegation.
>
> **That general approval does not authorize execution, and this document previously claimed
> it did.** An earlier revision of this banner recorded the digest rule as waived. That was
> not a disclosure, it was a self-grant: the rule exists to stop a packet drifting after
> approval while the executing agent holds the pen, so the agent is the one party who cannot
> waive it. Writing down that a control was bypassed does not discharge the control.
>
> That revision also understated the exposure it used to justify itself, calling 0.0000361 OKB
> the worst case. That is the estimate at the gas price observed on 2026-08-16. The number the
> ceiling actually permits is `999999999295360` wei — **~0.001 OKB, 27.67× higher.** The
> honest figure is the enforced maximum, not the expected spend.
>
> Execution requires one written owner approval naming **all three**:
>
> 1. the **full packet commit SHA**;
> 2. the **sha256 of this packet's raw Git blob** — supplied alongside this document and
>    reproducible with the `git show` command below, deliberately not printed inside it, since
>    a document cannot state its own hash;
> 3. the ceiling **`1000000000000000` wei**.
>
> The commit is not redundant with the blob. **A blob digest does not identify a unique
> commit** — a later commit can carry this document byte-for-byte while `tests/`,
> `scripts/`, `pyproject.toml` or the manifest schema have moved, and the approval would still
> appear to match. Naming both binds the approval to a tree, not just to wording.
>
> Nothing else unlocks execution.
>
> **Approval binds to a digest, not to this path.** `docs/DEPLOYMENT-G1.md` is mutable;
> approving "the G1 packet" would approve whatever it later says.
>
> **The approved artifact is the raw Git blob**, not the file in a working tree. This
> repository runs with `core.autocrlf=true`, so a checked-out copy has CRLF line endings and
> hashes differently from the blob — the same document, two digests, which is precisely the
> ambiguity a digest is supposed to remove. Print the approved digest with:
>
> ```
> git show <approved-packet-commit>:docs/DEPLOYMENT-G1.md | sha256sum
> ```
>
> and export a byte-identical copy for reading with:
>
> ```
> git cat-file blob <approved-packet-commit>:docs/DEPLOYMENT-G1.md > DEPLOYMENT-G1.approved.md
> ```
>
> **`<approved-packet-commit>` is not the release commit.** They are different commits and
> hash to different values: the release commit names the executable implementation, the packet
> commit names this document. Hashing the packet at the **release** commit produces the wrong
> digest — an earlier version of this section made that mistake.
>
> The packet commit **is** the commit execution runs from; §7 requires it. An earlier version
> of this banner said hashing "at the commit being executed from" gives the wrong digest. That
> was true when execution ran from the release commit and became false the moment §7 pinned
> `HEAD` to the packet commit — the two statements sat in the same document contradicting each
> other.
>
> If the digest does not match at execution time, **stop** — the packet has changed since it
> was approved and must be re-read and re-approved.

## 1. Why there is a second deployment at all

A registry was deployed to X Layer testnet on 2026-08-15 at
`0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, block 38369203. It never published anything.

It is unusable because of a defect found afterwards and reproduced: a daemon restarted on a
day it had already served derives the same epoch, reads the correct next sequence, and the
registry accepts a **second signed report about the same day**. Two valid reports, and a
consumer reading the latest one sees whichever landed last.

Both cheaper remedies were rejected on review. Durable local state is a projection rather
than chain truth and is lost with the workspace. Inferring the epoch from the latest report's
URI fails because a correction can become the latest report and hide the earlier duplicate.
So the registry itself enforces it — `bytes32 epochKey` on `Report`,
`epochSequence[assetKey][epochKey]`, and `EpochAlreadyPublished` on a second publish. That is
an ABI change, and the deployed contract cannot be upgraded to it.

The old deployment is marked `deployment_state: superseded` in its manifest and is refused by
`SignedRegistryBackend` before any key is read. **It is not being replaced because it failed;
it is being replaced because it cannot enforce something it was never built to.**

## 2. Exactly what would be deployed

| | |
|---|---|
| Release commit | `bcbd8b40828935888191b16f09f3c5d383e83108` — see §2.1 |
| Contract | `contracts/contracts/TouchstoneRegistry.sol` |
| Solidity | `0.8.24`, optimizer **enabled**, `runs: 200`, evm target `paris` |
| Creation bytecode | 6,303 bytes, sha256 `e1702c40bafef7a36ede227a32b19cf1904a78cd6cd70d00068a3643c4fa6926` |
| Runtime **template** | 6,147 bytes, sha256 `9b7019b0b2e3ad4242ac99adc2c0542513425c13336341505aca9674ba23bca7` |
| Runtime **as deployed** | sha256 `cecada9e4caefaa153ea321d5831b053ad8750ffe58a4ac0ee61b81ba4dbc561` |
| Constructor argument | `expectedChainId = 1952` |
| npm lockfile | `contracts/package-lock.json`, raw-blob sha256 `3c506269d6e7a73580760c9ab759fad032aa7ed82045a2bc93faa3d9295e2ff8` |
| Python project | `pyproject.toml`, raw-blob sha256 `9f5346f8f1ed53d7c2090a1ab631d9f35a6896b17c8bf39b3e4a396ba019a009` |

**The digests in this table have three different bases, and an operator checking them needs to
know which is which.**

- `package-lock.json` and `pyproject.toml` are **raw Git blobs at the release commit**,
  obtained with `git show <commit>:<path> | sha256sum` — the same basis as the packet's own
  approval digest. A working-tree copy of a text file hashes differently under
  `core.autocrlf`, so the blob is the only stable basis.
- **Creation** and **runtime template** are hashes of raw compiler output — the bytes decoded
  from the artifact's hex. They cannot be Git blobs: `contracts/artifacts/` is gitignored, so
  no artifact is tracked at any commit. Reproduce them by compiling, not by `git show`.
- **Runtime as deployed** is a *third* basis. It does not exist in any artifact and compiling
  will not produce it. It is the template with the immutables spliced in, per the recipe below
  — hashing the artifact's `deployedBytecode` gives `9b7019b0…`, not `cecada9e…`.

An earlier version of this table claimed a single raw-blob basis for all five. A later one
claimed two, which still told an operator to reproduce `cecada9e…` by compiling — which cannot
work.

**The two runtime digests are different things, and confusing them would abort this
deployment after the money was spent.** `owner` and `expectedChainId` are Solidity
immutables: the compiler emits a runtime with zeroed placeholders, and the constructor
splices the real values in. So the artifact's `deployedBytecode` hash — the template — is the
hash of no deployed contract anywhere.

`cecada9e…` is the digest the deployed code will actually have, computed by splicing the
owner in §4 and chain id 1952 into the template at the offsets the compiler records
(`immutableReferences` ids 467 and 469). The method was checked against a real local
deployment: splicing that chain's owner and id reproduces its on-chain `eth_getCode` hash
exactly.

**Any change to the deployer address changes this digest.** If the owner deploys from a
different address, `cecada9e…` is wrong and this packet must be reissued.

The superseded manifest records `7b0b36531a3d9234fb7d72a231b5582a8516f4d99c757fe4298be57d57dd6e2a`,
which is an *instantiated* digest of the old contract. It cannot be usefully compared with a
template digest — an earlier version of this document did exactly that and drew a conclusion
the comparison did not support. The old registry is superseded because its source genuinely
lacks `epochSequence`, which is verifiable by reading it, not by comparing those two numbers.

### 2.1 The release commit, and why it is not this one

The release commit names the **executable implementation** that would be deployed. It is
deliberately not the commit containing this document: a packet cannot embed its own hash, and
the two identify different things — the release commit identifies audited code, the packet
digest identifies the exact approved wording.

It moved from `15f6127` to `bcbd8b4` because the manifest test collected the deployment
journal as a manifest and failed. That defect does not need a successful deployment to fire —
`reserveDestination` creates the journal at `deploy.js:457–458`, before the spend checks and
before any send, so **any attempt that gets as far as reserving its destination leaves the file that
breaks the test**. It had to be fixed inside the release rather than after it. **The deployed bytecode
did not change.** `git diff 15f6127..bcbd8b4` touches only `tests/`, `contracts/test/` and this
document; the three digests above and both raw-blob digests are identical at either commit.

An earlier version of this packet named `e22cc7f`. **That commit is broken**: its deploy
script reads `destination` out of scope in `main()`, so the command deploys, authorizes,
prints the manifest and then exits 1 — losing the manifest write. Its spend ceiling also only
compared receipts after the fact. Following that packet would have deliberately selected the
superseded implementation. It is recorded here rather than quietly corrected, because the
failure mode was a document pointing at code it had outlived.

**No AssetGate is deployed by this gate.** The consumer contract must be pinned to a control
root, and the control set is expected to change when the NAV controls are recompiled to carry
`minimum_row_age_business_days`. Deploying a gate now would pin a root we intend to retire.

## 3. Network

| | |
|---|---|
| Network name | `xlayer-testnet` |
| Expected chain id | **1952** |
| Endpoint | `https://testrpc.xlayer.tech/terigon` |
| Deprecated chain | **195 must never be used** |

The chain id is bound to the network name in both `touchstone/deployment.py` and
`contracts/scripts/deploy.js`, so a manifest naming `xlayer-testnet` on any other chain is
refused. The deploy script additionally requires `TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=1952` —
a positive confirmation naming the chain, because a boolean flag can be satisfied by a stale
shell export.

Verified live on 2026-08-16: `eth_chainId` returns 1952, `eth_syncing` is `false`, and the head
advanced 38457129 → 38457133 across four seconds. The operator must **re-confirm all three
immediately before deploying** — an endpoint that answered an hour ago is not a live endpoint —
and abort on any mismatch.

## 4. Identities

All four are distinct, and the three EVM roles are enforced as distinct by the deploy script
before it sends anything.

| Role | Address | Held where |
|---|---|---|
| Deployer / registry owner | `0xAa1C01C6FcDcc268DbF93C861D26C44a27C35436` | Owner only. **Never on the publishing host** |
| Publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710` | Publishing host |
| Operations | `0xF901f538d17ED060018F043bCAA1d7f0977Fa65D` | Custody only; sends nothing here |
| Reporting key (Ed25519) | `ed25519:394ee022b83de4783cd49f60cc4842b48d108f6a339b73272739490cb9a581fd` | Reporting host |

Public key material only. **No private key or seed appears in this document, and none may be
added to it.** See `docs/KEY-MANAGEMENT.md` for where each is held and why the deployer must
not reach the publishing host.

## 5. Spending

Two separate ceilings, because they bound different things and conflating them is how an
approval to deploy became an approval to spend an unstated amount.

| Ceiling | Variable | Bounds |
|---|---|---|
| Deployment total | `TOUCHSTONE_DEPLOY_MAX_SPEND_WEI` | The deployment **and** authorization transactions of this one command. Required off the local chain |
| Per publication | `TOUCHSTONE_MAX_FEE_WEI` | One publication's worst-case fee, recorded in the manifest as `max_fee_wei`. Currently `2000000000000000` |

### How the ceiling is enforced

The ceiling is a real bound, not a report. From it the script derives an explicit
`gasLimit` and `maxFeePerGas` for both transactions and proves the **worst case** — every
unit of gas at the maximum fee — sits under the approved number *before the first send*. It
also refuses to start if the network's current `maxFeePerGas` already exceeds what the
ceiling allows, and if the deployer's balance is below it. The receipts are compared
afterwards as well, which would catch a provider that ignored the caps.

An earlier version of this document checked receipts only. That is monitoring, not
enforcement: it cannot un-send a transaction, and an owner who approved a number has not
approved "that number, probably".

### The recommended ceiling

Measured on a local chain at this release commit:

| | Gas |
|---|---|
| Registry deployment | 1,380,884 |
| With 20% headroom (what the script sets as `gasLimit`) | 1,657,060 |
| `authorizePublisher` | 68,191 actual; 150,000 allowed |
| **Worst-case total** | **1,807,060** |

An earlier version of this document recommended `36141200000000000` wei on an assumed
20 gwei. That assumption was wrong by three orders of magnitude. Measured live on
2026-08-16:

| | |
|---|---|
| `gasPrice` | `20000001` wei = **0.020000001 gwei** |
| `maxFeePerGas` | `40000001` wei |
| Illustrative spend at that price | 1,807,060 × 20000001 = `36141201807060` wei ≈ **0.0000361 OKB** |
| **Enforced maximum under the ceiling** | 1,807,060 × 553,385,056 = `999999999295360` wei ≈ **0.001 OKB** |
| Deployer balance | 0.14997275 OKB |

**Ceiling put to the owner: `TOUCHSTONE_DEPLOY_MAX_SPEND_WEI=1000000000000000`** (0.001 OKB).
It is a proposal until approved — see the banner.

It sits deliberately above the spend at the measured price rather than at it:

- 27.67× the spend at the price measured on 2026-08-16. **That multiple is the point:** what
  the owner approves is the enforced maximum in the table above, not the illustrative figure.
- Permits `553385056` wei/gas — 13.83× the network's current `maxFeePerGas`, so ordinary fee
  volatility between approval and execution does not turn into a spurious abort.
- 0.667% of the deployer balance, so an exhausted ceiling cannot strand the key.

A ceiling pinned to the measured number would be a nominal bound that aborts on the first
price tick; one set at balance would bound nothing. This is chosen to be the tightest number
that is still robust to volatility.

An earlier version proposed no number at all, on the reasoning that proposing one would be
the approval making itself. That was wrong: a recommendation is not an approval, and a gate
priced at "you decide" asks the owner to invent a technical risk parameter they have less
information about than the person who measured it.

## 6. The command

Run from **`contracts/`**, on the **owner's** machine, with the deployer key present and the
publisher key absent. That is where the only Hardhat project, config and script live; running
from the repository root produces `HH1: You are not inside a Hardhat project`. The manifest
path is still resolved against the repository root, which is deliberate — a relative path
resolved against `contracts/` is what lost the first deployment's manifest.

**Shell: Git Bash.** The block below is POSIX `VAR=value command` syntax and does not work in
PowerShell or `cmd.exe`. It also carries no `<placeholders>`: in a POSIX shell `<` is a
redirection, so a command written with them fails in a way that looks like a script bug.

Load the deployer key from its file rather than typing it, so it never enters shell history:

```
set -a; . ../.env.deployer; set +a
```

Then, from `contracts/`:

```
TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=1952 \
TOUCHSTONE_DEPLOY_MAX_SPEND_WEI=1000000000000000 \
TOUCHSTONE_NETWORK=xlayer-testnet \
TOUCHSTONE_RPC_URL=https://testrpc.xlayer.tech/terigon \
TOUCHSTONE_CONFIRMATIONS=3 \
TOUCHSTONE_MAX_FEE_WEI=2000000000000000 \
TOUCHSTONE_PUBLISHER_ADDRESS=0x86A100BDdF8754c95fec97BeC96dBFd64Be44710 \
TOUCHSTONE_OPERATIONS_ADDRESS=0xF901f538d17ED060018F043bCAA1d7f0977Fa65D \
TOUCHSTONE_REPORTER_PUBLIC_KEY=0959d043538a483024722ced848e52ae5dcaf56661e598dc850a9541028b9dba \
TOUCHSTONE_MANIFEST_OUT=deployments/xlayer-testnet-2.json \
npx hardhat run scripts/deploy.js --network xLayerTestnet
```

**`TOUCHSTONE_CONFIRMATIONS=3` does not give the deployment three confirmations.** It sets the
*publication* confirmation depth, which is recorded into the manifest as `confirmations` and
used later when publishing reports. The deploy script's own receipt calls — `deploy.js:234`
and `deploy.js:269` — are bare `wait()`, whose ethers default is `confirms = 1`. **Both
transactions wait for at least one confirmation; neither waits for three.** A receipt may
happen to come back deeper if the transaction was already buried, but nothing in this command
requires that. The variable is here because it belongs in the manifest, not because it
deepens this deployment. **§8's confirmation check is therefore not a formality — it is the
only thing that establishes depth.**

### The destination preserves the superseded manifest

`deployments/xlayer-testnet.json` is **not** the destination and must not be. It records the
superseded deployment, and that record is the only evidence of a registry that exists on
chain. The script refuses a destination that already exists, so naming it would abort rather
than overwrite — but the correct destination is a new file, and the superseded manifest stays
exactly where it is.

## 7. Before the first transaction

Every one of these must hold. Any failure is an abort, not a retry.

- [ ] Working tree clean — `git status --porcelain` returns nothing.
- [ ] `git rev-parse HEAD` equals the **approved packet commit** — the SHA named directly in
      the owner's approval, not one inferred from the blob. Without this, any later clean
      commit would satisfy the runtime check below while `tests/`, `scripts/publish_epoch.py`,
      `scripts/mutation_check.py`, `pyproject.toml` or the manifest schema had moved, and the
      historical packet blob would still hash correctly. The approval binds a commit, not just
      a document. Re-checked against the approval itself at the end of this list.
- [ ] The runtime is identical to the release commit in §2:
      `git diff --stat <release-commit>..HEAD -- contracts/contracts/ contracts/scripts/ touchstone/ contracts/hardhat.config.js contracts/package.json contracts/package-lock.json`
      returns nothing. **Not** "HEAD equals the release commit" — §2.1 requires the packet
      commit to be a later, different commit, so that check could never pass and an earlier
      version of this list demanded it anyway. What matters is that no code which executes or
      is deployed has moved.
- [ ] `python -m pytest -q` — full suite green.
- [ ] `python scripts/mutation_check.py` — every mutant killed.
- [ ] `npx hardhat test` — full contract suite green.
- [ ] `python -m pytest tests/test_e2e_local.py -q` — the managed local-chain loop green.
- [ ] `python -m ruff check .` — clean.
- [ ] `npx hardhat compile` from a clean `artifacts/`, and the **creation** digest and the **runtime template** digest match §2 exactly.
- [ ] The deployer address is the one in §4. Any other address changes the as-deployed runtime digest and invalidates this packet.
- [ ] `eth_chainId` at the endpoint returns `0x7a0`.
- [ ] `eth_syncing` is `false` and the head block advances between two reads. A syncing or
      stalled node can answer reads from stale state, including the nonce this deployment depends on.
- [ ] Deployer balance ≥ the approved ceiling.
- [ ] Deployer nonce is what the operator expects; an unexpected nonce means something else has used this key.
- [ ] The deployer's `latest` and `pending` nonces are **equal**. `pending > latest` means an
      outstanding transaction from this key, which would change the address the registry lands at.
- [ ] `eth_getCode` at the CREATE address for the deployer at that nonce returns `0x`. At
      nonce 3 that address is `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`.
- [ ] The superseded registry still has **no** `Published` or `Corrected` logs. The packet's
      "nothing to migrate" premise depends on it, and a publication after this was written
      would invalidate the whole gate.
- [ ] The three EVM addresses in §4 are distinct and are the ones the owner intends.
- [ ] `deployments/xlayer-testnet-2.json` does not exist.
- [ ] The owner's written approval names **all three** values the banner requires — not this
      document's path or title:
      1. the full packet commit SHA;
      2. the sha256 of the packet blob;
      3. the ceiling in wei.
      **An approval naming fewer than three does not satisfy this check.** An earlier version
      of this list asked for the blob and the ceiling only, while the banner asked for all
      three — so the checklist an operator actually works through was the weaker of the two.
- [ ] `git rev-parse HEAD` equals the approved commit from (1).
- [ ] `git show HEAD:docs/DEPLOYMENT-G1.md | sha256sum` equals the approved blob from (2).
      Hash at the packet commit, **not** the release commit in §2 — they differ and hash
      differently.
- [ ] `TOUCHSTONE_DEPLOY_MAX_SPEND_WEI` in the §6 command equals the approved ceiling from (3).

## 8. After deployment

The command in §6 leaves the owner's shell in `contracts/`. Return to the repository root
before running the root-relative checks in this section:

```
cd ..
```

The publishing-host preflight below is a separate step and starts from the repository root
on that host.

- [ ] Both transactions have receipts with `status = 1` and at least 3 confirmations.
- [ ] Record the address, both transaction hashes, and the deployment block.
- [ ] `eth_getCode` at the address hashes to the **as-deployed** digest in §2 — not the template digest.
- [ ] `expectedChainId()` returns 1952.
- [ ] `owner()` is the deployer in §4.
- [ ] `isPublisherAuthorized(publisher)` is true.
- [ ] `publisherIdentity(publisher)` is non-zero and equals the publisher — the lineage a rotation would carry.
- [ ] The emitted manifest validates against `deployments/manifest.schema.json`.
- [ ] The manifest records `deployment_state: "active"`.
- [ ] `python -m pytest tests/test_deployment_manifests.py -q` passes with the new manifest present.
- [ ] **On the publishing host**, with `TOUCHSTONE_PUBLISHER_PRIVATE_KEY` present and the
      deployer key absent, `python scripts/publish_epoch.py --manifest
      deployments/xlayer-testnet-2.json --preflight` succeeds. **This sends nothing.** It
      cannot run on the owner machine: `publish_epoch.py:43` loads and verifies the publisher
      key before `backend.preflight()` runs at `publish_epoch.py:44`, while §4 forbids that key
      from sharing the owner machine with the deployer key.
- [ ] The `.attempt.json` breadcrumb beside the manifest agrees with it, and its final stage is `authorized`.
- [ ] **Keep the breadcrumb. Do not delete it.** It is the only file carrying the deployment and authorization transaction hashes — the manifest records neither — so deleting it destroys the only local evidence of how this registry came to exist. Archive it beside the manifest.

## 9. Abort criteria

Stop at the first of these. **Never retry automatically** — a retry after a partial deployment
is how a second unrecorded registry gets created.

- Any §7 or §8 check fails. This includes any digest, chain id, owner, authorization or
  lineage mismatch; fewer than three confirmations; a manifest, schema, state or test
  failure; a publishing-host preflight failure; or a breadcrumb mismatch or final stage
  other than `authorized`.
- Either transaction reverted, or still pending beyond the operator's patience.
- An unexpected deployer nonce.
- Spend above the approved ceiling — the script raises after the fact; treat it as an abort.
- The manifest write failed, or the destination existed.
- Authorization failed after deployment succeeded.

## 10. If it goes wrong

A failed attempt is not erased. It is recorded, because a registry that exists on chain
exists whether or not anyone wrote it down — that is the lesson of 2026-08-15.

**That holds from destination reservation onward, not from the first line of the script.**
`reserveDestination` runs at `deploy.js:170`, the first journal line is written at
`deploy.js:203`, and the first send is at `deploy.js:213`.

- A failure **before 170** — a missing `TOUCHSTONE_MANIFEST_OUT` or `maxFeeWei` — leaves no
  files at all.
- A failure **inside `reserveDestination`** can leave only the manifest: it creates the
  manifest at `:457` and the companion at `:458` with two sequential writes, so "both files
  are reserved" is true only once the function has *returned*.
- A failure **between 170 and 203** leaves both reserved files and an empty journal. This
  window covers a missing or invalid deployment-spend ceiling — `deploymentSpendCeiling` is
  called at `deploy.js:175` and passes the value to `exactBigInt` at `deploy.js:588` — a
  ceiling too small to permit even 1 wei/gas, a network `maxFeePerGas` above what the ceiling
  allows, a balance below the ceiling, artifact/factory or gas-estimation or fee-data
  failures, and a failure writing the `prepared` record itself.

**None of these has broadcast anything** — the first send is ten lines after the journal
opens. That is why a missing breadcrumb here is safe rather than ambiguous, and it is what
makes the empty-journal row in the table below a statement of fact rather than a guess.

1. **Stop.** Do not re-run the command.
2. Read `<manifest>.attempt.json`. It is an append-only journal, one JSON object per line,
   fsynced as each stage completes. **The last complete line is how far the attempt got**,
   and every earlier line survives it. What each terminal stage tells you differs, so read
   the stage before assuming what it contains:

   | Last stage | What exists | What to do |
   |---|---|---|
   | *(file empty)* | **This invocation broadcast nothing** | The `prepared` line is appended and fsynced at `deploy.js:203` before the first send at `deploy.js:213`, so this invocation never reached a send. Compare both the deployer's `latest` and `pending` nonces with the values checked before the attempt. If either moved, stop and investigate: the empty journal does not prove the key was unused elsewhere. If neither moved, the failed attempt is still not erased. In either branch, keep both reserved files, write the incident note required below, and require a new destination plus fresh owner approval before another attempt |
   | `prepared` | **No hash journaled. A broadcast is possible but not established** | **The opposite of the row above** — do not read that row's reasoning here. `prepared` means execution passed `deploy.js:203`, but that alone does not prove it reached the RPC send: `deployContract()` at `:213` can fail before broadcasting. It also does not prove it did not — the node returns a hash before the journal is appended, so a crash in that window leaves `prepared` last after a real broadcast. **Treat it as unknown and resolve it on chain.** Read both nonces and search for a deployment from this key. Then keep both reserved files, write the incident note, and require a new destination plus fresh owner approval |
   | `broadcast` | One or more hashes, outcome unknown | Read each receipt. This line is written at the RPC boundary and is the earliest evidence |
   | `deploying` | Deployment hash; no address or block yet | Read the receipt to learn whether it mined and at what address |
   | `deployed` | Address, deployment hash, block. **No authorization hash journaled** | The registry exists. **Do not conclude it is unauthorized** — the authorization is broadcast before the journal records it, so a crash in that window leaves `deployed` last even though the transaction reached the node. Check the deployer's nonce, `isPublisherAuthorized`, `publisherIdentity`, and any `PublisherAuthorized` log at that address before classifying it |
   | `authorizing` | Both hashes; authorization outcome unknown | Read the authorization receipt |
   | `authorized` | Both hashes; deployment and authorization succeeded; post-deployment verification or manifest persistence failed | Freshly verify the runtime code and digest, chain id, owner, authorization and publisher lineage on chain. Reconstruct only if every value matches |
3. Read the outcome off the chain rather than guessing it. Run a receipt command only for
   each hash the last complete record actually contains. A `broadcast` record carries an
   array instead of the named fields, while an empty or `prepared` journal carries none:

   ```
   # If deployment_transaction is present:
   cast receipt <deployment_transaction> --rpc-url https://testrpc.xlayer.tech/terigon
   # If authorization_transaction is present:
   cast receipt <authorization_transaction> --rpc-url https://testrpc.xlayer.tech/terigon
   # For a broadcast record, run once for each hash in broadcast_transactions:
   cast receipt <broadcast_transaction> --rpc-url https://testrpc.xlayer.tech/terigon
   # Run state checks only when the record or deployment receipt supplies the address:
   cast call <address> "owner()(address)" --rpc-url https://testrpc.xlayer.tech/terigon
   cast call <address> "isPublisherAuthorized(address)(bool)"      0x86A100BDdF8754c95fec97BeC96dBFd64Be44710      --rpc-url https://testrpc.xlayer.tech/terigon
   cast call <address> "publisherIdentity(address)(address)"      0x86A100BDdF8754c95fec97BeC96dBFd64Be44710      --rpc-url https://testrpc.xlayer.tech/terigon
   ```

   For `authorized`, also fetch `eth_getCode`, hash the returned runtime bytecode and compare
   it with the approved as-deployed digest before reconstructing a manifest. The journal
   proves the two transactions succeeded; it does not prove that later verification passed.

4. **Do not try to write a manifest unless the attempt got as far as `authorized`.** A
   manifest cannot describe an incomplete deployment, and attempting one produces a file
   that is refused — verified: the schema rejects a `deployment_transaction` field as
   unknown, and a registry deployed but not yet authorized has a zero
   `publisher_identity_address`, which the loader refuses outright. An earlier version of
   this step told the operator to record "whatever the journal holds", which for every
   stage before `authorized` is impossible.

   | Last stage | What to record |
   |---|---|
   | empty, `prepared`, `broadcast`, `deploying` | **Keep the journal and write an incident note beside it** — date, what the journal contains, the deployer nonce before and after, and what was found on chain. No manifest |
   | `deployed` | Same — but establish the authorization state on chain first, because the journal cannot prove it either way. If authorization did land, treat it as `authorized`; if it did not, there is no publisher lineage for a manifest to record |
   | `authorizing` | Read the authorization receipt first. If it succeeded, treat as `authorized`; if it failed or is absent, treat as `deployed` |
   | `authorized` | A manifest is possible. Write it with `deployment_state: "superseded"`, and verify it loads and is refused for publication |

   **Never invent a value to satisfy the schema.** A manifest that exists only because a
   placeholder was supplied is worse than no manifest: it will be believed.
   Verify it is refused:

   ```
   python -c "from touchstone.deployment import DeploymentManifest as M;      m = M.load('deployments/<failed>.json');      print(m.deployment_state, m.is_active)"
   ```

   must print `superseded False`. This applies only to the `authorized` row above.

5. **If the registry deployed and authorization succeeded but the run still failed**, the
   contract is live with a publisher authorized on it. Revoking is a **separate owner
   approval**, and the command is:

   ```
   cast send <address> "revokePublisher(address)"      0x86A100BDdF8754c95fec97BeC96dBFd64Be44710      --rpc-url https://testrpc.xlayer.tech/terigon      --private-key <deployer key, owner machine only>
   ```

   Confirm with the `isPublisherAuthorized` call above returning `false`. Do not leave an
   authorized publisher on a registry nobody intends to use.

6. Only then consider a fresh attempt, with a new destination and fresh owner approval.

## 11. What this gate does not do

- It does not publish anything. The first live epoch is a **separate** owner gate.
- It does not deploy `AssetGate`.
- It does not touch mainnet. Mainnet is unscheduled and conditional on a proven testnet loop.
- It does not authorize a paid model call, a public post, a submission, or a domain.
