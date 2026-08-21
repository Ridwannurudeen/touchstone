"""Check that generated-site links and fragments resolve locally.

The generated site lives in ``site2``. Its underscore-prefixed directories are build
inputs, not public pages. ``/status`` is the sole known route generated on the host by
``scripts/build_status.py`` rather than committed to this tree.
"""

from __future__ import annotations

import argparse
import posixpath
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site2"
SITE_HOST = "touchstone.gudman.xyz"
KNOWN_ROUTES = frozenset({"/status"})


@dataclass(frozen=True, slots=True)
class Reference:
    attribute: str
    value: str
    line: int


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[Reference] = []
        self.anchors: set[str] = set()

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attributes)
        identifier = values.get("id")
        if identifier:
            self.anchors.add(identifier)
        if tag == "a" and values.get("name"):
            self.anchors.add(values["name"])
        for attribute in ("href", "src"):
            value = values.get(attribute)
            if value:
                self.references.append(
                    Reference(attribute, value, self.getpos()[0])
                )


def public_html_files(site: Path) -> list[Path]:
    return [
        path
        for path in sorted(site.rglob("*.html"))
        if not any(part.startswith("_") for part in path.relative_to(site).parts)
    ]


def page_url(path: Path, site: Path) -> str:
    relative = path.relative_to(site).as_posix()
    if relative == "index.html":
        return "/"
    if relative.endswith("/index.html"):
        return "/" + relative[: -len("index.html")]
    return "/" + relative.removesuffix(".html")


def normalized_route(path: str) -> str:
    decoded = unquote(path)
    normalized = posixpath.normpath("/" + decoded.lstrip("/"))
    return normalized if normalized == "/" else normalized.rstrip("/")


def target_for(route: str, site: Path) -> Path | None:
    if route == "/":
        candidates = (site / "index.html",)
    else:
        relative = route.lstrip("/")
        candidates = (
            site / relative,
            site / f"{relative}.html",
            site / relative / "index.html",
        )
    return next(
        (
            candidate
            for candidate in candidates
            if candidate.is_file()
            and not any(
                part.startswith("_") for part in candidate.relative_to(site).parts
            )
        ),
        None,
    )


def internal_url(reference: str, source_url: str) -> tuple[str, str] | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc != SITE_HOST:
            return None
        path = parsed.path
    else:
        path = urlsplit(urljoin(source_url, reference)).path
    return normalized_route(path), unquote(parsed.fragment)


def check_site(site: Path = SITE) -> list[str]:
    pages = public_html_files(site)
    parsed_pages: dict[Path, PageParser] = {}
    for page in pages:
        parser = PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        parsed_pages[page] = parser

    failures: list[str] = []
    for page, parser in parsed_pages.items():
        source_url = page_url(page, site)
        display = page.relative_to(ROOT) if page.is_relative_to(ROOT) else page
        for reference in parser.references:
            resolved = internal_url(reference.value, source_url)
            if resolved is None:
                continue
            route, fragment = resolved
            if route in KNOWN_ROUTES:
                continue
            target = target_for(route, site)
            prefix = (
                f"{display}:{reference.line}: {reference.attribute}="
                f"{reference.value!r}"
            )
            if target is None:
                failures.append(f"{prefix} resolves to missing route {route!r}")
                continue
            if fragment and target.suffix.lower() == ".html":
                target_parser = parsed_pages[target]
                if fragment not in target_parser.anchors:
                    failures.append(
                        f"{prefix} resolves to missing anchor #{fragment} in "
                        f"{target.relative_to(site).as_posix()}"
                    )
    return failures


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
        failures = check_site(arguments.site.resolve())
    except OSError as error:
        print(f"SITE LINK CHECK FAIL: {error}", file=sys.stderr)
        return 1
    if failures:
        print(f"SITE LINK CHECK FAIL: {len(failures)} dead internal link(s)")
        for failure in failures:
            print(f"  {failure}")
        return 1
    page_count = len(public_html_files(arguments.site.resolve()))
    print(
        f"site links are valid across {page_count} generated HTML pages "
        f"({len(KNOWN_ROUTES)} known host-generated route)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
