"""The panels are shown to a camera, so a wrong one is worse than a missing one."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_film_panels", ROOT / "scripts" / "build_film_panels.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


panels = _module()


def row(day: str, nav: str) -> dict:
    return {
        "assets_under_management": "957654989.4700",
        "fund_id": 1,
        "net_asset_value": nav,
        "net_asset_value_date": day,
        "net_income_expenses": "92763.22109200",
        "outstanding_shares": "85666804.737417",
        "subscription_nav_per_share": None,
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Two captures whose only recent difference is a revised August row.

    The December rows exist because they are the whole point of the regression below: they
    sort above the August ones as text and below them as dates.
    """
    old_rows = [
        row("12/30/2025", "10.00000000"),
        row("12/31/2025", "10.10000000"),
        row("08/15/2026", "11.17883400"),
        row("08/16/2026", "11.17883400"),
        row("08/17/2026", "11.17883400"),
    ]
    new_rows = [
        row("12/30/2025", "10.00000000"),
        row("12/31/2025", "10.10000000"),
        row("08/15/2026", "11.17883400"),
        row("08/16/2026", "11.17883400"),
        row("08/17/2026", "11.18208300"),
        row("08/18/2026", "11.18208300"),
    ]
    evidence = tmp_path / "evidence"
    objects = evidence / "objects"
    objects.mkdir(parents=True)
    lines = []
    for retrieved, rows in (
        ("2026-08-17T16:48:32.075003Z", old_rows),
        ("2026-08-18T16:28:07.833801Z", new_rows),
    ):
        raw = json.dumps(rows).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        (objects / digest).write_bytes(raw)
        lines.append(
            json.dumps(
                {
                    "source_id": "superstate-ustb-nav-daily",
                    "retrieved_at": retrieved,
                    "sha256": digest,
                    "byte_size": len(raw),
                }
            )
        )
    (evidence / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


class TestTheDiffPanel:
    def test_it_shows_the_revised_row_not_the_lexicographically_latest(
        self, workspace: Path
    ) -> None:
        """The regression that shipped once.

        `net_asset_value_date` is "MM/DD/YYYY" text, so sorting it as text puts 12/31/2025
        above 08/17/2026. The first cut of the panel therefore selected three December rows,
        every one of them unchanged, and rendered a panel headed "the catch" containing no
        catch — which is the worst possible artifact to put in front of a camera.
        """
        earlier, later = panels._captures(workspace)
        page, identical, changed = panels.panel_diff(workspace, earlier, later)
        assert changed == 1, "the revised August row must be the one shown"
        assert "08/17/2026" in page
        assert "12/31/2025" not in page, (
            "a December row means the sort is textual again"
        )

    def test_it_shows_the_before_and_after_values(self, workspace: Path) -> None:
        earlier, later = panels._captures(workspace)
        page, _, _ = panels.panel_diff(workspace, earlier, later)
        assert "11.17883400" in page and "11.18208300" in page

    def test_unchanged_rows_are_labelled_unchanged(self, workspace: Path) -> None:
        earlier, later = panels._captures(workspace)
        page, _, _ = panels.panel_diff(workspace, earlier, later)
        assert page.count("unchanged") >= 2

    def test_the_identical_count_is_real(self, workspace: Path) -> None:
        earlier, later = panels._captures(workspace)
        _, identical, _ = panels.panel_diff(workspace, earlier, later)
        assert identical == 4


class TestItRefusesRatherThanRenderingNothing:
    def test_one_capture_is_not_a_comparison(self, tmp_path: Path) -> None:
        """A panel with no comparison in it would still read as a finding on screen."""
        evidence = tmp_path / "evidence"
        (evidence / "objects").mkdir(parents=True)
        (evidence / "index.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "superstate-ustb-nav-daily",
                    "retrieved_at": "2026-08-17T16:48:32.075003Z",
                    "sha256": "a" * 64,
                    "byte_size": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="fewer than two"):
            panels._captures(tmp_path)

    def test_repeated_identical_captures_are_not_two_captures(
        self, tmp_path: Path
    ) -> None:
        """The observer stores a capture every pass, most of them byte-identical.

        Counting index entries rather than distinct artifacts would pick the same bytes
        twice and render a panel proving nothing changed between a thing and itself.
        """
        evidence = tmp_path / "evidence"
        (evidence / "objects").mkdir(parents=True)
        lines = [
            json.dumps(
                {
                    "source_id": "superstate-ustb-nav-daily",
                    "retrieved_at": stamp,
                    "sha256": "a" * 64,
                    "byte_size": 1,
                }
            )
            for stamp in (
                "2026-08-18T10:00:00.000000Z",
                "2026-08-18T10:15:00.000000Z",
                "2026-08-18T10:30:00.000000Z",
            )
        ]
        (evidence / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="fewer than two"):
            panels._captures(tmp_path)


class TestNothingIsFetched:
    def test_the_panel_builder_reaches_no_network(self) -> None:
        """The film's claim is about retained evidence; a live fetch would be a different film."""
        text = (ROOT / "scripts" / "build_film_panels.py").read_text(encoding="utf-8")
        for forbidden in (
            "requests",
            "urlopen",
            "httpx",
            "fetch_source",
            "LiveTransport",
        ):
            assert forbidden not in text

    def test_the_policy_panel_reads_the_approved_control(self) -> None:
        page = panels.panel_policy()
        assert "ustb-nav-per-share-present" in page
        assert "2 business days" in page
