"""Render a public status snapshot from the watcher's log and the daemon's heartbeat.

The site is static and carries no JavaScript, which constrains what a status page is allowed
to say. **A static page cannot know when it is being read.** So it never says "checked N
seconds ago": that number would be computed when the file was written and then served,
unchanged and increasingly wrong, for as long as the file survives. Every time here is
absolute, in UTC, and the page states when it was generated so a reader can do the subtraction
against their own clock rather than trusting ours.

It also refuses the other easy lie. A green badge would be the daemon's own verdict about
itself, and a process that has stopped writing cannot notice that it has stopped. The daemon's
liveness is decided at generation time by `heartbeat.verify`, which compares the record's
declared expiry against the clock rather than reading a stored answer — and the page says
plainly that an old snapshot is not evidence of a dead daemon, only of a snapshot that was not
regenerated. Those are different failures and only one of them is about the publisher.

Nothing here reads a key or touches a chain. It renders a file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone import heartbeat, observation  # noqa: E402
from touchstone.assets import USTB  # noqa: E402
from touchstone.workspace import Workspace  # noqa: E402

TEMPLATE = ROOT / "site2" / "_docs-template.html"
PARTIALS = ROOT / "site2" / "_partials"
FACTS = ROOT / "site2" / "_data" / "facts.json"


def _chrome(page: str) -> str:
    """Render the shared header/footer and fact tokens the template now carries.

    Deliberately re-implemented from the committed files rather than importing the site
    builder: that builder recomputes derived facts through a subprocess, and this script
    runs unattended on the host every five minutes — a page of status must not gain a
    dependency that can fail for reasons unrelated to status.
    """
    for path in sorted(PARTIALS.glob("*.html")):
        page = page.replace(
            "{{> " + path.stem + "}}", path.read_text(encoding="utf-8").rstrip("\n")
        )
    flat: dict[str, str] = {}

    def flatten(value: object, prefix: str) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                flatten(inner, f"{prefix}{key}.")
        else:
            flat[prefix.rstrip(".")] = str(value)

    flatten(json.loads(FACTS.read_text(encoding="utf-8")), "")
    for key, value in flat.items():
        page = page.replace("{{fact:" + key + "}}", value)
    if "{{" in page:
        offset = page.index("{{")
        raise SystemExit(
            f"status template holds an unrendered token near {page[offset : offset + 50]!r}"
        )
    return page


# What each transition means, in the reader's terms rather than the enum's. `PAYLOAD_CHANGED`
# gets the longest gloss because it is the one a reader is most likely to over-read: the bytes
# moved and the substance did not, which is not the issuer changing a number.
MEANING = {
    "FIRST_OBSERVATION": "first look at this source; nothing to compare against yet",
    "UNCHANGED": "byte-for-byte identical to the previous look",
    "PAYLOAD_CHANGED": (
        "the response bytes differed, but the normalized observation did not — a "
        "re-serialisation or reordering, not a change in what the issuer published"
    ),
    "OBSERVATION_CHANGED": "the normalized observation itself differed",
    "UNCOMPARABLE": (
        "the response bytes differed and there was no earlier normalized form to compare "
        "them against, so whether the substance changed is unknown"
    ),
    "SOURCE_UNAVAILABLE": "the source did not answer; recorded as silence, not as an observation",
    "PARSE_FAILED": "an artifact arrived and the normalizer refused it",
}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(record: dict[str, object]) -> str:
    transition = str(record.get("transition", ""))
    digest = str(record.get("payload_sha256") or "")
    detail = record.get("detail")
    return (
        "<tr>"
        f"<td><code>{html.escape(str(record.get('source_id', '')))}</code></td>"
        f"<td><code>{html.escape(str(record.get('observed_at', '')))}</code></td>"
        f"<td><strong>{html.escape(transition)}</strong>"
        f'<span class="td-sub">{html.escape(MEANING.get(transition, ""))}</span></td>'
        f"<td><code>{html.escape(digest[:16])}{'…' if digest else ''}</code>"
        + (f'<span class="td-sub">{html.escape(str(detail))}</span>' if detail else "")
        + "</td>"
        "</tr>"
    )


def render(
    workspace: Workspace,
    *,
    now: datetime,
    registry_address: str,
    project_state: dict[str, object] | None = None,
) -> str:
    latest = observation.latest_by_source(workspace.root / "observations.jsonl")
    total = len(observation.read_all(workspace.root / "observations.jsonl"))
    health = heartbeat.verify(
        workspace.root / "heartbeat.json",
        now=now,
        asset_key=USTB.asset_key,
        registry_address=registry_address,
    )

    rows = "\n".join(
        _row(latest[manifest.source_id])
        for manifest in USTB.sources
        if manifest.source_id in latest
    )
    if not rows:
        rows = '<tr><td colspan="4">No observation has been recorded yet.</td></tr>'

    record = health.record or {}
    beat = (
        f"<p>The publishing daemon last wrote a heartbeat at "
        f"<code>{html.escape(str(record.get('written_at', 'never')))}</code>, declaring it "
        f"valid until <code>{html.escape(str(record.get('expires_at', 'n/a')))}</code>.</p>"
        if record
        else "<p>No heartbeat has been written by the publishing daemon.</p>"
    )
    verdict = (
        "Within its declared window at the moment this page was generated."
        if health.daemon_alive
        else "Outside its declared window at the moment this page was generated, or absent."
    )
    reasons = (
        "<ul>" + "".join(f"<li>{html.escape(r)}</li>" for r in health.reasons) + "</ul>"
        if health.reasons
        else ""
    )

    reports = project_state.get("reports", {}) if project_state else {}
    latest_state = reports.get("latest_state", "UNVERIFIABLE")
    body = f"""<h1>Status</h1>
<p class="t-lead">What was last observed, and when. <strong>This page is a static
snapshot generated at <code>{_stamp(now)}</code>.</strong> It cannot know when you are
reading it, so every time below is absolute and in UTC; subtract against your own clock.</p>

<h2 id="watching">The sources</h2>
<p>{total} observations recorded. The watcher fetches each source, stores the exact
response bytes, and records what changed. It signs nothing and publishes nothing.</p>
<div class="table-scroll">
<table>
<thead><tr><th>Source</th><th>Last observed (UTC)</th><th>Result</th><th>Artifact</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>

<h2 id="daemon">The publishing daemon</h2>
{beat}
<p><strong>{html.escape(verdict)}</strong></p>
{reasons}
<p>That verdict is computed here, at generation time, by comparing the heartbeat's own
declared expiry against the clock. It is not a status the daemon stored about itself: a
process that has stopped running cannot write down that it stopped.</p>

<h2 id="window">The measured window</h2>
<p>Publication history is derived from the transparency logs and the operations journal,
never from memory: the committed snapshot at
<code>docs/OPERATIONS-METRICS-2026-08-19.json</code> records completed, missed and corrected
slots for the measured window it names. <strong>No uptime percentage is claimed</strong> —
every publication so far was operator-initiated, and a claim of continuity would exceed the
evidence. The observer's capture cadence above is the only continuously scheduled process.</p>

<h2 id="reading">How to read a stale page</h2>
<p><strong>An old timestamp above is not proof that the daemon is down.</strong> It proves
that this page was not regenerated. Those are different failures: one is about the publisher,
the other about whatever refreshes this file. Neither is evidence for the other, and this page
will not guess which one happened.</p>
<p>Nothing on this page asserts that an asset is verified. <strong>As of the generation
time above</strong>, the canonical artifact record's latest report was
<code>{html.escape(str(latest_state))}</code>, and the
consumer gate on X&nbsp;Layer <em>testnet</em> refused the asset accordingly. That is a
statement about this snapshot, not a standing guarantee: a later report can reach a different
state while this file is still being served, so check
<a href="/coverage">Coverage</a> and <a href="/verify">Verify</a> rather than treating this
sentence as current.</p>
<p class="secondary">The observations above are captured against the workspace named in this
service's configuration; the gate result is a separate fact about the testnet consumer
contract. Neither implies the other.</p>
"""
    page = _chrome(TEMPLATE.read_text(encoding="utf-8"))
    page = page.replace("<!--DOC_TITLE-->", "Status")
    page = page.replace("<!--DOC_BODY-->", body)
    return page.replace(
        "<!--DOC_NAV-->",
        '      <ol class="doc-toc-list">'
        '<li class="toc-2"><a href="#watching">The sources</a></li>'
        '<li class="toc-2"><a href="#daemon">The publishing daemon</a></li>'
        '<li class="toc-2"><a href="#window">The measured window</a></li><li class="toc-2"><a href="#reading">How to read a stale page</a></li>'
        "</ol>",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", required=True, help="where to write status.html")
    parser.add_argument(
        "--registry-address",
        required=True,
        help="the registry this workspace publishes to; the heartbeat is checked against it",
    )
    parser.add_argument("--project-state", type=Path)
    arguments = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    page = render(
        Workspace(arguments.workspace),
        now=now,
        registry_address=arguments.registry_address,
        project_state=(
            json.loads(arguments.project_state.read_text(encoding="utf-8"))
            if arguments.project_state
            else None
        ),
    )
    target = Path(arguments.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    print(f"status snapshot generated {_stamp(now)} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
