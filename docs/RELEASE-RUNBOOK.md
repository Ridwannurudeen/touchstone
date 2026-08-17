# Release runbook

**Unpublished text in a file.** This is not a release, not an approval, and not
authorisation to deploy, publish, spend, post, or submit. Nothing in it has been
executed from this document. Addresses that have not been deployed are written
`not_deployed`. Host and supervisor packaging that have not been decided are
written `not_configured`.

How a release is cut, what a reviewer must check in the document that records it,
how a registry reaches X Layer testnet, and why a publication cannot be undone.

Day-to-day running of a live service is `docs/OPERATIONS.md`. The one live
publication that has been prepared and not run is `docs/CANARY-G1B.md`. This
file does not replace either.

---

## 1. What a release is

A release is a JSON document written by `scripts/build_release.py`. It names
the commit, the compiler settings, the lockfile digest, the fixture and source-
manifest digests, the deployment records that are real manifests, and any test
counts the caller actually supplies.

It is not a git tag. It is not a spoken test count. The builder does not stamp
the clock, does not run the suite, and does not invent a number. A builder that
read the clock could not be reproduced; a builder that ran the suite, or guessed
its result, would be another place a figure could appear that nobody can point
at a run for.

It also does not execute `contracts/hardhat.config.js`. That file reads the
deployer private key from the environment; evaluating it would be a secret
read. The compiler settings live in `contracts/solidity.json`, which the Hardhat
config requires and the builder parses as data.

They used to be recovered by regex over the config, which took the first object
that *looked* like a solidity block — so a commented-out line, or an unused
config declared earlier in the file, was reported as what the contracts were
built with. Pattern-matching a program to find out how it was configured is
unsound however carefully it is done; the settings are data and now live in a
data file.

The document is unsigned JSON. `docs/THREAT-MODEL.md` still lists signed
release manifests as PLAN-T13 work. This builder does not sign.

`release_version` is the literal `"1"`.

---

## 2. How to produce the document

From the repository root, on a tree the reviewer can check out:

```text
python scripts/build_release.py \
  --out <path> \
  --built-at <ISO-8601 UTC instant ending in Z>
```

Required arguments:

| Flag | Meaning |
|---|---|
| `--out` | Path to write the document. Parent directories are created. The write is to a sibling `.tmp` then renamed. |
| `--built-at` | ISO-8601 UTC instant recorded as `built_at`. Must match `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z`. The builder does not read the clock. A stamp without the trailing `Z` is refused. |

Optional arguments:

| Flag | Meaning |
|---|---|
| `--root` | Repository root to describe. Defaults to the tree this script lives in. |
| `--tests-passed` | Passed-test count from a run the caller actually performed. Non-negative integer. |
| `--tests-skipped` | Skipped-test count from that same run. |
| `--mutants-killed` | Mutants killed, from a mutation run the caller actually performed. |
| `--mutants-total` | Mutants total, from that same mutation run. |

Zero is a real count. An omitted flag stays `null` and is explained in
`tests.note`. A partial set of flags records the missing names and does not
fill them with zero. Supplying none of the four writes the note that the
builder does not run the suite and will not invent the counts.

On success the script prints `wrote <path>` and exits 0.

Failures split by kind, and the exit codes differ:

| What is wrong | Output | Exit |
|---|---|---|
| A required flag is absent (`--out`, `--built-at`) | argparse usage error naming the flag | **2** |
| An input file is missing, the tree is unreadable, git fails, or `--built-at` is malformed | `RELEASE FAIL: …` on stderr | **1** |

An earlier version of this paragraph said "a missing required input … exits 1", which
readers would apply to a forgotten `--out`. That case never reaches the script's own error
handling — argparse rejects it first and exits 2. Both are refusals and neither writes a
file, but a runbook that names the wrong code sends whoever is reading it looking in the
wrong place.

It does not leave a half-written destination: the temporary file is replaced only after the
bytes are complete.

Two runs over the same tree with the same arguments produce byte-identical
output: keys sorted, indent 2, UTF-8, `\n` separators, trailing newline.
Writing through a text mode that translated line endings would change the
digest; the builder writes bytes.

A dirty working tree still gets a document. `commit.tree_clean` is `false`
when `git status --porcelain` is non-empty. Refusing would hide the dirt;
pretending the tree is clean would be a false release.

---

## 3. What the document contains

Every field is either read from the tree or taken as an explicit argument.

| Field | Source |
|---|---|
| `release_version` | Literal `"1"` |
| `commit.sha` | `git rev-parse HEAD`, required to be 40 lowercase hex |
| `commit.tree_clean` | `git status --porcelain` is empty |
| `built_at` | `--built-at` |
| `python.requires_python` | `pyproject.toml` `[project].requires-python` |
| `python.runtime` / `python.dev` | Pinned `==` dependencies from `[project].dependencies` and `[project.optional-dependencies].dev` |
| `contracts.solidity` | `contracts/solidity.json` `version` |
| `contracts.optimizer` | `enabled` and `runs` from the same file |
| `contracts.evm_version` | `settings.evmVersion` if named; otherwise `null`. Hardhat's default is not written: reading a default out of another program's memory is how a release claims a target nobody set. The committed file declares `paris`, and `hardhat compile` reports `evm target: paris`, so the document and the build agree. |
| `contracts.solidity_config_sha256` | SHA-256 of the bytes of `contracts/solidity.json` |
| `contracts.package_lock_sha256` | SHA-256 of the bytes of `contracts/package-lock.json` |
| `deployments` | Each real `deployments/*.json` manifest: `network`, `chain_id`, `registry_address`, `deployment_state`, `registry_runtime_bytecode_sha256` |
| `schemas` | SHA-256 of `deployments/manifest.schema.json` |
| `fixtures` | SHA-256 of every file under `fixtures/` |
| `manifests` | SHA-256 of every file under `manifests/sources/` |
| `tests` | The four counts, or `null`, plus `note` |

Digests are of the bytes on disk. A text read that translated line endings
would change them.

Files that look like deployments and are not:

- `deployments/manifest.schema.json` — the schema, recorded under `schemas`
- any name containing `.template.` — placeholder addresses, including the
  `0x1111…` marker in `xlayer-mainnet.template.json`
- any name containing `.attempt.` — the append-only deploy journal, not a
  deployment

A missing or all-zero `registry_address` is recorded as the literal
`not_deployed`. That is not a placeholder to be filled in later.

On this tree, as of the files read for this runbook, the real manifests are:

| Path | `deployment_state` | `registry_address` |
|---|---|---|
| `deployments/xlayer-testnet-2.json` | `active` | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C` |
| `deployments/xlayer-testnet.json` | `superseded` | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d` |

Mainnet is `not_deployed`. There is no mainnet manifest that is not a
template.

---

## 4. What a reviewer should check

Do not take the document's word. Recompute.

1. **`commit.sha` is this tree's `HEAD`.** `git rev-parse HEAD` must match
   exactly. A 40-hex string that is not this `HEAD` is someone else's release.
2. **`commit.tree_clean` matches porcelain.** If the working tree has
   uncommitted files and the document says `true`, the document is false.
   If it says `false`, the release does not correspond to the named commit
   alone; say so, do not paper it over.
3. **`built_at` is the argument that was passed**, not "about now". It must
   end in `Z`.
4. **Python pins are `==` pins split into runtime and dev.**
   `cryptography`, `psutil` and `web3` belong in runtime; `pytest`,
   `jsonschema`, `pyyaml` and `ruff` belong in dev. An unpinned specifier
   would have been refused at build time.
5. **Solidity settings match the source.** The committed config names
   `0.8.24`, optimizer `enabled: true`, `runs: 200`, and does not name an
   EVM version. `evm_version` must therefore be `null`.
6. **`package_lock_sha256` is `sha256(contracts/package-lock.json)`** of the
   bytes on disk.
7. **Templates, attempt journals and the schema are not under
   `deployments`.** A `0x1111…` address in the release is a template that
   leaked.
8. **The superseded registry is still listed**, with
   `deployment_state: superseded`. Omitting it would make the release look
   as if the obsolete contract was gone.
9. **The active registry address and runtime digest match
   `deployments/xlayer-testnet-2.json`**, which itself is the record written
   by the deploy script. The live digest recorded there is
   `cecada9e4caefaa153ea321d5831b053ad8750ffe58a4ac0ee61b81ba4dbc561`.
10. **Fixture and source-manifest digests match the files.** Spot-check at
    least the USTB pair and `manifests/sources/ustb.json`.
11. **Test counts were supplied by a run, or they are `null`.** A zero that
    nobody counted is the defect this field exists to stop. Mutation counts
    omitted while pytest counts are present is honest; inventing the missing
    half is not.
12. **No absolute paths, no backslashes in keys or string values.** The
    builder is tested for this.
13. **Two independent encodings of the parsed object are byte-identical**
    (`encode_release` sorts keys and ends in a newline).

The builder does not claim the tests passed. Only the caller who passed
`--tests-passed` does, and only for the run they point at.

---

## 5. Deployment sequence — X Layer testnet

The testnet registry **already exists**. The sequence below is how that
happened, and how a replacement would happen. It is not authorisation to do
either again.

Current chain facts, recorded in `docs/OPERATIONS.md` and
`docs/DEPLOYMENT-G1-EXECUTED.md` and in `deployments/xlayer-testnet-2.json`:

| | |
|---|---|
| Network | X Layer testnet, chain id **1952** |
| RPC recorded in the manifest | `https://testrpc.xlayer.tech/terigon` |
| Active registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, block 38489602 |
| Publisher | `0x86A100BDdF8754c95fec97BeC96dBFd64Be44710`, authorised, identity mapped to itself |
| `deployment_state` | `active` |
| Reports on this registry | **one.** USTB `latestSequence` is 1 — sequence 1, epoch `ustb-2026-08-17`, state `UNVERIFIABLE`, block 38526525, tx `0x5107140c5c9c755026de5e3193e14b9863aacc2962f78b8516bf00075be6b869`. Every other asset key is still zero |
| Predecessor | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, block 38369203, `deployment_state: superseded`. It predates `epochKey` and must not be published to. It published nothing. |

**Do not redeploy because a release document was cut.** A second deploy to
the same destination is refused by `contracts/scripts/deploy.js`: it will
not overwrite an existing manifest or its `.attempt.json` journal. A new
registry is a new destination, a new owner gate, and a new digest-bound
approval. The last such packet is `docs/DEPLOYMENT-G1.md`; its execution
record is `docs/DEPLOYMENT-G1-EXECUTED.md`. That approval did not cover a
publication, `AssetGate`, or mainnet.

If a **new** testnet registry is ever authorised, the order the deploy
script itself enforces is:

1. Owner approval naming the packet commit, the packet blob sha256, and a
   spend ceiling. A general "approval for the downstream work" is not
   enough; that claim was rejected on audit.
2. Environment on the **deploying** host only, never the long-running
   publishing host: `TOUCHSTONE_DEPLOYER_PRIVATE_KEY` (read by
   `hardhat.config.js`, never written there),
   `TOUCHSTONE_PUBLISHER_ADDRESS`, `TOUCHSTONE_OPERATIONS_ADDRESS`,
   `TOUCHSTONE_REPORTER_PUBLIC_KEY`, `TOUCHSTONE_NETWORK=xlayer-testnet`,
   `TOUCHSTONE_RPC_URL`, `TOUCHSTONE_CONFIRMATIONS`,
   `TOUCHSTONE_MAX_FEE_WEI`, `TOUCHSTONE_MANIFEST_OUT`,
   `TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=1952`,
   `TOUCHSTONE_DEPLOY_MAX_SPEND_WEI`. The confirmation variable is a
   positive statement of the chain id. A stale boolean would silently
   enable a send.
3. The script validates **before** the first transaction: publisher ≠
   deployer, operations distinct from both, reporter key is 32 lowercase
   hex bytes, network name bound to this chain id, HTTPS RPC with no
   credentials, destination reserved exclusively, worst-case gas under the
   approved ceiling, deployer balance at least the ceiling.
4. Two transactions, journalled at the moment the node returns each hash:
   deploy `TouchstoneRegistry(expectedChainId=1952)`, then
   `authorizePublisher`. Stages
   `prepared → broadcast → deploying → deployed → broadcast → authorizing → authorized`
   are appended to `<manifest>.attempt.json`. **Never re-run the command
   to "check" it.** Read the receipts. If the outcome is unclear, the
   journal is the local record.
5. The script writes the manifest, including
   `deployment_state: "active"` and the runtime bytecode sha256 it just
   read from the chain.
6. `python scripts/publish_epoch.py --manifest <new manifest> --preflight`
   must report the publisher authorised and `published: false`. It signs
   nothing.

The deprecated X Layer testnet on chain **195** must never be used.
`deploy.js` binds the name `xlayer-testnet` to 1952.

**Publication is not a deployment step.** The next publication is one USTB
canary epoch under its own owner gate, prepared in `docs/CANARY-G1B.md`
and not authorised by a release document. `AssetGate` is not deployed on
this chain; the deployment approval explicitly did not cover it.

**Host packaging is `not_configured`.** `docs/OPERATIONS.md` records the
host and supervisor units as unset; this runbook does not invent them.
PLAN-T13 named systemd units as later packaging work. They are not in
this tree.

**Mainnet is `not_deployed`.** It is owner-gated, conditional on a proven
testnet loop (nothing has published to testnet), and additionally blocked
until the deployer and publisher keys sit on separate hosts. They
currently do not; see `docs/DEPLOYMENT-G1-EXECUTED.md`.

---

## 6. Rollback

There is none in the sense of undoing a publication, and that is by
design.

The transparency log is append-only (`touchstone/translog.py`: "entries
are never updated or removed"). The registry only writes a new
`Report` at `latestSequence + 1`. There is no delete, no overwrite, no
admin rewrite of history.

The registry is not upgradeable. `contracts/contracts/TouchstoneRegistry.sol`
is a single implementation contract. It has no proxy, no `delegatecall`,
and no `selfdestruct`. `owner` and `expectedChainId` are `immutable`.
Replacing the contract means deploying another one and another owner
gate. The predecessor on this testnet is what that looks like: it is
marked `superseded` and refused by `scripts/run_service.py` **before any
key is read**. Prose in a notes field refuses nothing.

The remedy for a published report that is wrong is a **correction**:
`publishCorrection` on the same registry, carrying the **same** `epochKey`
as the report it corrects. A correction that named a different epoch is
refused (`CorrectionEpochMismatch`). A second first-publication for an
epoch that already has one is refused (`EpochAlreadyPublished`). The
correction is a new sequence; the original report stays.

`scripts/publish_epoch.py --correction` is the CLI for that path. It still
needs a signed report, a report URI, a workspace, and an active manifest.
It is not authorised by this document.

A canary that publishes `UNVERIFIABLE` about an unseeded workspace needs
no correction. That result is a true statement; see `docs/CANARY-G1B.md`.

A failed **deploy** is not rolled back on chain either. If only the
registry landed and authorisation did not, the attempt journal says so.
Mark that deployment superseded rather than reusing it. Never retry the
deploy command automatically.

A release document itself is not on chain. Replacing it means writing
another one, with a new `--built-at` and a freshly read tree. The earlier
file is not edited.

---

## 7. What this document does not claim

- That a release has been cut. No release document is committed in this
  tree.
- That tests passed a particular count. The builder will not invent one.
- That the active registry has been published to. It has not.
- That `AssetGate` is on any persistent chain. It is `not_deployed`.
- That a production host, supervisor unit, public URL, or mainnet
  registry exists. Host and units are `not_configured`; mainnet is
  `not_deployed`.
- That cutting a release authorises a canary, a post, or a submission.
  Those remain owner gates, listed in `docs/OPERATIONS.md`.
