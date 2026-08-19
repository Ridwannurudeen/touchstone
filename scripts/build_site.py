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
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site2"
PAGES = SITE / "_pages"
PARTIALS = SITE / "_partials"
FACTS = SITE / "_data" / "facts.json"

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


def load_facts() -> dict[str, str]:
    committed = json.loads(FACTS.read_text(encoding="utf-8"))
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
    return flat


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
    arguments = parser.parse_args(argv)

    partials = {
        path.stem: path.read_text(encoding="utf-8").rstrip("\n")
        for path in sorted(PARTIALS.glob("*.html"))
    }
    facts = load_facts()

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
        print(f"{len(sources)} rendered pages match their sources")
        return 0
    print(f"{len(sources)} pages rendered into {SITE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
