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

A new epoch adds one bundle and one page; it does not change the vhost. Re-run steps 1 and 6.
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
