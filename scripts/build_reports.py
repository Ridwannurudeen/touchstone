"""Build the scriptless reports feed from retained, verified report bundles."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime
import hashlib
import html
from pathlib import Path
import re
import sys
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.signing import strict_json_loads  # noqa: E402
from touchstone.verify import VerificationError, verify_bundle  # noqa: E402

BUNDLES = ROOT / "site2" / "data"
STATS = BUNDLES / "stats.json"
FACTS = ROOT / "site2" / "_data" / "facts.json"
OUT = ROOT / "site2" / "_pages" / "reports.html"

_TRANSACTION_HASH = re.compile(r"0x[0-9a-f]{64}")
_STATE_CHIPS = {
    "CONFIRMED": "chip-live",
    "STALE": "chip-blocked",
    "INCONSISTENT": "chip-suspended",
    "UNVERIFIABLE": "chip-unverifiable",
}
_NETWORK_NOTES = {
    "xlayer-mainnet-v1": "mainnet",
    "xlayer-testnet-v1": "testnet",
}


class ReportRow(NamedTuple):
    asset: str
    published_at: datetime
    published_text: str
    state: str
    report_hash: str
    bundle_url: str
    transaction_hash: str
    transaction_url: str
    network: str
    chain_id: int
    sequence: int
    correction_of: int | None
    block: int


def _document(path: Path, name: str) -> Mapping[str, object]:
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f"{name} is not strict JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UTC timestamp")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be in UTC")
    return moment


def _policy_id(report: Mapping[str, object]) -> str | None:
    policy = report.get("policy")
    if policy is None:
        return None
    if not isinstance(policy, Mapping) or not isinstance(policy.get("policy_id"), str):
        raise ValueError("verified policy report has no policy_id")
    return policy["policy_id"]


def _publication_key(
    record: Mapping[str, object], *, default_asset_key: str
) -> tuple[object, ...]:
    return (
        record.get("asset_key", default_asset_key),
        record.get("epoch_id"),
        record.get("sequence"),
        record.get("state"),
        record.get("correction_of"),
        record.get("observed_at"),
        record.get("policy"),
    )


def _report_key(report: Mapping[str, object]) -> tuple[object, ...]:
    return (
        str(report["asset_key"]).split("#policy:", 1)[0],
        report["epoch_id"],
        report["sequence"],
        report["state"],
        report["correction_of"],
        report["observed_at"],
        _policy_id(report),
    )


def load_rows(
    *, bundles: Path = BUNDLES, stats: Path = STATS, facts: Path = FACTS
) -> list[ReportRow]:
    stats_document = _document(stats, "report statistics")
    publications = stats_document.get("reports")
    if not isinstance(publications, list) or any(
        not isinstance(record, Mapping) for record in publications
    ):
        raise ValueError("report statistics must contain a reports array")

    facts_document = _document(facts, "site facts")
    asset_facts = facts_document.get("asset")
    if not isinstance(asset_facts, Mapping):
        raise ValueError("site facts must contain asset facts")
    default_asset_key = asset_facts.get("asset_key_text")
    if not isinstance(default_asset_key, str):
        raise ValueError("site asset facts must contain id and asset_key_text")

    assets = stats_document.get("assets")
    if not isinstance(assets, list) or any(
        not isinstance(record, Mapping) for record in assets
    ):
        raise ValueError("report statistics must contain an assets array")
    asset_names: dict[str, str] = {}
    for record in assets:
        asset_key = record.get("canonical_asset_key", record.get("asset_key"))
        ticker = record.get("ticker")
        if isinstance(asset_key, str) and isinstance(ticker, str):
            asset_names[asset_key] = ticker

    networks: dict[str, tuple[int, str]] = {}
    for name in ("mainnet", "testnet"):
        network = facts_document.get(name)
        if not isinstance(network, Mapping):
            raise ValueError(f"site facts must contain {name} facts")
        try:
            chain_id = int(network["chain_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{name} chain_id must be an integer") from error
        explorer = network.get("explorer")
        if not isinstance(explorer, str):
            raise ValueError(f"{name} explorer must be text")
        networks[name] = (chain_id, explorer.rstrip("/"))

    indexed: dict[tuple[object, ...], Mapping[str, object]] = {}
    network_at: dict[str, str] = {}
    for record in publications:
        observed_at = record.get("observed_at")
        note = record.get("note")
        if isinstance(observed_at, str) and note in _NETWORK_NOTES:
            network_at[observed_at] = _NETWORK_NOTES[note]
        key = _publication_key(record, default_asset_key=default_asset_key)
        if key in indexed:
            raise ValueError(f"duplicate report publication metadata for {key!r}")
        indexed[key] = record

    rows: list[ReportRow] = []
    for path in sorted(bundles.glob("*.json")):
        if path.name == stats.name:
            continue
        try:
            raw = path.read_bytes()
            bundle = strict_json_loads(raw)
            report = verify_bundle(raw)
        except (OSError, TypeError, ValueError, VerificationError) as error:
            raise ValueError(
                f"retained bundle {path.name} does not verify: {error}"
            ) from error
        if not isinstance(bundle, Mapping):
            raise ValueError(f"retained bundle {path.name} must be an object")
        publication = indexed.get(_report_key(report))
        if publication is None:
            raise ValueError(f"retained bundle {path.name} has no publication metadata")

        base_asset_key = str(report["asset_key"]).split("#policy:", 1)[0]
        asset = asset_names.get(base_asset_key)
        if asset is None:
            raise ValueError(f"retained bundle {path.name} identifies an unknown asset")
        note = publication.get("note")
        network_name = _NETWORK_NOTES.get(str(note), network_at.get(str(report["observed_at"])))
        if network_name is None:
            raise ValueError(f"retained bundle {path.name} has no network anchor")
        chain_id, explorer = networks[network_name]

        transaction_hash = publication.get("transaction_hash")
        if not isinstance(transaction_hash, str) or not _TRANSACTION_HASH.fullmatch(
            transaction_hash
        ):
            raise ValueError(
                f"publication for {path.name} has an invalid transaction hash"
            )
        block = publication.get("block")
        if type(block) is not int or block < 0:
            raise ValueError(f"publication for {path.name} has an invalid block")
        state = report["state"]
        if state not in _STATE_CHIPS:
            raise ValueError(f"retained bundle {path.name} has an unsupported state")
        sequence = report["sequence"]
        correction = report["correction_of"]
        if type(sequence) is not int or (
            correction is not None and type(correction) is not int
        ):
            raise ValueError(f"retained bundle {path.name} has an invalid sequence")

        published_at = _timestamp(report["observed_at"], f"{path.name} observed_at")
        report_canonical = bundle.get("report_canonical")
        if not isinstance(report_canonical, str):
            raise ValueError(f"retained bundle {path.name} has no canonical report")
        report_hash = hashlib.sha256(report_canonical.encode("utf-8")).hexdigest()
        rows.append(
            ReportRow(
                asset=asset,
                published_at=published_at,
                published_text=published_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                state=str(state),
                report_hash=report_hash,
                bundle_url=f"/data/{path.name}",
                transaction_hash=transaction_hash,
                transaction_url=f"{explorer}/tx/{transaction_hash}",
                network=f"X LAYER {network_name.upper()}",
                chain_id=chain_id,
                sequence=sequence,
                correction_of=correction,
                block=block,
            )
        )
    return sorted(rows, key=lambda row: (row.published_at, row.block), reverse=True)


def _short(value: str) -> str:
    return f"{value[:12]}&hellip;{value[-8:]}"


def _row(row: ReportRow) -> str:
    correction = (
        f'<span class="badge badge-correction">corrects #{row.correction_of}</span>'
        if row.correction_of is not None
        else ""
    )
    transaction = html.escape(row.transaction_hash)
    report_hash = html.escape(row.report_hash)
    return f"""        <tr>
          <td data-label="Asset"><strong>{html.escape(row.asset)}</strong><span class="td-sub mono">{html.escape(row.network)} &middot; CHAIN {row.chain_id} &middot; SEQ {row.sequence}</span></td>
          <td data-label="Published" class="mono reports-numeric"><time datetime="{row.published_at.isoformat().replace("+00:00", "Z")}">{html.escape(row.published_text)}</time></td>
          <td data-label="State"><span class="chip {_STATE_CHIPS[row.state]}"><i class="dot" aria-hidden="true"></i>{html.escape(row.state)}</span>{correction}</td>
          <td data-label="Report hash" class="mono reports-hash"><a href="{html.escape(row.bundle_url)}" title="{report_hash}">{_short(report_hash)}</a></td>
          <td data-label="Transaction" class="mono reports-hash"><a href="{html.escape(row.transaction_url)}" title="{transaction}">{_short(transaction)}</a></td>
        </tr>"""


def render(rows: list[ReportRow]) -> str:
    body = "\n".join(_row(row) for row in rows)
    if not body:
        body = (
            '        <tr class="reports-empty"><td colspan="5">No reports yet</td></tr>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reports &mdash; Touchstone</title>
<meta name="description" content="Touchstone retained reports, newest first, with report hashes and chain-bound publication transactions.">
<link rel="icon" href="/assets/mark.svg" type="image/svg+xml">
<link rel="canonical" href="https://touchstone.gudman.xyz/reports">
<meta property="og:title" content="Reports &mdash; Touchstone">
<meta property="og:description" content="Touchstone retained reports, newest first, with report hashes and chain-bound publication transactions.">
<meta property="og:url" content="https://touchstone.gudman.xyz/reports">
<meta property="og:type" content="website">
<meta property="og:image" content="https://touchstone.gudman.xyz/assets/og-card.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/assets/style.css">
<style>
  .reports-table-wrap {{ overflow: visible; }}
  .reports-table {{ table-layout: fixed; }}
  .reports-table th:nth-child(1) {{ width: 23%; }}
  .reports-table th:nth-child(2) {{ width: 22%; }}
  .reports-table th:nth-child(3) {{ width: 18%; }}
  .reports-table th:nth-child(4),
  .reports-table th:nth-child(5) {{ width: 18.5%; }}
  .reports-table .badge-correction {{ display: block; width: max-content; margin-top: 6px; }}
  .reports-numeric {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .reports-hash {{ font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
  @media (max-width: 639px) {{
    .reports-table, .reports-table tbody, .reports-table tr, .reports-table td {{ display: block; width: 100%; }}
    .reports-table thead {{ display: none; }}
    .reports-table tr {{ padding: 14px 16px; border-bottom: 1px solid var(--rule); }}
    .reports-table tr:last-child {{ border-bottom: 0; }}
    .reports-table td {{ display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; padding: 7px 0; border: 0; }}
    .reports-table td::before {{ content: attr(data-label); font-size: var(--t-micro); font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; color: var(--ink-500); }}
    .reports-table td > * {{ min-width: 0; }}
    .reports-table .reports-empty {{ padding: 0; }}
    .reports-table .reports-empty td {{ display: block; padding: 20px; }}
    .reports-table .reports-empty td::before {{ content: none; }}
    .reports-numeric {{ white-space: normal; }}
  }}
</style>
</head>
<body>
{{{{> header}}}}

<main id="main">
  <section class="page-head">
    <div class="wrap">
      <p class="eyebrow">Transparency log</p>
      <h1>Reports</h1>
    </div>
  </section>

  <section class="section">
    <div class="wrap">
      <div class="table-scroll reports-table-wrap">
        <table class="reports-table">
          <caption class="sr">Retained reports, newest first</caption>
          <thead>
            <tr>
              <th scope="col">Asset</th>
              <th scope="col">Published (UTC)</th>
              <th scope="col">State</th>
              <th scope="col">Report hash</th>
              <th scope="col">Transaction</th>
            </tr>
          </thead>
          <tbody>
{body}
          </tbody>
        </table>
      </div>
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
    parser.add_argument("--out", type=Path, default=OUT)
    arguments = parser.parse_args(argv)

    rows = load_rows(
        bundles=arguments.bundles,
        stats=arguments.stats,
        facts=arguments.facts,
    )
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(render(rows), encoding="utf-8")
    print(f"{len(rows)} retained reports rendered -> {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
