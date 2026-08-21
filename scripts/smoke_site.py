"""Run dependency-free structural smoke checks on generated public pages.

This is deliberately an HTML-level smoke because the repository declares no browser
automation dependency. It checks generated markup, but it does not execute JavaScript,
render CSS, exercise responsive layouts, or make navigation requests. The reports page is
checked when its generated file is present; until that branch is merged, the omission is
reported explicitly rather than mistaken for browser coverage.
"""

from __future__ import annotations

import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site2"
PAGES = {"/": "index.html", "/reports": "reports.html"}
INERT_SCRIPT_TYPES = frozenset({"application/json", "application/ld+json"})
VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}
)


class SmokeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1_count = 0
        self.viewport_count = 0
        self.hero_primary_ctas = 0
        self.nav_counts: list[int] = []
        self.executable_inline_script_lines: list[int] = []
        self._stack: list[tuple[str, bool, bool]] = []
        self._hero_depth = 0
        self._nav_depth = 0
        self._current_nav_links = 0

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attributes)
        classes = set((values.get("class") or "").split())
        enters_hero = "hero" in classes
        enters_nav = tag == "nav" and "site-nav" in classes

        if tag == "h1":
            self.h1_count += 1
        if tag == "meta" and (values.get("name") or "").lower() == "viewport":
            self.viewport_count += 1
        if self._hero_depth and tag == "a" and "cta" in classes:
            self.hero_primary_ctas += 1
        if self._nav_depth and tag == "a":
            self._current_nav_links += 1
        if tag == "script" and not values.get("src"):
            script_type = (values.get("type") or "").lower()
            if script_type not in INERT_SCRIPT_TYPES:
                self.executable_inline_script_lines.append(self.getpos()[0])

        if enters_hero:
            self._hero_depth += 1
        if enters_nav:
            self._nav_depth += 1
            self._current_nav_links = 0
        if tag not in VOID_ELEMENTS:
            self._stack.append((tag, enters_hero, enters_nav))

    def handle_startendtag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attributes)
        if tag not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        open_tag, exits_hero, exits_nav = self._stack.pop()
        if open_tag != tag:
            return
        if exits_nav:
            self.nav_counts.append(self._current_nav_links)
            self._current_nav_links = 0
            self._nav_depth -= 1
        if exits_hero:
            self._hero_depth -= 1


def inspect_page(path: Path) -> SmokeParser:
    parser = SmokeParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def page_failures(route: str, parsed: SmokeParser) -> list[str]:
    failures: list[str] = []
    if parsed.h1_count != 1:
        failures.append(f"{route}: expected exactly 1 h1, found {parsed.h1_count}")
    if parsed.viewport_count != 1:
        failures.append(
            f"{route}: expected exactly 1 viewport meta, found {parsed.viewport_count}"
        )
    if parsed.hero_primary_ctas > 1:
        failures.append(
            f"{route}: hero contains {parsed.hero_primary_ctas} primary .cta links; "
            "expected at most 1"
        )
    if len(parsed.nav_counts) != 1 or parsed.nav_counts[0] < 1:
        failures.append(
            f"{route}: expected one non-empty header .site-nav, found counts "
            f"{parsed.nav_counts}"
        )
    for line in parsed.executable_inline_script_lines:
        failures.append(f"{route}:{line}: executable inline script is not allowed")
    return failures


def check_site(site: Path = SITE) -> tuple[list[str], list[str]]:
    parsed_pages: dict[str, SmokeParser] = {}
    notices: list[str] = []
    for route, relative in PAGES.items():
        path = site / relative
        if not path.is_file():
            if route == "/reports":
                notices.append(
                    "/reports smoke skipped: generated reports.html is not present yet"
                )
                continue
            return [f"{route}: generated file {path} is missing"], notices
        parsed_pages[route] = inspect_page(path)

    failures = [
        failure
        for route, parsed in parsed_pages.items()
        for failure in page_failures(route, parsed)
    ]
    nav_counts = {
        route: parsed.nav_counts[0]
        for route, parsed in parsed_pages.items()
        if len(parsed.nav_counts) == 1
    }
    if len(set(nav_counts.values())) > 1:
        failures.append(f"header nav item count differs by route: {nav_counts}")
    if nav_counts:
        notices.append(f"header nav item count: {next(iter(nav_counts.values()))}")
    return failures, notices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "site",
        nargs="?",
        type=Path,
        default=SITE,
        help="generated site directory (default: site2)",
    )
    arguments = parser.parse_args(argv)
    try:
        failures, notices = check_site(arguments.site.resolve())
    except OSError as error:
        print(f"SITE SMOKE FAIL: {error}", file=sys.stderr)
        return 1
    for notice in notices:
        print(f"SITE SMOKE NOTICE: {notice}")
    if failures:
        print(f"SITE SMOKE FAIL: {len(failures)} structural failure(s)")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(
        "site HTML smoke passed (structural only; no JS, CSS, responsive, or "
        "navigation execution)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
