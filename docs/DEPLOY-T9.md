# Deploying the dossier site

`touchstone.gudman.xyz`, served from `/opt/touchstone-site` on the shared host
`75.119.153.252`. Static, scriptless, no backend, no build step.

**The host is shared with roughly 37 other live vhosts.** Nothing here stops, reloads or edits
any of them. The only shared-state change is one `systemctl reload nginx`, and it happens after
`nginx -t` passes — a failed test leaves the running configuration untouched, because nginx
keeps serving the old one until a reload it never receives.

## Preconditions, verified before the first deploy

| Fact | Value | How checked |
|---|---|---|
| DNS | `touchstone.gudman.xyz` → `75.119.153.252` | `nslookup`; already resolved, no record was created |
| vhost absent | 0 matches for `touchstone` | `nginx -T \| grep -c touchstone` |
| certbot | 5.7.0 | `certbot --version` |
| ACME path | `/etc/nginx/snippets/acme-challenge.conf` → `/var/www/html` | read from the host |
| Sibling pattern | `/opt/<name>-site`, `sites-enabled/<fqdn>.conf` | `aquifer.gudman.xyz` |

## Order, and why it is this order

The vhost names `/etc/letsencrypt/live/touchstone.gudman.xyz/fullchain.pem`, which does not
exist until certbot issues it. Installing the full vhost first makes `nginx -t` fail on a
missing file, which is a confusing way to discover you have not asked for a certificate yet.
So the certificate comes first, over the HTTP-01 webroot that every other site here already
uses, and the vhost is installed only once the file it references exists.

1. **Upload.** `rsync` the tree to `/opt/touchstone-site`. Serving nothing yet — no vhost
   points at it.
2. **Certificate.** `certbot certonly --webroot -w /var/www/html -d touchstone.gudman.xyz`.
   The webroot is already reachable: the ACME snippet is included by every vhost on the host,
   and the challenge is served over plain HTTP before any redirect applies.
3. **Install the vhost.** Copy `deploy/nginx/touchstone.gudman.xyz.conf` to
   `/etc/nginx/sites-available/` and symlink into `sites-enabled/`.
4. **Test.** `nginx -t`. **If it fails, remove the symlink and stop.** Nothing has changed for
   any other site: the running configuration is whatever was loaded last.
5. **Reload.** `systemctl reload nginx` — reload, not restart, so existing connections to the
   other vhosts survive.
6. **Verify from outside**, not from the box: the six routes return 200, the bundle's served
   sha256 equals the digest printed on `/verify`, and the security headers are present on a
   page, a font and the bundle — the three response paths, because a header set in a `location`
   replaces the inherited set rather than adding to it.

## Content updates after the first deploy

The vhost, certificate and nginx configuration are already in place, so a content update is
steps 1 and 6 only — nothing shared is touched and no reload is needed.

**There is no `rsync` on the authoring machine.** The upload is therefore a tar stream over
ssh, which is equivalent here only because the file sets were compared first:

    cd site2 && tar -czf - --exclude=_docs-template.html .       | ssh root@75.119.153.252 'tar -xzf - -C /opt/touchstone-site'

Extract-over-top does not remove files that have been deleted locally, which `rsync --delete`
would. **So compare the two file lists before uploading** and delete any orphan explicitly.

**Synchronize the publisher's append-only record before building or uploading content.** The
served archive is a projection of the publisher workspaces, not a second publication ledger.
Fetch only transparency logs and bundle artifacts with read-only `scp`, then let the sync tool
verify every log and bundle, check each new receipt through both configured RPC providers, retain
the bundle bytes unchanged, and derive the publication rows and public counts:

    sync_dir=$(mktemp -d)
    mkdir -p "$sync_dir"/{ustb,ustb-policy-disclosure-freshness-v1,ustb-policy-nav-settlement-v1}
    scp -r root@75.119.153.252:/var/lib/touchstone/xlayer-mainnet/ustb/bundles "$sync_dir/ustb/"
    scp root@75.119.153.252:/var/lib/touchstone/xlayer-mainnet/ustb/transparency.jsonl "$sync_dir/ustb/"
    scp root@75.119.153.252:/var/lib/touchstone/xlayer-mainnet/ustb-policy-disclosure-freshness-v1/transparency.jsonl "$sync_dir/ustb-policy-disclosure-freshness-v1/"
    scp root@75.119.153.252:/var/lib/touchstone/xlayer-mainnet/ustb-policy-nav-settlement-v1/transparency.jsonl "$sync_dir/ustb-policy-nav-settlement-v1/"
    python scripts/sync_publications.py --from "$sync_dir"
    python scripts/sync_publications.py --check
    python scripts/build_site.py
    rm -rf "$sync_dir"

The main USTB workspace's `bundles/` directory holds the asset and both policy bundles; the two
policy workspaces hold their separate transparency logs and no separate bundle directories.

The synchronizer is append-only: an existing publication row is never altered and an existing
retained filename is never overwritten. A verified retained bundle for the same signed report is
kept as-is; a bundle that fails offline verification is never retained. Do not edit
`site2/data/stats.json` publication rows or their count surfaces by hand.
If another publisher workspace gains bundles, fetch its `transparency.jsonl` and `bundles/` into
the same temporary tree before running the sync.

⚠️ **The host keeps a second copy of three site inputs, and the status timer reads that
copy, not the served tree.** `touchstone-status@xlayer-mainnet.service` runs
`/opt/touchstone/scripts/build_status.py` with `WorkingDirectory=/opt/touchstone`, so it
renders `/status` from `/opt/touchstone/site2/_data/facts.json` and
`/opt/touchstone/site2/_partials/*.html` — the *code* checkout, which `/opt/touchstone` holds
as a plain tar-deployed directory, not a git checkout. Uploading `site2/` updates only
`/opt/touchstone-site`. On 2026-08-21 this left the homepage at 20 reports while `/status`
still said 17, from the same repository. Whenever `facts.json`, a partial, or
`scripts/build_status.py` changes, sync those files into the code checkout as part of the
content update, back up first, then regenerate and require `Result=success`:

    ts=$(date -u +%Y%m%dT%H%M%SZ)
    for f in site2/_data/facts.json site2/_partials/header.html site2/_partials/footer.html scripts/build_status.py; do
      cp -a /opt/touchstone/$f /opt/touchstone/$f.bak-$ts
      install -m 0644 /opt/touchstone-site/${f#site2/} /opt/touchstone/$f 2>/dev/null || install -m 0644 <local copy> /opt/touchstone/$f
    done
    systemctl start touchstone-status@xlayer-mainnet.service
    systemctl show touchstone-status@xlayer-mainnet.service -p Result   # must be Result=success

`scripts/build_status.py` is not in the served tree, so it is shipped from the local checkout
directly. Then confirm from outside that `/status` and `/` show the same counts and the same
header before calling the content update complete.
Sort both with `LC_ALL=C` — Git Bash and the host disagree on collation otherwise, and `comm`
will report differences that do not exist.

⚠️ **`status.html` is generated on the host, not shipped in `site2/`.** It is owned
`touchstone-observer:www-data` so the status timer can replace it in place. A tar created on
Windows can also carry `0777` directory and `0666` file modes; extracting it as root applies
those modes and changes the served tree to `root:root`. Normalize the complete static tree
after every upload, then restore the status file's distinct owner:

    find /opt/touchstone-site -xdev -type d \
      -exec chown www-data:www-data {} + -exec chmod 0755 {} +
    find /opt/touchstone-site -xdev -type f ! -name status.html \
      -exec chown www-data:www-data {} + -exec chmod 0644 {} +
    chown touchstone-observer:www-data /opt/touchstone-site/status.html
    chmod 0644 /opt/touchstone-site/status.html

Confirm the write path with `systemctl start touchstone-status@<network>.service` and require
`Result=success` before calling the content update complete.

Take a snapshot first: `cp -a /opt/touchstone-site /opt/touchstone-site.bak-$(date -u +%Y%m%dT%H%M%SZ)`.
Rolling a content update back is restoring that directory, not removing the vhost.

## What to check after any later epoch

A new epoch adds publication rows and bundles; it does not change the vhost. Run the publication
sync above, rebuild, then re-run steps 1 and 6.
The bundle's digest is printed beside its own download link, so **step 6's hash comparison is
the check that matters** — if it disagrees, the file was translated in transit or in git, and
the page is telling a reader to verify against a number that no longer describes the file.

`site/data/**` is `-text` in `.gitattributes` for exactly this reason. Before that rule the
working tree held the bundle as CRLF while the committed blob held LF: a deploy from a fresh
clone would have served a file contradicting the digest on the page inviting you to check it.

## Rollback

Remove the symlink from `sites-enabled/`, `nginx -t`, reload. The site disappears; nothing else
is touched. The certificate can stay — an unused certificate costs nothing and re-issuing later
would consume rate limit for no reason.
