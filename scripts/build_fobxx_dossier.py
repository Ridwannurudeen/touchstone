"""Build the FOBXX dossier from retained bundles and the signed approval ledger."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.assets import FOBXX  # noqa: E402
from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.verify import VerificationError, verify_bundle  # noqa: E402

BUNDLES = ROOT / "site2" / "data"
STATS = BUNDLES / "stats.json"
FACTS = ROOT / "site2" / "_data" / "facts.json"
LEDGER = ROOT / "data" / "compilations" / "APPROVALS.json"
OUT = ROOT / "site2" / "_pages" / "assets" / "fobxx.html"

_NETWORKS = {196: "mainnet", 1952: "testnet"}


def _document(path: Path, label: str) -> Mapping[str, object]:
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must contain an object")
    return value


def _retained_reports(
    bundles: Path,
) -> tuple[
    dict[int, tuple[Path, Mapping[str, object], Mapping[str, object]]], dict[int, int]
]:
    reports = {}
    counts = {chain_id: 0 for chain_id in _NETWORKS}
    for path in sorted(bundles.glob("eip155-*-fobxx-*.json")):
        match = re.match(r"eip155-(\d+)-", path.name)
        if match is None:
            continue
        chain_id = int(match.group(1))
        if chain_id not in _NETWORKS:
            continue
        raw = path.read_bytes()
        bundle = strict_json_loads(raw)
        report = verify_bundle(raw)
        if not isinstance(bundle, Mapping):
            raise ValueError(f"{path.name} must contain an object")
        if report.get("asset_key") != FOBXX.asset_key or report.get("policy") is not None:
            raise ValueError(f"{path.name} is not a FOBXX asset report")
        counts[chain_id] += 1
        current = reports.get(chain_id)
        if current is None or (
            str(report.get("observed_at", "")), int(report.get("sequence", 0))
        ) > (
            str(current[2].get("observed_at", "")),
            int(current[2].get("sequence", 0)),
        ):
            reports[chain_id] = (path, bundle, report)
    if set(reports) != set(_NETWORKS):
        raise ValueError("one retained FOBXX report is required for each X Layer network")
    return reports, counts


def _publication(
    stats: Mapping[str, object], report: Mapping[str, object], chain_id: int
) -> Mapping[str, object]:
    publications = stats.get("reports")
    if not isinstance(publications, list):
        raise ValueError("report statistics must contain a reports array")
    note = f"xlayer-{_NETWORKS[chain_id]}-v1"
    matches = [
        item
        for item in publications
        if isinstance(item, Mapping)
        and item.get("asset_key") == FOBXX.asset_key
        and item.get("epoch_id") == report.get("epoch_id")
        and item.get("sequence") == report.get("sequence")
        and item.get("observed_at") == report.get("observed_at")
        and item.get("note") == note
    ]
    if len(matches) != 1:
        raise ValueError(
            f"FOBXX chain {chain_id} has {len(matches)} publication records; expected one"
        )
    return matches[0]


def _control_rows(
    bundle: Mapping[str, object], report: Mapping[str, object]
) -> str:
    records = bundle.get("control_records")
    evaluations = report.get("controls")
    if not isinstance(records, list) or not isinstance(evaluations, list):
        raise ValueError("FOBXX bundle must contain controls and control records")
    evaluated = {
        item.get("control_id"): item.get("evaluation")
        for item in evaluations
        if isinstance(item, Mapping) and isinstance(item.get("evaluation"), Mapping)
    }
    rows = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("FOBXX control record must be an object")
        control_id = record.get("control_id")
        evaluation = evaluated.get(control_id)
        if not isinstance(control_id, str) or not isinstance(evaluation, Mapping):
            raise ValueError("FOBXX control record has no matching evaluation")
        expected = json.dumps(
            record.get("expected_value"), sort_keys=True, separators=(",", ":")
        )
        rows.append(
            f"""        <article class="persona">
          <p class="p-for"><code>{html.escape(control_id)}</code></p>
          <h3>{html.escape(str(record.get("subject", "")))}</h3>
          <p><span class="chip chip-live"><i class="dot" aria-hidden="true"></i>{html.escape(str(evaluation.get("result", "")))}</span></p>
          <dl class="a-facts">
            <div><dt>Observed</dt><dd><code>{html.escape(str(evaluation.get("observed_value", "")))}</code> on {html.escape(str(evaluation.get("observed_on", "")))}</dd></div>
            <div><dt>Expected</dt><dd><code>{html.escape(expected)}</code> via <code>{html.escape(str(record.get("comparison_operator", "")))}</code></dd></div>
            <div><dt>Evidence deadline</dt><dd><code>{html.escape(str(evaluation.get("evidence_deadline", "")))}</code></dd></div>
            <div><dt>Source</dt><dd><code>{html.escape(str(record.get("source_id", "")))}</code> · {html.escape(str(record.get("source_authority_class", "")))}</dd></div>
            <div><dt>Evidence span</dt><dd><code class="wrap-code">{html.escape(str(record.get("evidence_span", "")))}</code></dd></div>
          </dl>
        </article>"""
        )
    return "\n".join(rows)


def _declined_rows(ledger: Mapping[str, object]) -> str:
    declined = ledger.get("declined")
    if not isinstance(declined, list):
        raise ValueError("approval ledger must contain a declined array")
    entries = [
        entry
        for entry in declined
        if isinstance(entry, Mapping)
        and str(entry.get("control_id", "")).startswith("fobxx-")
    ]
    if len(entries) != 2:
        raise ValueError(f"FOBXX must have exactly two declined controls, found {len(entries)}")
    return "\n".join(
        f"""          <tr>
            <td><code>{html.escape(str(entry["control_id"]))}</code></td>
            <td>{html.escape(str(entry.get("reason", "")))}</td>
          </tr>"""
        for entry in entries
    )


def render(
    *, bundles: Path = BUNDLES, stats_path: Path = STATS, facts_path: Path = FACTS,
    ledger_path: Path = LEDGER
) -> str:
    retained, retained_counts = _retained_reports(bundles)
    stats = _document(stats_path, "report statistics")
    facts = _document(facts_path, "site facts")
    ledger = _document(ledger_path, "approval ledger")
    main_path, main_bundle, main_report = retained[196]
    test_path, _, test_report = retained[1952]
    if main_report.get("controls") != test_report.get("controls"):
        raise ValueError("FOBXX network reports do not carry the same control results")
    publications = {
        chain_id: _publication(stats, report, chain_id)
        for chain_id, (_, _, report) in retained.items()
    }
    network_rows = []
    for chain_id, path in ((196, main_path), (1952, test_path)):
        name = _NETWORKS[chain_id]
        network_report = retained[chain_id][2]
        network = facts.get(name)
        if not isinstance(network, Mapping):
            raise ValueError(f"site facts have no {name} network")
        publication = publications[chain_id]
        transaction_hash = str(publication.get("transaction_hash", ""))
        network_rows.append(
            f"""          <tr>
            <td>X Layer {name}</td><td class="mono">{chain_id}</td>
            <td><a href="{html.escape(str(network.get("explorer", "")))}/address/{html.escape(str(network.get("registry", "")))}"><code>{html.escape(str(network.get("registry", "")))}</code></a></td>
            <td class="mono">{network_report.get("sequence")}</td>
            <td><a href="{html.escape(str(network.get("explorer", "")))}/tx/{html.escape(transaction_hash)}">Transaction</a><span class="td-sub">block {publication.get("block")}</span></td>
            <td><a href="/data/{html.escape(path.name)}">Bundle</a></td>
          </tr>"""
        )
    limitations = main_report.get("limitations")
    if not isinstance(limitations, list):
        raise ValueError("FOBXX report must contain limitations")
    limitation_items = "\n".join(
        f"        <li>{html.escape(str(item))}</li>" for item in limitations
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FOBXX dossier &mdash; Touchstone</title>
<meta name="description" content="The retained FOBXX evidence, four satisfied controls, two declined controls, and first X Layer publications.">
<link rel="icon" href="/assets/mark.svg" type="image/svg+xml">
<link rel="canonical" href="https://touchstone.gudman.xyz/assets/fobxx">
<link rel="stylesheet" href="/assets/style.css">
</head>
<body>
{{{{> header}}}}
<main id="main">
  <section class="page-head">
    <div class="wrap">
      <p class="eyebrow">Asset dossier · first publication</p>
      <h1>FOBXX</h1>
      <p class="t-lead prose">Franklin OnChain U.S. Government Money Fund has
      {retained_counts[196]} retained <strong>{html.escape(str(main_report.get("state")))}</strong>
      publication on mainnet and {retained_counts[1952]} retained
      <strong>{html.escape(str(test_report.get("state")))}</strong> publication on testnet.
      The latest rows below are sequence {main_report.get("sequence")} and
      {test_report.get("sequence")}; this page does not imply a daily history.</p>
    </div>
  </section>
  <section class="section">
    <div class="wrap">
      <h2>Report</h2>
      <dl class="a-facts">
        <div><dt>Asset key</dt><dd><code class="wrap-code">{html.escape(FOBXX.asset_key)}</code></dd></div>
        <div><dt>State</dt><dd><span class="chip chip-live"><i class="dot" aria-hidden="true"></i>{html.escape(str(main_report.get("state")))}</span></dd></div>
        <div><dt>Mainnet evidence as of</dt><dd><code>{html.escape(str(main_report.get("observed_at")))}</code></dd></div>
        <div><dt>Fresh through</dt><dd><code>{html.escape(str(main_report.get("valid_until")))}</code></dd></div>
        <div><dt>Control-set root</dt><dd><code class="wrap-code">{html.escape(str(main_report.get("control_set_root")))}</code></dd></div>
        <div><dt>Approval ledger</dt><dd><code class="wrap-code">{html.escape(str(main_report.get("approval_ledger_sha256")))}</code></dd></div>
      </dl>
      <div class="table-scroll">
        <table><caption class="sr">FOBXX publications by network</caption>
          <thead><tr><th>Network</th><th>Chain</th><th>Registry</th><th>Seq</th><th>Proof</th><th>Artifact</th></tr></thead>
          <tbody>
{chr(10).join(network_rows)}
          </tbody>
        </table>
      </div>
    </div>
  </section>
  <section class="section section--sunk">
    <div class="wrap">
      <p class="eyebrow">Approved and evaluated</p>
      <h2>Four controls, four satisfied results</h2>
      <div class="persona-grid">
{_control_rows(main_bundle, main_report)}
      </div>
    </div>
  </section>
  <section class="section">
    <div class="wrap">
      <p class="eyebrow">Refusal is part of the record</p>
      <h2>Two controls were declined</h2>
      <p class="prose">These candidates passed deterministic compilation gates but were not
      approved, evaluated, or published. Their signed ledger reasons are reproduced below.</p>
      <div class="table-scroll"><table>
        <thead><tr><th>Declined control</th><th>Recorded reason</th></tr></thead>
        <tbody>
{_declined_rows(ledger)}
        </tbody>
      </table></div>
    </div>
  </section>
  <section class="section section--sunk">
    <div class="wrap prose">
      <h2>What this report does not prove</h2>
      <ul>
{limitation_items}
      </ul>
    </div>
  </section>
</main>
{{{{> footer}}}}
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", type=Path, default=BUNDLES)
    parser.add_argument("--stats", type=Path, default=STATS)
    parser.add_argument("--facts", type=Path, default=FACTS)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--out", type=Path, default=OUT)
    arguments = parser.parse_args(argv)
    try:
        page = render(
            bundles=arguments.bundles,
            stats_path=arguments.stats,
            facts_path=arguments.facts,
            ledger_path=arguments.ledger,
        )
    except (OSError, TypeError, ValueError, VerificationError) as error:
        print(f"FOBXX DOSSIER FAIL: {error}", file=sys.stderr)
        return 1
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(page, encoding="utf-8")
    print(f"FOBXX dossier rendered -> {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
