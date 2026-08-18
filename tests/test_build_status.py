"""A status page's failure mode is confidence, so these test what it refuses to say."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from touchstone import heartbeat, observation
from touchstone.assets import USTB
from touchstone.workspace import Workspace

ROOT = Path(__file__).parents[1]
REGISTRY = "0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C"
AT = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_status", ROOT / "scripts" / "build_status.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_status = _module()


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    space = Workspace(tmp_path)
    space.root.mkdir(parents=True, exist_ok=True)
    log = space.root / "observations.jsonl"
    for manifest in USTB.sources:
        observation.append(
            log,
            observation.build_record(
                observation.Observation(
                    source_id=manifest.source_id,
                    observed_at="2026-08-18T11:45:00Z",
                    transition=observation.Transition.UNCHANGED,
                    payload_sha256="a" * 64,
                    previous_payload_sha256="a" * 64,
                    normalized_sha256="n" * 64,
                    previous_normalized_sha256="n" * 64,
                    byte_size=1024,
                    detail=None,
                )
            ),
        )
    return space


def render(space: Workspace, *, now: datetime = AT) -> str:
    return build_status.render(space, now=now, registry_address=REGISTRY)


class TestItRefusesToOverclaim:
    def test_no_relative_time_is_ever_rendered(self, workspace: Workspace) -> None:
        """A static page cannot know when it is read, so it must not count from that.

        "Checked 12 seconds ago" would be computed once, written to a file, and then served
        unchanged and increasingly wrong for as long as the file survives.
        """
        page = render(workspace).lower()
        for phrase in (
            "seconds ago",
            "minutes ago",
            "hours ago",
            "just now",
            "moments ago",
        ):
            assert phrase not in page

    def test_the_generation_time_is_stated(self, workspace: Workspace) -> None:
        assert "2026-08-18T12:00:00Z" in render(workspace)

    def test_a_stale_page_is_not_presented_as_a_dead_daemon(
        self, workspace: Workspace
    ) -> None:
        page = render(workspace)
        assert "not proof that the daemon is down" in page

    def test_it_never_claims_an_asset_is_verified(self, workspace: Workspace) -> None:
        page = render(workspace)
        assert "UNVERIFIABLE" in page
        for forbidden in ("CONFIRMED", "verified and safe", "proof of reserves"):
            assert forbidden not in page

    def test_a_payload_change_is_not_described_as_a_change_in_the_data(
        self, workspace: Workspace
    ) -> None:
        """The gloss that stops a re-serialisation reading as the issuer moving a number."""
        log = workspace.root / "observations.jsonl"
        observation.append(
            log,
            observation.build_record(
                observation.Observation(
                    source_id=USTB.sources[0].source_id,
                    observed_at="2026-08-18T11:55:00Z",
                    transition=observation.Transition.PAYLOAD_CHANGED,
                    payload_sha256="b" * 64,
                    previous_payload_sha256="a" * 64,
                    normalized_sha256="n" * 64,
                    previous_normalized_sha256="n" * 64,
                    byte_size=1024,
                    detail=None,
                )
            ),
        )
        assert "not a change in what the issuer published" in render(workspace)


class TestDaemonVerdict:
    def test_absent_heartbeat_is_reported_as_absent(self, workspace: Workspace) -> None:
        page = render(workspace)
        assert "No heartbeat has been written" in page

    def test_a_fresh_heartbeat_reads_as_within_its_window(
        self, workspace: Workspace
    ) -> None:
        heartbeat.write(
            workspace.root / "heartbeat.json",
            heartbeat.build_record(
                asset_key=USTB.asset_key,
                registry_address=REGISTRY,
                sequence=1,
                now=AT - timedelta(seconds=30),
            ),
        )
        assert "Within its declared window" in render(workspace)

    def test_an_expired_heartbeat_reads_as_outside_it(
        self, workspace: Workspace
    ) -> None:
        """The verdict must follow the clock, not a stored answer.

        Same file, same content, read at a later moment: a process that has stopped cannot
        write down that it stopped, so nothing here may trust what it last wrote.
        """
        heartbeat.write(
            workspace.root / "heartbeat.json",
            heartbeat.build_record(
                asset_key=USTB.asset_key,
                registry_address=REGISTRY,
                sequence=1,
                now=AT,
            ),
        )
        assert "Within its declared window" in render(workspace)
        assert "Outside its declared window" in render(
            workspace, now=AT + timedelta(days=1)
        )

    def test_a_heartbeat_for_another_registry_is_not_accepted(
        self, workspace: Workspace
    ) -> None:
        heartbeat.write(
            workspace.root / "heartbeat.json",
            heartbeat.build_record(
                asset_key=USTB.asset_key,
                registry_address="0x" + "cd" * 20,
                sequence=1,
                now=AT,
            ),
        )
        assert "Outside its declared window" in render(workspace)


class TestPage:
    def test_exactly_one_h1(self, workspace: Workspace) -> None:
        assert render(workspace).count("<h1") == 1

    def test_every_observed_source_appears(self, workspace: Workspace) -> None:
        page = render(workspace)
        for manifest in USTB.sources:
            assert manifest.source_id in page

    def test_an_empty_log_still_renders(self, tmp_path: Path) -> None:
        space = Workspace(tmp_path / "empty")
        space.root.mkdir(parents=True)
        assert "No observation has been recorded yet" in render(space)

    def test_detail_text_is_escaped(self, tmp_path: Path) -> None:
        """Detail carries an exception message, which is the one field an outsider shapes."""
        space = Workspace(tmp_path / "escaped")
        space.root.mkdir(parents=True)
        observation.append(
            space.root / "observations.jsonl",
            observation.build_record(
                observation.Observation(
                    source_id=USTB.sources[0].source_id,
                    observed_at="2026-08-18T11:45:00Z",
                    transition=observation.Transition.SOURCE_UNAVAILABLE,
                    payload_sha256=None,
                    previous_payload_sha256=None,
                    normalized_sha256=None,
                    previous_normalized_sha256=None,
                    byte_size=None,
                    detail="<script>alert(1)</script>",
                )
            ),
        )
        page = render(space)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_it_writes_the_file_it_reports(
        self, workspace: Workspace, tmp_path: Path
    ) -> None:
        target = tmp_path / "out" / "status.html"
        assert (
            build_status.main(
                [
                    "--workspace",
                    str(workspace.root),
                    "--out",
                    str(target),
                    "--registry-address",
                    REGISTRY,
                ]
            )
            == 0
        )
        assert target.read_text(encoding="utf-8").count("<h1") == 1


class TestNoSecrets:
    def test_the_generator_reads_no_key(self) -> None:
        text = (ROOT / "scripts" / "build_status.py").read_text(encoding="utf-8")
        for forbidden in (
            "TOUCHSTONE_PUBLISHER_PRIVATE_KEY",
            "TOUCHSTONE_SIGNING_SEED",
            "PublisherKey",
            "Ed25519Signer",
        ):
            assert forbidden not in text

    def test_the_page_carries_no_filesystem_path(self, workspace: Workspace) -> None:
        """The workspace lives beside the keys; its path is not a public fact."""
        page = render(workspace)
        assert str(workspace.root) not in page
        assert "C:\\" not in page and "/home/" not in page

    def test_the_page_carries_no_process_identity(self, workspace: Workspace) -> None:
        heartbeat.write(
            workspace.root / "heartbeat.json",
            heartbeat.build_record(
                asset_key=USTB.asset_key,
                registry_address=REGISTRY,
                sequence=1,
                now=AT,
                process_id=4242,
            ),
        )
        page = render(workspace)
        record = json.loads(
            (workspace.root / "heartbeat.json").read_text(encoding="utf-8")
        )
        assert str(record["process_identity"]) not in page
        assert "4242" not in page
