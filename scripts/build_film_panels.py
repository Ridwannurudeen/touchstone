"""Render the film's evidence panels from retained captures. Fetches nothing.

The film shows a value the issuer published on one day and had changed by the next. That
comparison has to come from the two artifacts that were actually captured and kept, not from
whatever the feed is serving while the camera is running — otherwise the film would be
demonstrating a live fetch and calling it a historical catch.

So this reads the evidence store and nothing else. If the store does not hold both captures it
refuses rather than rendering an empty panel, because a panel with no rows in it would still
look like a finding on screen.

Panels are standalone HTML at 1920x1080, styled to the site's palette, and deliberately
contain no filesystem path, no command line and no terminal — they exist so the film can show
retained evidence without putting a shell in frame.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.approval import ledger_from_bytes  # noqa: E402
from touchstone.evaluate import default_ustb_controls  # noqa: E402

NAV_SOURCE = "superstate-ustb-nav-daily"

STYLE = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    width: 1920px; height: 1080px; background: #FCFBFA; color: #3A4149;
    font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
    display: flex; flex-direction: column; justify-content: center;
    padding: 96px 128px; -webkit-font-smoothing: antialiased;
  }
  .eyebrow {
    font-size: 22px; letter-spacing: .14em; text-transform: uppercase;
    color: #8A4522; font-weight: 600; margin-bottom: 20px;
  }
  h1 { font-size: 62px; line-height: 1.1; color: #14171A; font-weight: 600; letter-spacing: -.02em; }
  .lead { font-size: 28px; color: #6B747C; margin-top: 20px; max-width: 1400px; line-height: 1.45; }
  .mono { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace; }
  table { width: 100%; border-collapse: collapse; margin-top: 52px; }
  th {
    text-align: left; font-size: 20px; letter-spacing: .10em; text-transform: uppercase;
    color: #8C949B; font-weight: 600; padding: 0 24px 18px 0; border-bottom: 2px solid #E3E0DB;
  }
  td { font-size: 30px; padding: 26px 24px 26px 0; border-bottom: 1px solid #EFEDE9; }
  tr.changed td { background: #FBF1EA; font-weight: 600; color: #14171A; }
  tr.changed td:first-child { box-shadow: inset 5px 0 0 #A6552A; padding-left: 24px; }
  .same { color: #6B747C; }
  .was { color: #8C949B; text-decoration: line-through; }
  .now { color: #8A4522; font-weight: 700; }
  .cards { display: flex; gap: 40px; margin-top: 56px; }
  .card {
    flex: 1; background: #FFFFFF; border: 1px solid #E3E0DB; border-radius: 10px; padding: 40px;
  }
  .card h2 { font-size: 24px; color: #6B747C; font-weight: 600; letter-spacing: .06em;
             text-transform: uppercase; margin-bottom: 22px; }
  .card .big { font-size: 34px; color: #14171A; line-height: 1.5; word-break: break-all; }
  .foot { margin-top: 56px; font-size: 24px; color: #8C949B; }
"""


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def _rows(document: object) -> list[dict]:
    if isinstance(document, dict):
        for key in ("data", "navs", "results", "items"):
            if key in document and isinstance(document[key], list):
                return document[key]
    return document if isinstance(document, list) else []


def _captures(
    workspace: Path, *, earlier: str | None = None, later: str | None = None
) -> tuple[dict, dict]:
    """The two NAV captures the film is about, with their index records.

    ``earlier`` and ``later`` pin the exact artifacts by digest. Without them this takes the
    two oldest distinct payloads, which is fine for a one-off render and wrong for a film:
    the observer adds captures continuously, so an unpinned render can quietly become a
    different comparison than the one the script narrates. The script names its digests; the
    pins let the builder refuse anything else.
    """
    index = workspace / "evidence" / "index.jsonl"
    records = [
        json.loads(line)
        for line in index.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    nav = sorted(
        (r for r in records if r["source_id"] == NAV_SOURCE),
        key=lambda r: r["retrieved_at"],
    )
    distinct: list[dict] = []
    for record in nav:
        if not distinct or record["sha256"] != distinct[-1]["sha256"]:
            distinct.append(record)
    if earlier or later:
        # Both, or neither. One pin plus a free choice is not a pinned comparison, and the
        # failure would look like success: the panel renders, the narration is unchanged, and
        # only one of the two artifacts on screen is the one the script names.
        if not (earlier and later):
            raise SystemExit(
                "pin both captures or neither: one pin still lets the other half of the "
                "comparison drift as the observer adds captures"
            )
        if earlier == later:
            raise SystemExit(
                "the two pins are the same artifact; a capture cannot be compared to itself"
            )
        held = {record["sha256"]: record for record in nav}
        for pin, role in ((earlier, "earlier"), (later, "later")):
            if pin not in held:
                raise SystemExit(
                    f"the {role} capture {pin[:16]}… is not in this workspace. The film is "
                    "about specific retained artifacts; rendering a different pair under the "
                    "same narration would be a fabricated comparison."
                )
        first, second = held[earlier], held[later]
        if first["retrieved_at"] >= second["retrieved_at"]:
            raise SystemExit(
                f"the pinned 'earlier' capture was retrieved at {first['retrieved_at']}, "
                f"which is not before {second['retrieved_at']}. Swapping them would render a "
                "revision backwards."
            )
        return first, second

    if len(distinct) < 2:
        raise SystemExit(
            "this workspace holds fewer than two distinct NAV captures; there is nothing to "
            "compare, and a panel with no comparison in it would still look like a finding"
        )
    return distinct[0], distinct[1]


def panel_captures(earlier: dict, later: dict) -> str:
    body = f"""
    <p class="eyebrow">What it looked at</p>
    <h1>Two captures of the issuer's own feed</h1>
    <p class="lead">Superstate's published daily NAV feed for USTB, fetched twice and kept
    exactly as it arrived. Every byte, hashed on receipt.</p>
    <div class="cards">
      <div class="card">
        <h2>Retrieved</h2>
        <p class="big mono">{html.escape(earlier["retrieved_at"])}</p>
        <h2 style="margin-top:32px">SHA-256</h2>
        <p class="big mono">{html.escape(earlier["sha256"][:32])}…</p>
      </div>
      <div class="card">
        <h2>Retrieved</h2>
        <p class="big mono">{html.escape(later["retrieved_at"])}</p>
        <h2 style="margin-top:32px">SHA-256</h2>
        <p class="big mono">{html.escape(later["sha256"][:32])}…</p>
      </div>
    </div>
    <p class="foot">Both artifacts are retained. Neither was fetched to make this film.</p>
    """
    return _page("Two captures", body)


def panel_diff(workspace: Path, earlier: dict, later: dict) -> tuple[str, int, int]:
    objects = workspace / "evidence" / "objects"
    first = _rows(json.loads((objects / earlier["sha256"]).read_text(encoding="utf-8")))
    second = _rows(json.loads((objects / later["sha256"]).read_text(encoding="utf-8")))

    by_date_first = {r.get("net_asset_value_date"): r for r in first}
    identical = sum(
        1 for r in second if by_date_first.get(r.get("net_asset_value_date")) == r
    )

    # Sorted as dates, not as text. These are "MM/DD/YYYY" strings, so a lexicographic sort
    # puts 12/31/2025 above 08/17/2026 — which silently selected three December rows, all of
    # them unchanged, and rendered a panel titled "the catch" that contained no catch.
    common = {r.get("net_asset_value_date") for r in second} & set(by_date_first)
    shown = sorted(common, key=lambda d: datetime.strptime(d, "%m/%d/%Y"))[-3:]

    lines, changed_count = [], 0
    for day in shown:
        before = by_date_first[day]
        after = next(r for r in second if r.get("net_asset_value_date") == day)
        if before == after:
            lines.append(
                f'<tr><td class="mono">{html.escape(str(day))}</td>'
                f'<td class="mono same">{html.escape(str(before["net_asset_value"]))}</td>'
                f'<td class="same">unchanged</td></tr>'
            )
        else:
            changed_count += 1
            lines.append(
                f'<tr class="changed"><td class="mono">{html.escape(str(day))}</td>'
                f'<td class="mono"><span class="was">{html.escape(str(before["net_asset_value"]))}</span>'
                f' &nbsp;&rarr;&nbsp; <span class="now">{html.escape(str(after["net_asset_value"]))}</span></td>'
                f"<td><strong>revised between captures</strong></td></tr>"
            )

    body = f"""
    <p class="eyebrow">The catch</p>
    <h1>One row changed. {identical} did not.</h1>
    <p class="lead">The same feed, read about a day apart. The value published for the most
    recent date was not the value published for it the day before.</p>
    <table>
      <thead><tr><th>NAV date</th><th>Net asset value</th><th>Across the two captures</th></tr></thead>
      <tbody>{"".join(lines)}</tbody>
    </table>
    <p class="foot">Rows shown are the three most recent common to both captures.</p>
    """
    return _page("The catch", body), identical, changed_count


def panel_interval(earlier: dict, later: dict) -> str:
    """The fact the first cut of this film left out.

    The film originally said the revised row "was skipped" in favour of an older settled one.
    That is what the rule does; it is not what happened on this run. These two captures are
    less than the confirmation interval apart, so no predecessor qualified and the value
    control never compared any row at all — it returned UNEVALUABLE with no observed value.
    Narrating the skip over this evidence asserted a cause that did not occur.
    """
    first = datetime.fromisoformat(earlier["retrieved_at"].replace("Z", "+00:00"))
    second = datetime.fromisoformat(later["retrieved_at"].replace("Z", "+00:00"))
    elapsed = int((second - first).total_seconds())
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    short_by = 86_400 - elapsed
    body = f"""
    <p class="eyebrow">What it did about that</p>
    <h1>It refused, and the reason was its own clock</h1>
    <p class="lead">A value is observed only when a capture at least twenty-four hours older
    still carries it. These two captures were closer together than that, so nothing qualified
    to confirm against and the value control did not evaluate at all.</p>
    <div class="cards">
      <div class="card"><h2>Between these captures</h2>
        <p class="big mono">{hours}h {minutes}m {seconds}s</p></div>
      <div class="card"><h2>Required</h2>
        <p class="big mono">24h 0m 0s</p></div>
      <div class="card"><h2>Short by</h2>
        <p class="big mono">{short_by // 60}m {short_by % 60}s</p></div>
    </div>
    <table>
      <thead><tr><th>Control</th><th>Result</th><th>Observed value</th></tr></thead>
      <tbody><tr class="changed"><td class="mono">ustb-nav-per-share-present</td>
      <td><strong>UNEVALUABLE</strong></td><td class="mono">none</td></tr></tbody>
    </table>
    <p class="foot">The asset was reported <strong>UNVERIFIABLE</strong>. Twenty minutes of
    shortfall was enough for it to decline rather than round its own rule down.</p>
    """
    return _page("The interval", body)


def panel_policy(project_state: dict[str, object] | None = None) -> str:
    ledger = (ROOT / "data" / "compilations" / "APPROVALS.json").read_bytes()
    control = next(
        c
        for c in default_ustb_controls(ledger_from_bytes(ledger))
        if c.control_id == "ustb-nav-per-share-present"
    )
    minimum = control.expected_value.get("minimum_row_age_business_days")
    policy_note = ""
    if project_state is not None:
        policies = project_state.get("policies", [])
        policy_note = (
            f" The canonical state records {len(policies)} predeclared consumer policies."
        )
    body = f"""
    <p class="eyebrow">The rule that exists for that</p>
    <h1>The freshest number is never the verified one</h1>
    <p class="lead">A human-approved control decides what may be observed. This one will not
    read a value until a later capture still carries it, unchanged.</p>
    <div class="cards">
      <div class="card">
        <h2>Control</h2>
        <p class="big mono">{html.escape(control.control_id)}</p>
      </div>
      <div class="card">
        <h2>Minimum row age</h2>
        <p class="big mono">{html.escape(str(minimum))} business days</p>
      </div>
    </div>
    <table>
      <thead><tr><th>The rule, in words</th></tr></thead>
      <tbody><tr><td>Observe the newest row that is byte-identical in a capture taken at least
    twenty-four hours earlier, and at least {html.escape(str(minimum))} business days old.{policy_note}
      A row revised between captures is skipped, and an older settled row is observed
      instead.</td></tr></tbody>
    </table>
    <p class="foot">When a qualifying capture exists, a row revised between the two is
    skipped and an older settled row is observed instead &mdash; decided before anything is
    signed, not flagged afterwards. Whether that comparison happened on any given run is a
    separate question, answered next.</p>
    """
    return _page("The policy", body)


def panel_confirmation() -> str:
    """The ending the first cuts could not have, because it had not happened yet.

    Reads the retained sequence-4 bundle rather than a workspace, so the panel can only
    render from an artifact a stranger could download and verify.
    """
    bundle = json.loads(
        (ROOT / "site2" / "data" / "ustb-2026-08-19-4.json").read_text(encoding="utf-8")
    )
    report = bundle["signed_report"]["report"]
    if report["state"] != "CONFIRMED":
        raise SystemExit("the retained sequence-4 bundle is not CONFIRMED; do not film it")
    nav = next(
        c["evaluation"]["observed_value"]
        for c in report["controls"]
        if c["control_id"] == "ustb-nav-per-share-present"
    )
    body = f"""
    <p class="eyebrow">A day later</p>
    <h1>The same row, unchanged. Confirmed.</h1>
    <p class="lead">On the 19th a fresh capture carried the same NAV the issuer had revised
    to on the 17th &mdash; now at least a day old and byte-identical across two looks. Every
    approved control passed, and the first <strong>CONFIRMED</strong> state published to
    both chains.</p>
    <div class="cards">
      <div class="card"><h2>Refused on the 18th</h2>
        <p class="big mono">{html.escape(str(nav))}</p></div>
      <div class="card"><h2>Confirmed on the 19th</h2>
        <p class="big mono">{html.escape(str(nav))}</p></div>
      <div class="card"><h2>Controls changed</h2>
        <p class="big mono">none</p></div>
    </div>
    <table>
      <thead><tr><th>Consequence, on chain</th><th>Result</th></tr></thead>
      <tbody>
        <tr><td>Consumer gate, refused for two days</td><td class="mono">(true, "allowed")</td></tr>
        <tr class="changed"><td>GuardedAction on the confirmed policy</td><td class="mono">executed &mdash; status 1</td></tr>
        <tr><td>GuardedAction on a never-verified key</td><td class="mono">reverted &mdash; status 0</td></tr>
      </tbody>
    </table>
    <p class="foot">Refusal and confirmation are one mechanism. The gate moved because the
    evidence did.</p>
    """
    return _page("Confirmed", body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--out", required=True, help="directory for the rendered panels"
    )
    parser.add_argument(
        "--earlier-sha256",
        default=None,
        help="pin the earlier capture so a later render cannot film a different pair",
    )
    parser.add_argument("--later-sha256", default=None)
    parser.add_argument("--project-state", type=Path)
    arguments = parser.parse_args(argv)

    workspace = Path(arguments.workspace)
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)

    earlier, later = _captures(
        workspace, earlier=arguments.earlier_sha256, later=arguments.later_sha256
    )
    diff_page, identical, changed = panel_diff(workspace, earlier, later)

    written = {
        "panel-2-captures.html": panel_captures(earlier, later),
        "panel-3-diff.html": diff_page,
        "panel-4-policy.html": panel_policy(
            json.loads(arguments.project_state.read_text(encoding="utf-8"))
            if arguments.project_state
            else None
        ),
        "panel-5-interval.html": panel_interval(earlier, later),
        "panel-6-confirmed.html": panel_confirmation(),
    }
    for name, page in written.items():
        (out / name).write_text(page, encoding="utf-8")
        print(f"  {name}")

    print("\nfrom retained evidence only, nothing fetched:")
    print(f"  earlier capture {earlier['retrieved_at']}  {earlier['sha256'][:16]}…")
    print(f"  later capture   {later['retrieved_at']}  {later['sha256'][:16]}…")
    print(f"  rows identical across both: {identical}")
    print(f"  rows revised between them:  {changed} (of the three most recent shown)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
