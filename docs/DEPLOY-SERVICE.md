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
| Blast radius of the frequent process | The **observer holds no key at all** and cannot publish | The daily publisher still does |
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
| `/var/lib/touchstone/<network>/ustb` | `touchstone:touchstone-data` | `2770` | workspace: evidence, logs, heartbeat. Setgid so both identities' files inherit the shared group; both units set `UMask=0002` |
| `/etc/touchstone/<network>.env` | `root` | `0600` | publisher key, signing seed |
| `/etc/touchstone/<network>.status.env` | `root` | `0644` | registry address; public values only |
| `/opt/touchstone-site` | `www-data` | `0755` | the served site; the status snapshot lands here |

`<network>` is a manifest name: `xlayer-testnet-2` or `xlayer-mainnet`. The units are systemd
templates, so the instance after `@` selects both the manifest and the workspace, and the two
can never disagree.

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
install -d -o touchstone -g touchstone-data -m 2770 /var/lib/touchstone
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
systemctl daemon-reload
```

### 3a. The observer — no secret, install now

```sh
install -d -o touchstone -g touchstone-data -m 2770 /var/lib/touchstone/xlayer-mainnet/ustb
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
- the workspace is not inside `/opt/touchstone-site`, so nginx cannot serve it;
- `journalctl -u touchstone-publisher@xlayer-mainnet` shows no secret in any line.

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
