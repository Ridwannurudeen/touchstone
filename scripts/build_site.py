"""Render the site's pages from sources, partials and one set of canonical facts.

Four consecutive external audits found public pages carrying facts the tree had moved past
— counts, addresses, deployment states — because every page carried its own hand-typed copy.
The truth gate catches phrases it has been taught; it cannot catch a number nobody taught it.
So the pages stop carrying copies: a page source names the fact it means (`{{fact:key}}`),
the shared chrome lives in one partial (`{{> header}}`), and this script renders the pages
the server actually serves. Editing a rendered file is futile by design — the next build
overwrites it, and the banner comment at the top of each says so.

Facts come from two places, deliberately:

* `site2/_data/facts.json` — chain facts (addresses, transaction hashes, sequences, blocks).
  These are committed and reviewed, because the build machine cannot be assumed to reach a
  chain, and a build that silently rendered whatever an RPC answered would let one flaky
  endpoint rewrite the site.
* the canonical project state (`scripts/build_project_state.py`) — facts derivable from the
  tree itself (ledger digest, approval counts, bundle counts). Rendering these from the same
  artifact CI asserts against means a page cannot disagree with the repository.

The build refuses an unknown fact, an unused override, and any `{{` left in rendered output.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import re
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site2"
PAGES = SITE / "_pages"
PARTIALS = SITE / "_partials"
FACTS = SITE / "_data" / "facts.json"
STATS = SITE / "data" / "stats.json"
LIVE_STATUS = "https://touchstone.gudman.xyz/status"

_TOKEN = re.compile(r"\{\{\s*(>\s*([a-z0-9-]+)|fact:([a-z0-9_.-]+))\s*\}\}")

BANNER = (
    "<!-- Generated from site2/_pages/{source} by scripts/build_site.py. Do not edit this "
    "file: edit the source, then rebuild. Facts render from site2/_data/facts.json and the "
    "canonical project state. -->\n"
)


def derived_facts() -> dict[str, object]:
    """Facts recomputed from the tree, via the same artifact CI asserts against."""
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "state.json"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_project_state.py"),
                "--out",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        state = json.loads(out.read_text(encoding="utf-8"))
    return {
        "ledger_sha256": state["approval"]["ledger_sha256"],
        "approved_count": state["approval"]["approved_count"],
        "declined_count": state["approval"]["declined_count"],
        "bundle_count": state["reports"]["artifact_count"],
        "latest_state": state["reports"]["latest_state"],
    }


def _committed_facts() -> dict[str, object]:
    value = json.loads(FACTS.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("facts.json must contain an object")
    return value


def _stats() -> dict[str, object]:
    value = json.loads(STATS.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("stats.json must contain an object")
    return value


def _bundles() -> list[tuple[Path, dict[str, object]]]:
    bundles = []
    for path in sorted((SITE / "data").glob("*.json")):
        if path == STATS:
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SystemExit(f"{path}: bundle must contain an object")
        bundles.append((path, value))
    return bundles


def _report(bundle: dict[str, object], path: Path) -> dict[str, object]:
    signed = bundle.get("signed_report")
    report = signed.get("report") if isinstance(signed, dict) else None
    if not isinstance(report, dict):
        raise SystemExit(f"{path}: signed_report.report is unavailable")
    return report


def _gate_sentence(stats: dict[str, object]) -> str:
    gate_result = stats.get("gate_result")
    if isinstance(gate_result, dict) and gate_result.get("allowed") is True:
        return "A configured admission contract may permit USTB right now."
    reason = gate_result.get("reason") if isinstance(gate_result, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        reason = "the gate result is not available"
    return (
        "A configured admission contract may refuse USTB right now because "
        f"{reason.rstrip('.')}."
    )


def _state_class(state: object) -> str:
    return {
        "CONFIRMED": "chip-live",
        "STALE": "chip-blocked",
        "INCONSISTENT": "chip-suspended",
        "UNVERIFIABLE": "chip-unverifiable",
    }.get(str(state), "chip-unverifiable")


def _current_source_ids(bundle: dict[str, object], path: Path) -> set[str]:
    evidence = bundle.get("evidence_digests")
    if not isinstance(evidence, list):
        raise SystemExit(f"{path}: evidence digests are unavailable")
    return {
        str(item["source_id"])
        for item in evidence
        if isinstance(item, dict)
        and item.get("capture_role") == "current"
        and isinstance(item.get("source_id"), str)
    }


def site_facts() -> dict[str, str]:
    committed = _committed_facts()
    stats = _stats()
    base_by_chain: dict[int, dict[str, tuple[Path, dict[str, object], dict[str, object]]]] = {}
    for path, bundle in _bundles():
        match = re.match(r"eip155-(\d+)-", path.name)
        if match is None:
            continue
        report = _report(bundle, path)
        if report.get("policy") is not None:
            continue
        asset_key = report.get("asset_key")
        if not isinstance(asset_key, str):
            raise SystemExit(f"{path}: report asset key is unavailable")
        chain_id = int(match.group(1))
        current = base_by_chain.setdefault(chain_id, {}).get(asset_key)
        candidate = (path, bundle, report)
        if current is None or (
            str(report.get("observed_at", "")), int(report.get("sequence", 0))
        ) > (
            str(current[2].get("observed_at", "")),
            int(current[2].get("sequence", 0)),
        ):
            base_by_chain[chain_id][asset_key] = candidate

    assets = stats.get("assets")
    if not isinstance(assets, list):
        raise SystemExit("stats.json asset collection is unavailable")
    asset_keys = {
        item.get("ticker"): item.get("canonical_asset_key", item.get("asset_key"))
        for item in assets
        if isinstance(item, dict)
    }
    try:
        ustb_key = str(asset_keys["USTB"])
        fobxx_key = str(asset_keys["FOBXX"])
        ustb_path, ustb_bundle, ustb_report = base_by_chain[196][ustb_key]
        fobxx_path, fobxx_bundle, fobxx_report = base_by_chain[196][fobxx_key]
    except KeyError as error:
        raise SystemExit(f"no retained mainnet report is available for {error.args[0]}") from error

    controls = ustb_report.get("controls")
    if not isinstance(controls, list):
        raise SystemExit(f"{ustb_path}: report controls are unavailable")
    nav = next(
        (
            control.get("evaluation")
            for control in controls
            if isinstance(control, dict)
            and control.get("control_id") == "ustb-nav-per-share-present"
            and isinstance(control.get("evaluation"), dict)
        ),
        None,
    )
    if not isinstance(nav, dict):
        raise SystemExit(f"{ustb_path}: USTB NAV control is unavailable")
    ustb_sources = _current_source_ids(ustb_bundle, ustb_path)
    fobxx_sources = _current_source_ids(fobxx_bundle, fobxx_path)
    networks = stats.get("networks_live")
    reports = stats.get("reports")
    if not isinstance(networks, list) or not isinstance(reports, list):
        raise SystemExit("stats.json homepage collections are unavailable")
    confirmed = sum(
        isinstance(item, dict) and item.get("state") == "CONFIRMED" for item in reports
    )
    fobxx_publications = {
        network: sum(
            isinstance(item, dict)
            and item.get("asset_key") == fobxx_key
            and item.get("policy") is None
            and item.get("note") == f"xlayer-{network}-v1"
            for item in reports
        )
        for network in ("mainnet", "testnet")
    }
    live_assets = {
        asset_key
        for asset_key, entry in base_by_chain.get(196, {}).items()
        if entry[2].get("state") == "CONFIRMED"
        and base_by_chain.get(1952, {}).get(asset_key, ({}, {}, {}))[2].get("state")
        == "CONFIRMED"
    }
    if stats.get("assets_live") != len(live_assets):
        raise SystemExit(
            f"stats.json assets_live is {stats.get('assets_live')!r}; retained bundles "
            f"derive {len(live_assets)}"
        )
    counts = committed.get("counts")
    if not isinstance(counts, dict):
        raise SystemExit("facts.json homepage counts are unavailable")
    for key, expected in (
        ("reports_published", len(reports)),
        ("confirmed_reports", confirmed),
    ):
        if str(counts.get(key)) != str(expected):
            raise SystemExit(
                f"facts.json {key} is {counts.get(key)!r}; stats.json derives {expected}"
            )
    return {
        "homepage.live_assets": str(len(live_assets)),
        "homepage.evidence_sources": str(len(ustb_sources | fobxx_sources)),
        "homepage.networks": str(len(networks)),
        "homepage.confirmed_reports": str(confirmed),
        "homepage.ustb.state": str(ustb_report.get("state", "not available")),
        "homepage.ustb.state_class": _state_class(ustb_report.get("state")),
        "homepage.ustb.nav": str(nav.get("observed_value", "not available")),
        "homepage.ustb.nav_date": str(nav.get("observed_on", "not available")),
        "homepage.ustb.evidence_as_of": str(
            ustb_report.get("observed_at", "not available")
        ),
        "homepage.ustb.valid_until": str(
            ustb_report.get("valid_until", "not available")
        ),
        "homepage.ustb.source_count": str(len(ustb_sources)),
        "homepage.ustb.control_count": str(len(controls)),
        "homepage.ustb.gate_sentence": _gate_sentence(stats),
        "homepage.fobxx.state": str(fobxx_report.get("state", "not available")),
        "homepage.fobxx.state_class": _state_class(fobxx_report.get("state")),
        "homepage.fobxx.evidence_as_of": str(
            fobxx_report.get("observed_at", "not available")
        ),
        "homepage.fobxx.valid_until": str(
            fobxx_report.get("valid_until", "not available")
        ),
        "homepage.fobxx.source_count": str(len(fobxx_sources)),
        "homepage.fobxx.control_count": str(len(fobxx_report.get("controls", []))),
        "homepage.fobxx.history_summary": (
            f"{fobxx_publications['mainnet']} publication on mainnet and "
            f"{fobxx_publications['testnet']} publication on testnet"
        ),
    }


def load_facts() -> dict[str, str]:
    committed = _committed_facts()
    flat: dict[str, str] = {}

    def flatten(value: object, prefix: str) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                flatten(inner, f"{prefix}{key}." if prefix else f"{key}.")
        else:
            flat[prefix.rstrip(".")] = str(value)

    flatten(committed, "")
    for key, value in derived_facts().items():
        derived_key = f"derived.{key}"
        if derived_key in flat:
            raise SystemExit(f"facts.json overrides derived fact {derived_key}")
        flat[derived_key] = str(value)
    for key, value in site_facts().items():
        if key in flat:
            raise SystemExit(f"facts.json overrides site-derived fact {key}")
        flat[key] = value
    return flat


class _HomepageFactsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, list[str]] = {}
        self._key: str | None = None
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        attributes = dict(attrs)
        if self._key is not None:
            self._depth += 1
        elif attributes.get("data-homepage-fact"):
            self._key = attributes["data-homepage-fact"]
            self._depth = 1
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self._key is None:
            return
        self._depth -= 1
        if self._depth == 0:
            value = " ".join("".join(self._text).split())
            self.values.setdefault(self._key, []).append(value)
            self._key = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._key is not None:
            self._text.append(data)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            self.ids.add(identifier)
        if tag == "a" and attributes.get("href"):
            self.hrefs.append(attributes["href"])


def assert_homepage_truth(rendered: str) -> None:
    expected = {
        key.removeprefix("homepage."): value
        for key, value in site_facts().items()
        if key.startswith("homepage.")
        and key
        not in {
            "homepage.ustb.state_class",
            "homepage.ustb.gate_sentence",
            "homepage.fobxx.state_class",
            "homepage.fobxx.source_count",
            "homepage.fobxx.control_count",
            "homepage.fobxx.history_summary",
            "homepage.ustb.control_count",
        }
    }
    parser = _HomepageFactsParser()
    parser.feed(rendered)
    for key, value in expected.items():
        rendered_values = parser.values.get(key, [])
        if rendered_values != [value]:
            shown = rendered_values[0] if len(rendered_values) == 1 else rendered_values
            raise SystemExit(
                f"homepage fact {key} renders {shown!r}; canonical data is {value!r}"
            )


def _partials() -> dict[str, str]:
    return {
        path.stem: path.read_text(encoding="utf-8").rstrip("\n")
        for path in sorted(PARTIALS.glob("*.html"))
    }


def rendered_homepage() -> str:
    return render(PAGES / "index.html", _partials(), load_facts())


def _site_target(path: str) -> Path | None:
    if path == "/status":
        return None
    if path == "/":
        candidates = (SITE / "index.html",)
    else:
        relative = path.lstrip("/")
        candidates = (
            SITE / relative,
            SITE / f"{relative}.html",
            SITE / relative / "index.html",
        )
    return next((candidate for candidate in candidates if candidate.exists()), None)


def assert_page_links(page: Path) -> None:
    parser = _LinkParser()
    parser.feed(page.read_text(encoding="utf-8"))
    for href in parser.hrefs:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc:
            continue
        path = parsed.path or "/"
        target = _site_target(path)
        if target is None:
            if path == "/status":
                continue
            raise SystemExit(f"{page}: link {href!r} has no generated-site target")
        if parsed.fragment and target.suffix == ".html":
            target_parser = _LinkParser()
            target_parser.feed(target.read_text(encoding="utf-8"))
            if parsed.fragment not in target_parser.ids:
                raise SystemExit(
                    f"{page}: link {href!r} has no #{parsed.fragment} target"
                )


def assert_live_status_counts(status_html: str) -> None:
    match = re.search(
        r"<strong>(\d+) reports</strong>, of which\s*"
        r"<strong>(\d+) reached\s*<code>CONFIRMED</code></strong>",
        status_html,
    )
    if match is None:
        raise SystemExit("SITE TRUTH FAIL: live /status report counts were not found")
    live = tuple(map(int, match.groups()))
    facts = site_facts()
    local = (
        int(_committed_facts()["counts"]["reports_published"]),
        int(facts["homepage.confirmed_reports"]),
    )
    if local != live:
        raise SystemExit(
            "SITE TRUTH FAIL: local "
            f"{local[0]}/{local[1]} reports_published/confirmed_reports diverge from "
            f"live /status {live[0]}/{live[1]}"
        )


def check_live_status() -> None:
    request = Request(LIVE_STATUS, headers={"User-Agent": "touchstone-site-truth/1"})
    try:
        with urlopen(request, timeout=10) as response:
            status_html = response.read().decode("utf-8")
    except (OSError, UnicodeError, URLError) as error:
        raise SystemExit(
            f"SITE TRUTH FAIL: live /status could not be read: {error}; "
            "use --offline only in explicitly offline CI"
        ) from error
    assert_live_status_counts(status_html)


def render(source: Path, partials: dict[str, str], facts: dict[str, str]) -> str:
    text = source.read_text(encoding="utf-8")

    def substitute(match: re.Match[str]) -> str:
        if match.group(2):
            name = match.group(2)
            if name not in partials:
                raise SystemExit(f"{source}: unknown partial {{{{> {name}}}}}")
            return partials[name]
        key = match.group(3)
        if key not in facts:
            raise SystemExit(f"{source}: unknown fact {{{{fact:{key}}}}}")
        return facts[key]

    # Two passes so a partial may itself name facts; a partial may not include partials.
    text = _TOKEN.sub(substitute, text)
    text = _TOKEN.sub(substitute, text)
    if "{{" in text:
        offset = text.index("{{")
        raise SystemExit(
            f"{source}: unrendered token near …{text[offset : offset + 60]!r}"
        )
    relative = source.relative_to(PAGES).as_posix()
    return text.replace(
        "<!DOCTYPE html>\n", "<!DOCTYPE html>\n" + BANNER.format(source=relative), 1
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the rendered files match the sources without writing",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the default live /status count comparison for explicitly offline CI",
    )
    arguments = parser.parse_args(argv)

    partials = _partials()
    facts = load_facts()

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_reports.py")], check=True
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_fobxx_dossier.py")],
        check=True,
    )
    sources = sorted(PAGES.rglob("*.html"))
    if not sources:
        print(f"no page sources under {PAGES}", file=sys.stderr)
        return 1
    stale: list[str] = []
    for source in sources:
        rendered = render(source, partials, facts)
        target = SITE / source.relative_to(PAGES)
        if arguments.check:
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != rendered:
                stale.append(str(target.relative_to(ROOT)))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    if arguments.check:
        if stale:
            print(
                "rendered pages differ from their sources — run scripts/build_site.py:"
            )
            for name in stale:
                print(f"  {name}")
            return 1
        assert_homepage_truth(rendered_homepage())
        assert_page_links(SITE / "index.html")
        assert_page_links(SITE / "reports.html")
        if not arguments.offline:
            check_live_status()
        print(f"{len(sources)} rendered pages match their sources")
        return 0
    print(f"{len(sources)} pages rendered into {SITE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
