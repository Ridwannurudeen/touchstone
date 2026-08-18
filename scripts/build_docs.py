"""Render the committed documentation into the site's docs surface.

The depth of this project lives in `docs/`, and the site showed almost none of it. This
converts the markdown that is already reviewed and committed rather than re-writing it for the
web: a docs page that paraphrases its source is a second copy that drifts, and this project's
whole argument is that a published claim should match the artifact behind it.

**The publish list is an allowlist, deliberately.** Several documents in `docs/` record host
paths, environment variable names and key-custody facts. Saying in a threat model that there is
no HSM is mature; publishing where the keys live is a map for somebody else. Nothing is
published because it exists — it is published because it is named here.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site2"
TEMPLATE = SITE / "_docs-template.html"
OUT = SITE / "docs"

# slug -> (source file, title, one-line summary for the index)
PUBLISHED: dict[str, tuple[Path, str, str]] = {
    "control-language": (
        ROOT / "docs" / "CONTROL-LANGUAGE.md",
        "The control language",
        "What a control is: its subject, the byte-exact span it cites, its adapter, operator "
        "and expected value, and the period it is in force for.",
    ),
    "threat-model": (
        ROOT / "docs" / "THREAT-MODEL.md",
        "Threat model",
        "What can go wrong, what the design stops, and the residual risks that remain open "
        "with their identifiers.",
    ),
    "limitations": (
        ROOT / "docs" / "LIMITATIONS.md",
        "Limitations",
        "Every target that was missed, recorded as missed, and every claim the system cannot "
        "support.",
    ),
    "usdy-retrieval": (
        ROOT / "docs" / "USDY-RETRIEVAL.md",
        "USDY retrieval",
        "What was measured when the 260 MB suspension was re-examined, including the two "
        "shortcuts that turned out to be closed.",
    ),
    "ai": (
        ROOT / "AI_USAGE.md",
        "AI usage",
        "Where a model is used, what it may propose, what it may never decide, and the "
        "measured outcome of every compilation in this repository.",
    ),
    "source-audit": (
        ROOT / "SOURCE_AUDIT.md",
        "Source audit",
        "Every candidate issuer source that was examined, what it returned, and why it was "
        "kept or set aside.",
    ),
    "roadmap": (
        ROOT / "ROADMAP.md",
        "Roadmap",
        "The build plan, the product principles, and the security and governance ladder.",
    ),
}

_HEADING = re.compile(r"<h([23]) id=\"([^\"]+)\">(.*?)</h\1>", re.S)


def _headings(body: str) -> list[tuple[int, str, str]]:
    """The h2/h3 headings, for the on-this-page rail."""
    return [
        (int(level), anchor, re.sub(r"<[^>]+>", "", text).strip())
        for level, anchor, text in _HEADING.findall(body)
    ]


def _nav(headings: list[tuple[int, str, str]]) -> str:
    if not headings:
        return '      <p class="doc-toc-empty">No sections.</p>'
    lines = ['      <ol class="doc-toc-list">']
    for level, anchor, text in headings:
        cls = "toc-2" if level == 2 else "toc-3"
        lines.append(
            f'        <li class="{cls}"><a href="#{anchor}">{html.escape(text)}</a></li>'
        )
    lines.append("      </ol>")
    return "\n".join(lines)


def _demote_after_first(body: str) -> str:
    """Keep the document's first <h1> and demote the rest.

    State lives in this function rather than in a closure default, which would have carried
    the count from one document into the next and quietly stripped the <h1> from every page
    after the first.
    """
    parts = re.split(r"(<h1[^>]*>.*?</h1>)", body, flags=re.S)
    seen = 0
    for index, part in enumerate(parts):
        if not part.startswith("<h1"):
            continue
        seen += 1
        if seen > 1:
            parts[index] = re.sub(
                r"^<h1([^>]*)>(.*)</h1>$", r"<h2\1>\2</h2>", part, flags=re.S
            )
    return "".join(parts)


def _fill(title: str, body: str, headings: list[tuple[int, str, str]]) -> str:
    page = TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("<!--DOC_TITLE-->", html.escape(title))
    page = page.replace("<!--DOC_BODY-->", body)
    return page.replace("<!--DOC_NAV-->", _nav(headings))


def render(slug: str) -> tuple[str, int]:
    source, title, _ = PUBLISHED[slug]
    text = source.read_text(encoding="utf-8")

    converter = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"anchorlink": False, "permalink": False}},
    )
    body = converter.convert(text)

    # Exactly one <h1> per page, which is the rule every hand-written page here is checked
    # against. The template deliberately carries none — the document's own title becomes it —
    # so the first heading stays and only the rest demote. ROADMAP.md opens six phases at top
    # level, so this is not hypothetical; demoting all six left the page with no <h1> at all,
    # and demoting none left it with six. Both were caught by counting rather than by reading.
    body = _demote_after_first(body)

    target = OUT / f"{slug}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_fill(title, body, _headings(body)), encoding="utf-8")
    return title, len(text.splitlines())


def render_index(rows: list[tuple[str, str, int]]) -> None:
    """The docs landing page, from the same template so it cannot drift from the pages.

    Each entry carries its source line count, because the number is the argument: this is the
    documentation the project works from, not a summary written for a website.
    """
    items = [
        '<h1 id="documentation">Documentation</h1>',
        "<p>Six committed documents, rendered from the repository rather than rewritten for "
        "the web. Each is the file the project itself works from.</p>",
        '<ul class="doc-index">',
    ]
    for slug, title, lines in rows:
        items.append(
            f'<li><a href="/docs/{slug}"><strong>{html.escape(title)}</strong></a> '
            f'<span class="doc-index-lines">{lines} lines</span><br>'
            f'<span class="doc-index-sum">{html.escape(PUBLISHED[slug][2])}</span></li>'
        )
    items.append("</ul>")
    items.append(
        "<p>Documents recording host paths, environment variable names or key custody are "
        "deliberately not published. Stating in a threat model that there is no HSM is "
        "accountability; publishing where the keys live is something else.</p>"
    )
    body = "\n".join(items)
    (SITE / "docs.html").write_text(
        _fill("Documentation", body, _headings(body)), encoding="utf-8"
    )


def main() -> int:
    if not TEMPLATE.exists():
        print(f"missing template: {TEMPLATE}", file=sys.stderr)
        return 1

    rows = []
    for slug in PUBLISHED:
        title, lines = render(slug)
        rows.append((slug, title, lines))
        print(f"{slug:<20} {title:<24} {lines:>5} source lines")

    render_index(rows)
    print(
        f"\n{len(rows)} documents rendered into {OUT.relative_to(ROOT)}, plus docs.html"
    )
    print(f"{sum(r[2] for r in rows)} lines of committed documentation now published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
