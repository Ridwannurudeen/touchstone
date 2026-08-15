"""The manifests must describe the fixtures that actually exist, byte for byte."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.probe_sources import (
    ABSOLUTE_MAX_BYTES,
    ProbeTarget,
    _checked_cap,
    _checked_url,
    _redacted,
    load_targets,
    probe,
)


ROOT = Path(__file__).parents[1]
MANIFESTS = ROOT / "manifests" / "sources"
REQUIRED_ASSET_FIELDS = {
    "asset_key",
    "display_name",
    "ticker",
    "role",
    "identity_basis",
}
REQUIRED_PUBLISHER_FIELDS = {"legal_entity", "authority_note"}
REQUIRED_SOURCE_FIELDS = {"source_id", "method", "failure_semantics"}


def manifests() -> list[tuple[str, dict]]:
    return [
        (path.stem, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(MANIFESTS.glob("*.json"))
    ]


def test_every_portfolio_asset_has_a_manifest() -> None:
    assert {name for name, _ in manifests()} == {"ustb", "usdy", "fobxx", "ousg"}


@pytest.mark.parametrize("name,manifest", manifests())
def test_manifest_identifies_its_asset_and_publisher(name: str, manifest: dict) -> None:
    assert REQUIRED_ASSET_FIELDS <= set(manifest["asset"])
    assert REQUIRED_PUBLISHER_FIELDS <= set(manifest["publisher"])
    assert manifest["asset"]["identity_caveat"]


@pytest.mark.parametrize("name,manifest", manifests())
def test_every_source_states_how_it_fails(name: str, manifest: dict) -> None:
    """A source without failure semantics invites a retrieval failure being read as an
    issuer failure, which is the error this project exists to avoid."""
    for source in manifest["sources"]:
        assert REQUIRED_SOURCE_FIELDS <= set(source), source.get("source_id")
        assert "SOURCE_ERROR" in source["failure_semantics"]


@pytest.mark.parametrize("name,manifest", manifests())
def test_declared_fixtures_match_their_recorded_bytes(
    name: str, manifest: dict
) -> None:
    for record in manifest.get("fixtures", []):
        path = ROOT / record["file"]
        assert path.is_file(), record["file"]
        raw = path.read_bytes()
        assert len(raw) == record["bytes"], record["file"]
        assert hashlib.sha256(raw).hexdigest() == record["sha256"], record["file"]


@pytest.mark.parametrize("name,manifest", manifests())
def test_a_manifest_without_fixtures_says_why(name: str, manifest: dict) -> None:
    """Missing evidence is recorded as missing, never left as an unexplained absence."""
    if manifest.get("fixtures"):
        return
    status = manifest["fixtures_status"]
    assert status["search_execution"] == "not_completed"
    assert status["reason"] and status["human_follow_up"]


def test_fobxx_records_its_uncaptured_fixture() -> None:
    manifest = json.loads((MANIFESTS / "fobxx.json").read_text(encoding="utf-8"))
    missing = manifest["fixtures_missing"]
    assert missing and all(
        item["search_execution"] == "not_completed" and item["human_follow_up"]
        for item in missing
    )


def liquidity_rows() -> list[tuple[str, str, str]]:
    """Return (date, daily, weekly) for every dated liquidity row in the filing."""
    text = (ROOT / "fixtures" / "fobxx-nmfp3-20260731.xml").read_text(
        encoding="utf-8", errors="replace"
    )
    return re.findall(
        r"<percentageDailyLiquidAssets>([^<]+)</percentageDailyLiquidAssets>\s*"
        r"<percentageWeeklyLiquidAssets>([^<]+)</percentageWeeklyLiquidAssets>\s*"
        r"<totalLiquidAssetsNearPercentDate>([^<]+)</totalLiquidAssetsNearPercentDate>",
        text,
    )


def test_the_sec_filing_fixture_carries_the_audited_values() -> None:
    """Guards the regulator cross-check: if this file is ever swapped, the numbers move."""
    text = (ROOT / "fixtures" / "fobxx-nmfp3-20260731.xml").read_text(
        encoding="utf-8", errors="replace"
    )

    assert "<reportDate>2026-07-31</reportDate>" in text
    assert "S000067043" in text
    assert "720928224.29" in text
    assert "Franklin OnChain U.S. Government Money Fund" in text


def test_liquidity_is_a_dated_series_not_a_single_figure() -> None:
    """The filing carries one row per business day, so a value means nothing without its
    date. Quoting the first row as the period-end figure is the error this pins shut."""
    rows = liquidity_rows()

    assert len(rows) == 22
    first_daily, first_weekly, first_date = rows[0]
    last_daily, last_weekly, last_date = rows[-1]

    assert (first_date, first_daily, first_weekly) == ("2026-07-01", "0.6742", "0.7462")
    assert (last_date, last_daily, last_weekly) == ("2026-07-31", "0.6528", "0.7455")
    assert first_daily != last_daily, (
        "the series moves; a positional read would misdate it"
    )


def test_the_manifest_does_not_misdate_the_liquidity_figures() -> None:
    """The prior audit quoted the 07-01 values as the period-end figure."""
    manifest = json.loads((MANIFESTS / "fobxx.json").read_text(encoding="utf-8"))
    edgar = next(
        s for s in manifest["sources"] if s["source_id"] == "sec-edgar-fobxx-nmfp3"
    )

    assert "2026-07-01" in edgar["status"]["finding"]
    assert "2026-07-31" in edgar["status"]["finding"]
    assert "65.28" in edgar["status"]["finding"]
    assert edgar["dated_series_warning"]


def test_suspended_and_demoted_assets_say_so_in_their_role() -> None:
    """A blocked asset must not sit in the portfolio looking healthy."""
    usdy = json.loads((MANIFESTS / "usdy.json").read_text(encoding="utf-8"))
    fobxx = json.loads((MANIFESTS / "fobxx.json").read_text(encoding="utf-8"))
    ousg = json.loads((MANIFESTS / "ousg.json").read_text(encoding="utf-8"))

    assert "SUSPENDED" in usdy["asset"]["role"]
    assert "DEMOTED" in fobxx["asset"]["role"]
    assert ousg["asset"]["promotion_state"].startswith("not promoted")
    assert ousg["qualification"]["oracle_cross_check"]["status"] == "UNVERIFIED"


def test_probe_refuses_a_non_https_or_unbounded_target() -> None:
    """The safety claims are enforced in code, not merely true of today's manifests."""
    with pytest.raises(ValueError, match="https"):
        _checked_url("http://example.com/x", "s")
    with pytest.raises(ValueError, match="credentials"):
        _checked_url("https://user:pw@example.com/x", "s")
    with pytest.raises(ValueError, match="port"):
        _checked_url("https://example.com:8443/x", "s")
    with pytest.raises(ValueError, match="positive integer"):
        _checked_cap(0, "s")
    with pytest.raises(ValueError, match="ceiling"):
        _checked_cap(ABSOLUTE_MAX_BYTES + 1, "s")


def test_no_manifest_persists_a_rotating_credential() -> None:
    """USDY's rlkey rotates and must be rediscovered, so no real value may be stored.

    A documented URL pattern with a placeholder is fine and useful; an actual credential
    is not, because persisting one contradicts the manifest's own rule and would go stale.
    """
    for name, manifest in manifests():
        for match in re.findall(r"rlkey=([^\"&\s]*)", json.dumps(manifest)):
            assert match.startswith("<") and match.endswith(">"), (
                f"{name}: a real rlkey value is persisted: {match!r}"
            )


def test_probe_revalidates_a_hand_built_target() -> None:
    """A ProbeTarget built directly must not reach the network on weaker terms."""
    unsafe = ProbeTarget(
        manifest="x",
        source_id="s",
        url="http://example.com/x",
        max_bytes=1,
        expected_mime=None,
    )

    with pytest.raises(ValueError, match="https"):
        probe(unsafe)


def test_probe_refuses_a_forged_cap_on_a_declared_url() -> None:
    """Membership by URL alone let a hand-built target widen its own byte cap."""
    declared = next(
        target
        for target in load_targets()
        if target.source_id == "superstate-ustb-yield"
    )
    forged = ProbeTarget(
        manifest=declared.manifest,
        source_id=declared.source_id,
        url=declared.url,
        max_bytes=ABSOLUTE_MAX_BYTES,
        expected_mime=declared.expected_mime,
    )

    assert forged.url == declared.url and forged.max_bytes > declared.max_bytes
    with pytest.raises(ValueError, match="not exactly as declared"):
        probe(forged)


def test_the_discovery_fixture_points_at_the_filing_fixture() -> None:
    """Discovery is only useful if it resolves to the filing actually retained."""
    submissions = json.loads(
        (ROOT / "fixtures" / "fobxx-submissions-20260815.json").read_text(
            encoding="utf-8"
        )
    )
    recent = submissions["filings"]["recent"]
    index = recent["form"].index("N-MFP3")

    assert submissions["cik"] == "0001786958"
    assert recent["reportDate"][index] == "2026-07-31"
    assert recent["accessionNumber"][index] == "0002071691-26-017542"

    manifest = json.loads((MANIFESTS / "fobxx.json").read_text(encoding="utf-8"))
    filing = next(
        record
        for record in manifest["fixtures"]
        if record["source_id"] == "sec-edgar-fobxx-nmfp3"
    )
    assert filing["accession"] == recent["accessionNumber"][index]


def test_probe_refuses_an_undeclared_https_target() -> None:
    """HTTPS alone is not enough: the module promises only manifest-declared URLs."""
    undeclared = ProbeTarget(
        manifest="x",
        source_id="s",
        url="https://example.com/anything",
        max_bytes=1024,
        expected_mime=None,
    )

    with pytest.raises(ValueError, match="not exactly as declared"):
        probe(undeclared)


@pytest.mark.parametrize("name,manifest", manifests())
def test_every_source_states_a_fixture_disposition(name: str, manifest: dict) -> None:
    """A source with no fixture and no stated reason is an unexplained gap."""
    captured = {record.get("source_id") for record in manifest.get("fixtures", [])}
    for source in manifest["sources"]:
        disposition = source.get("fixture_disposition")
        assert disposition, f"{name}/{source['source_id']} has no fixture disposition"
        kind = disposition.split(":")[0]
        assert kind in {"captured", "blocked", "exempt", "deferred"}, disposition
        if kind == "captured":
            assert source["source_id"] in captured, (
                f"{name}/{source['source_id']} claims captured but no fixture "
                f"declares it as its source"
            )


def test_edgar_filing_discovery_is_probed_not_just_one_filing() -> None:
    """Probing only a known filing says nothing about finding the next one."""
    manifest = json.loads((MANIFESTS / "fobxx.json").read_text(encoding="utf-8"))
    ids = {source["source_id"] for source in manifest["sources"]}

    assert "sec-edgar-fobxx-submissions" in ids
    probed = {target.source_id for target in load_targets()}
    assert "sec-edgar-fobxx-submissions" in probed


def test_probe_output_never_carries_a_url_credential() -> None:
    """USDY's rlkey rides in the query string, so the query is dropped before printing."""
    assert (
        _redacted("https://www.dropbox.com/scl/fo/abc/def?rlkey=SECRET&dl=1")
        == "https://www.dropbox.com/scl/fo/abc/def"
    )


def test_usdy_records_the_unbounded_retrieval_problem() -> None:
    """USDY may not be scheduled until a bounded retrieval exists; the manifest says so."""
    manifest = json.loads((MANIFESTS / "usdy.json").read_text(encoding="utf-8"))
    problem = manifest["retrieval_problem"]

    assert problem["status"].startswith("UNRESOLVED")
    assert "260431605" in problem["detail"]
    assert problem["before_PLAN_T10"]


def test_probe_targets_are_only_manifest_declared_get_urls() -> None:
    targets = load_targets()
    declared = {
        source.get("url") or source.get("url_observed")
        for _, manifest in manifests()
        for source in manifest["sources"]
    }

    assert targets, "probe found no targets"
    for target in targets:
        assert target.url in declared
        assert target.url.startswith("https://")
        assert target.max_bytes > 0


def test_probe_never_targets_a_post_source() -> None:
    """The Franklin endpoint is POST-only; a reachability probe must not construct one."""
    post_urls = {
        source["url"]
        for _, manifest in manifests()
        for source in manifest["sources"]
        if source.get("method") == "POST"
    }

    assert post_urls, "expected at least one POST source in the portfolio"
    assert not {target.url for target in load_targets()} & post_urls
