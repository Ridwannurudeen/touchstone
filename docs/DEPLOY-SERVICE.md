# Running Touchstone as a service

This installs the watcher, the daily epoch service and the public status snapshot on a host,
under systemd, as a dedicated identity.

**Read the deviation section first.** The host chosen here is not the host this project's own
key-management document asks for, and installing these units does not make role separation
true. It is recorded rather than implied.

---

## 1. The deviation, stated before the instructions

`docs/KEY-MANAGEMENT.md` separates the publisher and the reporter by host. These units run
both on one machine, and that machine is `75.119.153.252` — a shared box already serving
roughly 37 unrelated public vhosts, including this project's own site.

An external reviewer recommended a dedicated VPS whose only role is publishing, and called the
shared host a constrained interim fallback rather than a custody design. The owner chose the
shared host. That is a cost decision, and it is recorded here so nobody later reads a running
service as evidence of an isolation that does not exist.

What the layout below does to bound it:

| Concern | What is done | What is still true |
|---|---|---|
| Web compromise reaching the key | The service account is **not** the nginx user, and the workspace is outside the served tree | A root compromise of the box takes everything |
| Secret readable by the service identity | `EnvironmentFile` is root-owned `0600`; systemd reads it as root and drops privileges before `ExecStart` | The secret is in the process environment once running |
| Blast radius of the frequent process | The **observer holds no key**, runs as its own Unix identity, and can write only `evidence/`, `observations.jsonl` and its own lock — not the transparency log, pending journal, incidents or operations | It still writes the evidence the publisher confirms against, so it can decide what a publication *concludes* — **R-13** in `docs/THREAT-MODEL.md`. That one is inherent to letting a second process capture evidence at all |
| Noisy-neighbour interference | `MemoryMax`, `CPUQuota`, `TasksMax` on every unit | The box runs hot and is shared |

**The observer is the part that is safe to run here.** It has no `EnvironmentFile`, imports no
signer or publisher, and two tests parse its import graph to keep that true. Install it freely.

⚠️ **This was corrected after an audit, and then fixed.** The first version of this layout ran
the observer and the publisher as one Unix identity while claiming a compromise of the observer
"yields retained public artifacts". That was an operating-system claim the layout did not
support: systemd places the publisher's secrets in the publisher process's environment, and
same-UID code can read another same-UID process's environment on a host with ordinary `/proc`
permissions. The observer now runs as **`touchstone-observer`**, a separate identity that is
never given a key file, and the publisher keeps `touchstone`. They share only
`touchstone-data`, the group that owns the workspace.

**The publisher is the part that carries the deviation.** It is a separate, owner-gated step.

---

## 2. Layout

| Path | Owner | Mode | Holds |
|---|---|---|---|
| `/opt/touchstone` | `root` | `0755` | the checkout, pinned to a commit |
| `/opt/touchstone/.venv` | `root` | `0755` | dependencies |
| `/var/lib/touchstone` | `root:touchstone-data` | **`2775`** | explicitly provisioned shared-state root; no service owns it |
| `/var/lib/touchstone/<network>` | `root:touchstone-data` | **`2775`** | root-created network namespace shared by the asset workspaces |
| `/var/lib/touchstone/<network>/ustb` | `touchstone:touchstone-data` | **`2750`** | workspace root — the publisher's. The group traverses and reads; it cannot create or replace |
| `…/ustb/evidence/` | `touchstone-observer:touchstone-data` | `2770` | the one tree the observer writes |
| `…/ustb/observations.jsonl`, `observer.lock` | `touchstone-observer:touchstone-data` | `664` | pre-created, so the observer never needs write on the root |
| `/etc/touchstone/<network>.env` | `root` | `0600` | publisher key, signing seed |
| `/etc/touchstone/<network>.status.env` | `root` | `0644` | registry address; public values only |
| `/opt/touchstone-site` | `www-data` | `0755` | the served site; the status snapshot lands here |

`<network>` is a manifest name: `xlayer-testnet-2` or `xlayer-mainnet`. The units are systemd
templates, so the instance after `@` selects both the manifest and the workspace, and the two
can never disagree.

The shared state root is deliberately outside systemd's `StateDirectory=` ownership model.
That directive has one owning user, while this tree is intentionally used by two. Giving it to
either service identity lets a later start recursively rewrite the ownership of the other
identity's files. `tmpfiles.d` owns only `/var/lib/touchstone`; the tighter per-workspace modes
below preserve the observer/publisher boundary.

---

## 3. Install

```sh
# Two identities and one shared group. The publisher holds keys; the observer never does,
# and running them as one user would let observer-side code read the publisher's process
# environment. Neither account has a login shell, a home, or a password.
groupadd --system touchstone-data
useradd --system --no-create-home --shell /usr/sbin/nologin -g touchstone-data touchstone
useradd --system --no-create-home --shell /usr/sbin/nologin -g touchstone-data touchstone-observer

install -d -o root -g root -m 0755 /opt/touchstone
# Setgid, so files created by either identity stay in the shared group.
install -d -o root -g touchstone-data -m 2775 /var/lib/touchstone
install -d -o root -g root -m 0700 /etc/touchstone

# Code, pinned. A service that tracks a branch is a service whose behaviour changes when
# somebody pushes.
git clone https://github.com/Ridwannurudeen/touchstone.git /opt/touchstone
git -C /opt/touchstone checkout <commit>
python3 -m venv /opt/touchstone/.venv
/opt/touchstone/.venv/bin/pip install /opt/touchstone
# Runtime dependencies only (cryptography, psutil, web3) — pyproject.toml declares
# them; there is no requirements.txt. `psutil` is a RUNTIME dependency, not a test
# one: without it `process_identity` degrades and the heartbeat reports a dead daemon
# on a machine whose daemon is fine.

cp /opt/touchstone/deploy/systemd/touchstone-*.service /etc/systemd/system/
cp /opt/touchstone/deploy/systemd/touchstone-*.timer   /etc/systemd/system/
cp /opt/touchstone/deploy/tmpfiles.d/touchstone.conf /etc/tmpfiles.d/
systemctl daemon-reload
systemd-tmpfiles --create /etc/tmpfiles.d/touchstone.conf
install -d -o root -g touchstone-data -m 2775 /var/lib/touchstone/xlayer-mainnet

# Always confirm. A unit read with CRLF carries a trailing carriage return on every value,
# so ExecStart names a binary that does not exist while looking exactly right.
for f in /etc/systemd/system/touchstone-*; do
  printf '%s %s
' "$(tr -cd '
' < "$f" | wc -c)" "$(basename "$f")"
done   # every count must be 0
```

⚠️ **Install units from the committed blob, not from a Windows working tree.** `.gitattributes`
forces `deploy/**` to LF *in git*, which does not stop a checkout on a machine with
`core.autocrlf=true` from holding CRLF on disk — and a tar of that working tree ships the
carriage returns to the host. This has now happened once, after the rule was added
specifically to prevent it. From the authoring machine, pipe the blob rather than the file:

    git show ":deploy/systemd/touchstone-observer@.service"       | ssh root@<host> 'cat > /etc/systemd/system/touchstone-observer@.service'

### Updating an existing two-user installation

All four observer/publisher units must move together. A start under an old unit containing
`StateDirectory=touchstone` can perform one final recursive ownership rewrite. Stop every
writer first, install and reload all four changed units, and only then repair ownership. No
old-definition start may occur after the repair.

```sh
units='touchstone-observer@xlayer-mainnet touchstone-publisher@xlayer-mainnet touchstone-fobxx-observer@xlayer-mainnet touchstone-fobxx-publisher@xlayer-mainnet'
systemctl stop $units

cp /opt/touchstone/deploy/systemd/touchstone-{observer,publisher,fobxx-observer,fobxx-publisher}@.service /etc/systemd/system/
cp /opt/touchstone/deploy/tmpfiles.d/touchstone.conf /etc/tmpfiles.d/
systemctl daemon-reload
systemd-tmpfiles --create /etc/tmpfiles.d/touchstone.conf
chown root:touchstone-data /var/lib/touchstone/xlayer-mainnet
chmod 2775 /var/lib/touchstone/xlayer-mainnet

NETWORK_ROOT=/var/lib/touchstone/xlayer-mainnet
# Every immediate child is a publisher-written workspace. Discover them from the durable
# namespace instead of maintaining a name list: that covers ustb, fobxx, ustb-policy-*,
# v2-policy-*, ustb-observer-history, release-* and future publisher workspaces.
for W in "$NETWORK_ROOT"/*; do
  [ -d "$W" ] || continue
  chown -R touchstone:touchstone-data "$W"
  chmod 2750 "$W"
  if [ -d "$W/evidence" ]; then
    chown -R touchstone-observer:touchstone-data "$W/evidence"
    find "$W/evidence" -type d -exec chmod 2770 {} +
    [ ! -d "$W/evidence/objects" ] || \
      find "$W/evidence/objects" -type f -exec chmod 0640 {} +
    [ ! -f "$W/evidence/index.jsonl" ] || chmod 0664 "$W/evidence/index.jsonl"
  fi
  for name in observations.jsonl observer.lock; do
    [ ! -e "$W/$name" ] || {
      chown touchstone-observer:touchstone-data "$W/$name"
      chmod 0664 "$W/$name"
    }
  done
done

systemctl start $units
```

The rule is the namespace, not the examples: every directory directly below the network
root is repaired as a publisher workspace first. Observer ownership is then handed back only
for observer paths that actually exist. A new publisher workspace therefore joins the repair
automatically and does not require another edit to this loop.

### Recover a confirmed publication whose transparency append was lost

Use this only when the exact signed bundle and the confirmed publishing transaction are
known, but both `operation.json` and `pending.json` are already gone. `--resolve-only` cannot
repair that state because there is no durable operation left to resolve. Stop the publisher
so the recovery owns the workspace lock, then run the bundle recovery under the publisher
identity:

```sh
systemctl stop touchstone-publisher@xlayer-mainnet
set -a
. /etc/touchstone/xlayer-mainnet.env
set +a
runuser --user touchstone --preserve-environment -- \
  /opt/touchstone/.venv/bin/python /opt/touchstone/scripts/run_service.py \
  --manifest /opt/touchstone/deployments/xlayer-mainnet.json \
  --workspace /var/lib/touchstone/xlayer-mainnet/ustb \
  --asset-key eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e \
  --recover-bundle \
    /var/lib/touchstone/xlayer-mainnet/ustb/bundles/eip155-196-ustb-2026-08-25-10.json \
  --recover-transaction "$CONFIRMED_PUBLICATION_TX"
unset TOUCHSTONE_PUBLISHER_PRIVATE_KEY TOUCHSTONE_SIGNING_SEED
systemctl start touchstone-publisher@xlayer-mainnet
```

Set `CONFIRMED_PUBLICATION_TX` to the lowercase transaction hash from the receipt; do not
guess it and do not resend anything to discover it. The command verifies the bundle, its
manifest-listed reporting key, the transaction destination, sender lineage and calldata,
the canonical confirmed receipt, and the exact report and URI stored by the registry before
it appends one transparency entry. It never prepares, signs or broadcasts a transaction.
It also refuses while another pending journal exists. A second invocation is idempotent only
when the existing entry contains the same signed report and receipt.

The stop/start pair is the required service restart. If `systemctl restart` is run while the
old definitions are still loaded, treat that as the final chown and perform the ownership
repair after it, with all four units stopped. After `daemon-reload`, starts use the new units
and no longer manage `/var/lib/touchstone` ownership.

### 3a. The observer — no secret, install now

```sh
W=/var/lib/touchstone/xlayer-mainnet/ustb
# The root belongs to the publisher. A group-writable root would let the observer create or
# replace the transparency log, the pending journal, incidents and operations — all state the
# publisher trusts — which makes the shared group a hole rather than a boundary.
install -d -o touchstone         -g touchstone-data -m 2750 "$W"
install -d -o touchstone-observer -g touchstone-data -m 2770 "$W/evidence"
# Pre-created and group-writable, so appending needs no write permission on the root.
install -o touchstone-observer -g touchstone-data -m 664 /dev/null "$W/observations.jsonl"
install -o touchstone-observer -g touchstone-data -m 664 /dev/null "$W/observer.lock"
systemctl enable --now touchstone-observer@xlayer-mainnet
systemctl status touchstone-observer@xlayer-mainnet --no-pager
journalctl -u touchstone-observer@xlayer-mainnet -n 20 --no-pager
```

Expect one line per source per pass. The first pass reads `FIRST_OBSERVATION`; the second,
fifteen minutes later, should read `UNCHANGED` unless the issuer moved something.

### 3b. The status snapshot — no secret

```sh
printf 'TOUCHSTONE_REGISTRY_ADDRESS=0xc9d58e4496bF061C3177301Ff02518eBB70AD30d\n' \
  > /etc/touchstone/xlayer-mainnet.status.env
chmod 0644 /etc/touchstone/xlayer-mainnet.status.env
systemctl enable --now touchstone-status@xlayer-mainnet.timer
systemctl start touchstone-status@xlayer-mainnet.service
curl -sI https://touchstone.gudman.xyz/status | head -1
```

### 3c. The publisher — **owner-gated, carries the deviation**

Do not run this step to "complete the install". It puts a key that can sign and publish
reports onto a shared web host. It is a decision, not a step.

```sh
# Root-owned, 0600. The service account must never be able to read this file.
install -m 0600 -o root -g root /dev/null /etc/touchstone/xlayer-mainnet.env
# then write TOUCHSTONE_PUBLISHER_PRIVATE_KEY and TOUCHSTONE_SIGNING_SEED into it

systemctl enable --now touchstone-publisher@xlayer-mainnet
```

Before enabling it, confirm on the host:

- `systemd-analyze security touchstone-publisher@xlayer-mainnet` — read the exposure score;
- `sudo -u touchstone cat /etc/touchstone/xlayer-mainnet.env` — **must** be denied;
- `sudo -u touchstone-observer cat /etc/touchstone/xlayer-mainnet.env` — **must** be denied;
- `sudo -u touchstone-observer sh -c 'echo x > $W/transparency.jsonl'` — **must** be denied,
  and the same for `pending.json`, `incidents.jsonl` and `mkdir operations`. If any succeeds,
  the observer can forge the state the publisher trusts and the split has bought nothing;
- the workspace is not inside `/opt/touchstone-site`, so nginx cannot serve it;
- `journalctl -u touchstone-publisher@xlayer-mainnet` shows no secret in any line.

---

## 3d. How fast the workspace grows, measured rather than assumed

An audit raised the archive cap as a risk on the grounds that a 226 KB NAV response captured
96 times a day would exhaust the 512 MiB backup limit in under a fortnight. That is the right
worst case and it is not what happens, because objects are content-addressed and a capture
whose bytes are unchanged writes no new object.

Measured on the host over six passes (18 captures, 3 sources):

| | |
|---|---|
| Index cost per capture | 437 B |
| Distinct objects created | 3 — one per source; no intra-day churn observed |
| Projected index | ~123 KiB/day at a 900 s interval |
| Projected objects | ~226 KiB/day, assuming all three sources change once daily |
| Archive growth (hex-encoded, so doubled) | ~698 KiB/day |
| 512 MiB cap reached in | ~751 days |

**The whole difference is deduplication, so watch the thing that would break it.** If a source
began embedding a timestamp or a nonce, every capture would be a distinct object and the
runway would collapse from about two years to about twelve days.

That condition is already visible without adding a monitor: the observer would report
`PAYLOAD_CHANGED` — bytes differed, normalized observation did not — on *every* pass for that
source. A run of `PAYLOAD_CHANGED` where `UNCHANGED` is expected is the early warning, and it
appears on `/status`. Sustained `PAYLOAD_CHANGED` means: re-measure this table before trusting
the runway.

---

## 4. What a running service does and does not prove

It proves the process is supervised and restarts. It does **not** prove uptime, reliability or
continuity, and no such claim may be made from an install.

Until a measured window exists, the honest wording is *"the daemon is configured and was last
observed at …"*. Not *"always on"*, not *"continuous"*, not a percentage. Every report
published to date reports `UNVERIFIABLE`, and the consumer gate refuses the asset accordingly;
running a service changes neither of those.

The status page states this itself: an old timestamp on it proves the page was not
regenerated, which is a different failure from the daemon stopping, and the page declines to
guess which happened.

---

## 5. Rollback

```sh
systemctl disable --now touchstone-observer@<network>
systemctl disable --now touchstone-publisher@<network>
systemctl disable --now touchstone-status@<network>.timer
```

Nothing else on the host is touched: no vhost, no nginx reload, no other unit. The workspace
survives on purpose — its evidence is the one artifact here that cannot be recreated, and a
rollback that deleted it would destroy the captures a later epoch confirms against.

To remove the key without removing the service, shred `/etc/touchstone/<network>.env` and stop
the publisher. The observer keeps running; it never needed it.
