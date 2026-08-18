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
