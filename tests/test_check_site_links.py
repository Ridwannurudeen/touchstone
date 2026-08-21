from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "check_site_links", Path(__file__).parents[1] / "scripts" / "check_site_links.py"
)
assert SPEC is not None and SPEC.loader is not None
check_site_links = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_site_links
SPEC.loader.exec_module(check_site_links)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_files_clean_routes_trailing_slashes_and_anchors_resolve(
    tmp_path: Path,
) -> None:
    write(tmp_path / "assets" / "site.css", "body {}")
    write(
        tmp_path / "index.html",
        """<h1 id="top">Home</h1>
<a href="/about">About</a>
<a href="/about/#detail">Detail</a>
<a href="#top">Top</a>
<a href="/status">Status</a>
<link href="/assets/site.css" rel="stylesheet">
<a href="https://example.com/elsewhere">External</a>""",
    )
    write(
        tmp_path / "about.html",
        '<h1>About</h1>\n<section id="detail">Detail</section>',
    )

    assert check_site_links.check_site(tmp_path) == []


def test_missing_file_reports_source_and_line(tmp_path: Path) -> None:
    write(tmp_path / "index.html", '<h1>Home</h1>\n<a href="/missing">Gone</a>')

    assert check_site_links.check_site(tmp_path) == [
        f"{tmp_path / 'index.html'}:2: href='/missing' resolves to missing route "
        "'/missing'"
    ]


def test_missing_anchor_reports_target(tmp_path: Path) -> None:
    write(tmp_path / "index.html", '<a href="/about#missing">Missing section</a>')
    write(tmp_path / "about.html", '<h1 id="present">About</h1>')

    assert check_site_links.check_site(tmp_path) == [
        f"{tmp_path / 'index.html'}:1: href='/about#missing' resolves to missing "
        "anchor #missing in about.html"
    ]


def test_build_inputs_are_not_crawled(tmp_path: Path) -> None:
    write(tmp_path / "index.html", "<h1>Home</h1>")
    write(tmp_path / "_pages" / "draft.html", '<a href="/never-built">Draft</a>')
    write(tmp_path / "_template.html", '<a href="/not-public">Template</a>')

    assert check_site_links.check_site(tmp_path) == []
