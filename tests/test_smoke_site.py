from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "smoke_site", Path(__file__).parents[1] / "scripts" / "smoke_site.py"
)
assert SPEC is not None and SPEC.loader is not None
smoke_site = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke_site
SPEC.loader.exec_module(smoke_site)


def page(*, extra_hero: str = "", script: str = "") -> str:
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width">{script}</head>
<body><header><nav class="site-nav"><a href="/">Home</a></nav></header>
<main><section class="hero"><h1>Title</h1><a class="cta">Go</a>{extra_hero}</section>
</main></body></html>"""


def test_structural_smoke_passes_and_reports_the_absent_reports_page(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text(
        page(script='<script type="application/ld+json">{}</script>'),
        encoding="utf-8",
    )

    failures, notices = smoke_site.check_site(tmp_path)

    assert failures == []
    assert any("reports.html is not present" in notice for notice in notices)
    assert "header nav item count: 1" in notices


def test_duplicate_primary_cta_and_inline_script_fail(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        page(extra_hero='<a class="cta">Again</a>', script="<script>alert(1)</script>"),
        encoding="utf-8",
    )

    failures, _ = smoke_site.check_site(tmp_path)

    assert any("2 primary .cta" in failure for failure in failures)
    assert any("executable inline script" in failure for failure in failures)


def test_reports_header_count_must_match_homepage(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(page(), encoding="utf-8")
    (tmp_path / "reports.html").write_text(
        page().replace(
            '<a href="/">Home</a>',
            '<a href="/">Home</a><a href="/reports">Reports</a>',
        ),
        encoding="utf-8",
    )

    failures, _ = smoke_site.check_site(tmp_path)

    assert failures == ["header nav item count differs by route: {'/': 1, '/reports': 2}"]
