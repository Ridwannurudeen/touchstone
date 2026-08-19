# Build handoff — full context for the agent taking this on

You are picking up a live, deployed project mid-build. Read this whole file before running
anything. `docs/BUILD-PLAN.md` is the work; this is everything you need around it to do the work
without breaking what is already live.

---

## 0. Read this first: the tree is not clean

**There is uncommitted, unverified work in `touchstone/report.py` and `touchstone/verify.py`.**

It is the start of Phase 0.1 (schema v5). What I did:

- `report.py`: `REPORT_VERSION` → `…v5`, kept `REPORT_VERSION_V4`, added a `policy=` parameter to
  `build_observation_report`, added the `policy` field to the returned report, added
  `_policy_record()`.
- `verify.py`: `BUNDLE_VERSION` → `…v5`, kept `BUNDLE_VERSION_V4`, split `_REPORT_FIELDS` and
  `_BUNDLE_FIELDS` into v4/v5 sets with `*_BY_VERSION` dispatch maps, added
  `_verify_policy_record()`, made `create_bundle` accept `policy_manifest=` and check it against
  the digest the report commits to.

What I verified: `ruff check` passed, and the three retained v4 bundles still verify under the
new code.

**What I did not verify: the full test suite.** Many tests reference `REPORT_VERSION` and the
exact field sets, and I expect some to fail. Treat this work as a sketch, not a foundation.

**Your first decision.** Either finish and prove it, or throw it away and start clean:

```sh
git status --porcelain          # see exactly what is modified
git stash                       # park it
# or
git checkout -- touchstone/report.py touchstone/verify.py   # discard it
```

Do not build on top of it until `python -m pytest -q` is green. Starting clean is a perfectly
good choice; the design in `BUILD-PLAN.md` §0.1 is what matters, not my half of it.

---

## 1. What this project is

Touchstone turns a fund issuer's published disclosures into machine-checkable controls,
evaluates them deterministically against retained evidence, signs the result, and publishes it
to an append-only registry on X Layer so a contract can decide whether to proceed.

The chain of custody is the product:

```
issuer bytes → AI proposes cited controls → deterministic gates → human approval
   → deterministic evaluation → Ed25519-signed report → X Layer registry → consumer gate
```

**The one thing that must never break:** a stranger can verify a published report offline, from
the bundle, without trusting the website or us. Everything else is negotiable.

### The property that makes it worth anything

It refuses to overclaim. Value controls observe a row only when a capture at least 24 hours
older carries it unchanged, so a provisional number cannot be certified. On 2026-08-17 it read a
NAV; by 2026-08-18 the issuer had restated that row. It had already declined to confirm it.

**Never weaken a control to make a result look better.** If you find yourself considering it,
that is the signal you have taken a wrong turn.

---

## 2. Environment

| | |
|---|---|
| Repo | `C:\Users\gudma\OneDrive\Desktop\GITHUB-FILES\touchstone` |
| Branch | `feat/t12-ci` — **all work is here**; `main` is ~70 commits behind and untouched |
| Python | 3.12, deps in `pyproject.toml` (`cryptography`, `psutil`, `web3`) |
| Tests | `python -m pytest -q` — 1,877 pass, 1 skipped locally, takes 6–7 minutes |
| Lint | `python -m ruff check .` — must be clean |
| Mutation | `python scripts/mutation_check.py` — 125/125 killed, needs a clean tree |
| Shell | Windows, Git Bash. Both `bash` and PowerShell available |
| Site | https://touchstone.gudman.xyz, served from `/opt/touchstone-site` on `root@75.119.153.252` |
| Live chains | X Layer testnet 1952, X Layer mainnet 196 |

### Live deployments — real value, do not experiment against these

| What | Where |
|---|---|
| Testnet registry | `0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C`, chain 1952, 3 reports |
| Mainnet registry | `0xc9d58e4496bF061C3177301Ff02518eBB70AD30d`, chain 196, 2 reports |
| Testnet AssetGate | `0xAac48DC261B04737FDCB101D5049395121034a83` |
| Observer daemon | `touchstone-observer@xlayer-mainnet` on the VPS, running since 2026-08-18 |

🚨 **The mainnet registry address is also a superseded registry on chain 1952.** Same deployer,
nonce 0. The chain id is the only discriminator. Never resolve that address without pinning the
chain.

---

## 3. How this project works, and why it is strict

These are not style preferences. Each exists because its absence caused a real defect.

1. **Verify before asserting.** Read the file, run the command, check the chain. Never "should
   work". A claim in a commit message or a doc must be backed by something you actually ran.
2. **Never publish a claim the evidence does not support.** This applies to code, docs, the
   website and commit messages equally. It is the product.
3. **Tests are not optional.** Every change runs the full suite. New behaviour gets tests that
   would fail without it.
4. **Comments and docstrings explain *why*, never *what*.** Look at any existing module: they
   record the defect that motivated the design. Match that.
5. **Small verified increments.** Commit working states; do not accumulate a giant diff.
6. **No attribution to any AI tool** in code, commits or PRs. Ever.

### Commit messages

Long, specific, and about *why*. State what was verified and how. If you got something wrong
earlier, say so in the message rather than quietly fixing it — the git history is part of the
audit trail. Read `git log` for the register.

---

## 4. Traps that have already cost time

Every one of these has bitten at least once in this repo.

**CRLF is load-bearing, four times over.** `.gitattributes` normalises what git *stores*, not
what a Windows checkout *holds*. A tar of the working tree shipped CRLF systemd units to the
host after the rule was added to prevent exactly that. **Install deploy files by piping
`git show ":<path>"` over ssh, then count CR on the host.** Check `git ls-files --eol`.

**`cmd | tail && git commit` hides the exit code.** A red suite committed silently once. Redirect
to a file and read `$?`, or use `${PIPESTATUS[0]}`.

**`comm` needs `LC_ALL=C` on both inputs.** Git Bash and the Linux host disagree on collation;
`comm` reported differences that did not exist, twice. Prefer comparing in Python.

**`date` prints local time (UTC+1).** Reading it as UTC ran an epoch twenty minutes early and
cost the project its first `CONFIRMED` state. Always `date -u`.

**Codex hangs on stdin in background mode.** Always `codex exec … < /dev/null`. Check health by
**CPU seconds**, not process count — a hung process still exists. Kill orphans before starting;
exit `-1073741502` (`0xC0000142`) means process exhaustion.

**Two workspaces exist and must never be cross-wired.** Testnet
`C:\Users\gudma\touchstone-workspace\ustb`, mainnet `…-workspace-mainnet\ustb`. Publishing to the
wrong one succeeds on chain and corrupts the other's transparency hash chain.

**The site deploy's `chown -R www-data` breaks the status timer.** `status.html` is
host-generated and owned `touchstone-observer:www-data`. Restore it after every deploy.

**Hand-sweeping the public record does not work.** Four attempts, four misses, twice after I had
declared it clean. That is why Phase 0.2 replaces it with generation plus a CI gate. Do not
"just check carefully" — build the gate.

---

## 5. The build plan

`docs/BUILD-PLAN.md` is authoritative. `docs/AUDIT-RESPONSE.md` maps each item to the external
auditor's findings so a re-audit can check them off.

Order, with the reasoning compressed:

| Phase | What | Why it is here |
|---|---|---|
| **0.1** | Report/bundle schema v5, v4 still verifiable | Everything downstream adds fields; the verifier accepts one exact schema, so this gates it all |
| **0.2** | Canonical `project-state.json` + CI contradiction gate | Hand-sweeping has failed four times |
| **1.1** | Evaluate and report per policy | Policy evaluation exists and is proven; nothing consumes it |
| **1.2** | Publish per policy key | No registry change — the contract is keyed by an opaque `bytes32` |
| **1.3** | One gate per policy | **No Solidity change.** Produces the demo: one pass, one refusal, same block |
| **2.1** | `GuardedAction` | An action inseparable from its gate |
| **2.2** | Builder Code / ERC-8021 | Owner registers |
| **2.3** | Integration kit (`sdk/`) | Five-minute integration |
| **3.1** | Narrow the public claim | **Do this early — two hours, and the overclaim is live now** |
| **3.2** | Signed approvals (EIP-712) | Approval is currently an unauthenticated field |
| **3.3** | Registry v2 | Ed25519 signs the report; EIP-712 attests the digest on chain |
| **4.1** | `/judge` page | The submission is a URL; it carries the argument |
| **4.2** | Reshoot the film | Now with a pass *and* a refusal |
| **5.x** | Operations hardening | 8 sub-items, several owner-gated |
| **6.x** | AI benchmark, FOBXX | Breadth last, and **not USDY** |

Critical path `0.1 → 1.1 → 1.2 → 1.3 → 2.1 → 4.1`, ~9–12 days. Total ~26–35 days.

### The key design decisions, already settled — do not relitigate without new evidence

- **A policy is a versioned subset of approved controls.** It cannot add a control, reinstate a
  declined one, change a threshold, or be edited in place. `touchstone/policy.py` enforces this
  and has 31 tests.
- **Policies publish under `<asset>#policy:<id>:<version>`.** The registry is keyed by an opaque
  `bytes32`; no contract change. `publish.py` and `verify.py` already accept the format.
- **`AssetGate` needs no Solidity change.** `check(bytes32)` passes its argument straight to
  `getLatestReport`. Deploy one gate per policy, each pinned to that policy's control-set root.
- **Registry v2 keeps Ed25519 for reports** and adds EIP-712 as a *separate attestation* over the
  report digest. They assert different things, so they cannot contradict; only the second is
  EVM-checkable.
- **The five published reports stay legacy v1**, unchanged, forever verifiable.

---

## 6. What you cannot do — stop and ask

These need the owner. Build everything up to the gate, then stop and say so plainly.

- **Any mainnet or testnet deploy or publish.** Real value, irreversible.
- **Enabling `touchstone-publisher@`** — puts a signing key on a shared web host.
- **The rebrand decision** — an existing product occupies a near-identical architecture.
- **External integration counterparty**, **Builder Code registration**, **administrative
  eligibility evidence** (X post URL, form receipt, making the repo public).
- **Resolving the workspace split** (`BUILD-PLAN.md` §5.1) — it moves the data backing the live
  mainnet reports.
- **Anything that spends gas.** One send per transfer, never a retry to "check".

---

## 7. Working with Codex

The owner's standing rule: **consult Codex before each significant step, build to spec, submit
for audit, and only move on after a pass.** It has caught real defects — including a false claim
that had reached a delivered film.

```sh
codex exec -s danger-full-access -C "C:/Users/gudma/OneDrive/Desktop/GITHUB-FILES/touchstone" \
  "$(cat brief.md)" < /dev/null > run.log 2>&1
```

Kill orphans first. Health = CPU seconds. Tell it to prefer few broad commands.

**Verify what it tells you.** It has been wrong — it budgeted two days to change `AssetGate`
when its own earlier memo correctly said no change was needed. It has also been right when I was
sure it was wrong. Check both ways.

---

## 8. Definition of done, per item

Not "the code is written". An item is done when:

1. `python -m pytest -q` is green and the new behaviour has a test that fails without it.
2. `python -m ruff check .` is clean.
3. If it touched anything the mutation harness covers, `scripts/mutation_check.py` is 125/125
   (bump `EXPECTED_MUTATIONS` when adding one).
4. Every public claim it changes is true — checked, not assumed.
5. The commit message says what was verified and how.
6. `docs/AUDIT-RESPONSE.md` is updated so the re-audit can trace it.

**The final gate is an external re-audit** against the auditor's original findings. Build so that
each one can be checked off with evidence a stranger can inspect.

---

## 9. If you are unsure

Prefer the honest, smaller claim. This project's entire value is that it does not say more than
it can show — and that standard applies to the people building it exactly as much as to the
reports it publishes.
