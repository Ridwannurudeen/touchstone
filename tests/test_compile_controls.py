"""Asset-selectable control compilation from retained evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

from touchstone.assets import FOBXX, USTB
from touchstone.compiler import (
    CompilationStatus,
    DeterministicFixtureProvider,
    ProviderResponse,
    _fobxx_comparison_span,
    compile_evidence,
)
from touchstone.evidence import EvidenceStore

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import compile_controls  # noqa: E402


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures"
USTB_DIGESTS = {
    "superstate-ustb-holdings": (
        "261bc18d84c9c00489cbc7e69ec8f568953c3e998195f5b383c757f64d4a4227"
    ),
    "superstate-ustb-nav-daily": (
        "c1e010cc60a305afc7fee20a00e688c703443958ebf74768e3a22192e34dc12c"
    ),
    "superstate-ustb-yield": (
        "cf2f70b97f368c51274ef82d0726fd82a3d2d65124482bb513ee808ef0ef375e"
    ),
}


class ReplayProvider:
    """Replay retained provider responses without making another model call."""

    provider_name = "HTTPProvider"

    def __init__(self, artifacts: dict[str, dict[str, object]]) -> None:
        self.artifacts = artifacts

    def propose_controls(self, evidence_excerpt, source_manifest, bindings):
        del evidence_excerpt, bindings
        artifact = self.artifacts[source_manifest.source_id]
        provenance = artifact["provenance"]
        return ProviderResponse(
            content=artifact["raw_output"],
            requested_model=provenance["requested_model_name"],
            returned_model=provenance["returned_model_name"],
            response_id=provenance["provider_response_id"],
            finish_reason="stop",
            endpoint=provenance["provider_endpoint"],
            raw_response=artifact["provider_response"],
        )


class FobxxProvider:
    provider_name = "FobxxProvider"

    def propose_controls(self, evidence_excerpt, source_manifest, bindings):
        controls = _fobxx_candidates(source_manifest.source_id, bindings)
        content = json.dumps({"controls": controls}, separators=(",", ":"))
        raw_response = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {"content": content, "role": "assistant"},
                    }
                ],
                "id": f"fixture-{source_manifest.source_id}",
                "model": "fixture",
            },
            separators=(",", ":"),
        )
        assert all(
            candidate["evidence_span"] in evidence_excerpt for candidate in controls
        )
        return ProviderResponse(
            content=content,
            requested_model="fixture",
            returned_model="fixture",
            response_id=f"fixture-{source_manifest.source_id}",
            finish_reason="stop",
            endpoint="urn:touchstone:fobxx-fixture-provider",
            raw_response=raw_response,
        )


def _candidate(
    bindings: dict[str, object],
    *,
    control_id: str,
    span: str,
    operator: str,
    expected_value: dict[str, object],
    grace_period: int = 0,
    confidence: float = 0.95,
) -> dict[str, object]:
    return {
        **bindings,
        "control_id": control_id,
        "control_version": 1,
        "subject": control_id,
        "evidence_span": span,
        "grace_period": grace_period,
        "comparison_operator": operator,
        "expected_value": expected_value,
        "compiler_confidence": confidence,
    }


def _fobxx_candidates(
    source_id: str, bindings: dict[str, object]
) -> list[dict[str, object]]:
    if source_id in {
        "franklin-fobxx-product-lookup",
        "sec-edgar-fobxx-submissions",
    }:
        return []
    if source_id == "franklin-fobxx-price-performance":
        return [
            _candidate(
                bindings,
                control_id="fobxx-issuer-row-fresh",
                span='"navdate":"2026-08-21"',
                operator="fresh_within",
                expected_value={"business_days": 2},
                grace_period=2,
            ),
            _candidate(
                bindings,
                control_id="fobxx-issuer-nav-peg",
                span='"navdate":"2026-08-21","navstd":"1.00000000"',
                operator="eq",
                expected_value={"field": "nav_std", "value": "1.00000000"},
            ),
            _candidate(
                bindings,
                control_id="fobxx-issuer-daily-floor",
                span='"dailyliquidassetratio":"63.7387"',
                operator="non_decreasing",
                expected_value={
                    "field": "daily_liquid_asset_ratio",
                    "value": "0.25",
                },
            ),
            _candidate(
                bindings,
                control_id="fobxx-issuer-weekly-floor",
                span='"weeklyliquidassetratio":"71.7012"',
                operator="non_decreasing",
                expected_value={
                    "field": "weekly_liquid_asset_ratio",
                    "value": "0.50",
                },
            ),
            _candidate(
                bindings,
                control_id="fobxx-nav-reconciliation",
                span='"navdate":"2026-07-31","navstd":"1.00000000"',
                operator="reconciles_with",
                expected_value={
                    "field": "nav_std",
                    "comparison_source_id": "sec-edgar-fobxx-nmfp3",
                    "comparison_field": "stable_price_per_share",
                    "tolerance": "0",
                },
            ),
            _candidate(
                bindings,
                control_id="fobxx-daily-reconciliation",
                span=(
                    '"navdate":"2026-07-31","navstd":"1.00000000",'
                    '"dailyliquidassetratio":"63.7420"'
                ),
                operator="reconciles_with",
                expected_value={
                    "field": "daily_liquid_asset_ratio",
                    "comparison_source_id": "sec-edgar-fobxx-nmfp3",
                    "comparison_field": "daily_percentage",
                    "tolerance": "0",
                },
            ),
            _candidate(
                bindings,
                control_id="fobxx-weekly-reconciliation",
                span=(
                    '"navdate":"2026-07-31","navstd":"1.00000000",'
                    '"dailyliquidassetratio":"63.7420",'
                    '"weeklyliquidassetratio":"73.4485"'
                ),
                operator="reconciles_with",
                expected_value={
                    "field": "weekly_liquid_asset_ratio",
                    "comparison_source_id": "sec-edgar-fobxx-nmfp3",
                    "comparison_field": "weekly_percentage",
                    "tolerance": "0",
                },
            ),
        ]
    return [
        _candidate(
            bindings,
            control_id="fobxx-sec-filing-fresh",
            span="<reportDate>2026-07-31</reportDate>",
            operator="fresh_within",
            expected_value={"business_days": 10},
            grace_period=10,
        ),
        _candidate(
            bindings,
            control_id="fobxx-sec-stable-price",
            span="<stablePricePerShare>1.0000</stablePricePerShare>",
            operator="eq",
            expected_value={"field": "stable_price_per_share", "value": "1.0000"},
        ),
        _candidate(
            bindings,
            control_id="fobxx-sec-daily-floor",
            span=("<percentageDailyLiquidAssets>0.6463</percentageDailyLiquidAssets>"),
            operator="non_decreasing",
            expected_value={"field": "daily_percentage", "value": "0.25"},
        ),
        _candidate(
            bindings,
            control_id="fobxx-sec-weekly-floor",
            span=(
                "<percentageWeeklyLiquidAssets>0.7305</percentageWeeklyLiquidAssets>"
            ),
            operator="non_decreasing",
            expected_value={"field": "weekly_percentage", "value": "0.50"},
        ),
        _candidate(
            bindings,
            control_id="fobxx-sec-stable-price-low-confidence",
            span="<stablePricePerShare>1.0000</stablePricePerShare>",
            operator="eq",
            expected_value={"field": "stable_price_per_share", "value": "1.0000"},
            confidence=0.79,
        ),
        _candidate(
            bindings,
            control_id="fobxx-sec-daily-floor-wrong-span",
            span=("<percentageDailyLiquidAssets>0.6742</percentageDailyLiquidAssets>"),
            operator="non_decreasing",
            expected_value={"field": "daily_percentage", "value": "0.25"},
        ),
    ]


def test_default_cli_selects_only_ustb(monkeypatch, tmp_path: Path) -> None:
    selected = []

    def compile_selected(**arguments):
        selected.append(arguments)
        return {}

    monkeypatch.setattr(compile_controls, "compile_all", compile_selected)

    assert compile_controls.main(["--fixtures", str(tmp_path)]) == 0
    assert selected[0]["asset"] is USTB


def test_cli_can_select_only_fobxx(monkeypatch, tmp_path: Path) -> None:
    selected = []

    def compile_selected(**arguments):
        selected.append(arguments)
        return {}

    monkeypatch.setattr(compile_controls, "compile_all", compile_selected)

    assert compile_controls.main(["--asset", "fobxx", "--fixtures", str(tmp_path)]) == 0
    assert selected[0]["asset"] is FOBXX


def test_ustb_replay_keeps_all_three_compilation_digests(
    monkeypatch, tmp_path: Path
) -> None:
    artifacts = {}
    for source_id, digest in USTB_DIGESTS.items():
        raw = (ROOT / "data" / "compilations" / f"{digest}.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        artifacts[source_id] = json.loads(raw)
    monkeypatch.setattr(
        compile_controls, "HTTPProvider", lambda timeout: ReplayProvider(artifacts)
    )
    monkeypatch.setattr(compile_controls, "COMPILATIONS", tmp_path / "compilations")

    digests = compile_controls.compile_all(asset=USTB, live=False, fixtures=FIXTURES)

    assert digests == USTB_DIGESTS
    for digest in digests.values():
        assert (
            hashlib.sha256(
                (tmp_path / "compilations" / f"{digest}.json").read_bytes()
            ).hexdigest()
            == digest
        )


def test_fobxx_compiles_all_retained_sources_with_strict_evidence_gates(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        compile_controls, "HTTPProvider", lambda timeout: FobxxProvider()
    )
    monkeypatch.setattr(compile_controls, "COMPILATIONS", tmp_path / "compilations")

    digests = compile_controls.compile_all(asset=FOBXX, live=False, fixtures=FIXTURES)

    assert list(digests) == [source.source_id for source in FOBXX.sources]
    records = {
        source_id: json.loads(
            (tmp_path / "compilations" / f"{digest}.json").read_bytes()
        )
        for source_id, digest in digests.items()
    }
    history = records["franklin-fobxx-price-performance"]
    history_status = {
        outcome["control"]["control_id"]: outcome["status"]
        for outcome in history["outcomes"]
        if outcome["control"] is not None
    }
    assert history_status == {
        "fobxx-issuer-row-fresh": "accepted",
        "fobxx-issuer-nav-peg": "accepted",
        "fobxx-nav-reconciliation": "accepted",
        "fobxx-daily-reconciliation": "accepted",
        "fobxx-weekly-reconciliation": "accepted",
    }
    rejected = [
        outcome["reason"]
        for outcome in history["outcomes"]
        if outcome["status"] == "rejected"
    ]
    assert rejected == [
        "latest FOBXX issuer daily liquidity ratio is blank",
        "latest FOBXX issuer weekly liquidity ratio is blank",
    ]
    assert {item["control_id"] for item in history["comparison_evidence"]} == {
        "fobxx-nav-reconciliation",
        "fobxx-daily-reconciliation",
        "fobxx-weekly-reconciliation",
    }
    assert all(
        item["source_id"] == "sec-edgar-fobxx-nmfp3"
        and item["sha256"]
        == records["sec-edgar-fobxx-nmfp3"]["provenance"]["input_evidence_sha256"]
        for item in history["comparison_evidence"]
    )
    comparison_raw = (FIXTURES / "fobxx-nmfp3-20260731.xml").read_bytes()
    for item in history["comparison_evidence"]:
        assert all(
            span.encode("utf-8") in comparison_raw for span in item["evidence_spans"]
        )
        if item["control_id"] in {
            "fobxx-daily-reconciliation",
            "fobxx-weekly-reconciliation",
        }:
            assert (
                "<totalLiquidAssetsNearPercentDate>2026-07-31"
                in item["evidence_spans"][1]
            )
    sec = records["sec-edgar-fobxx-nmfp3"]
    assert [
        outcome["control"]["control_id"]
        for outcome in sec["outcomes"]
        if outcome["status"] == "accepted"
    ] == [
        "fobxx-sec-filing-fresh",
        "fobxx-sec-stable-price",
        "fobxx-sec-daily-floor",
        "fobxx-sec-weekly-floor",
    ]
    assert sec["outcomes"][-1]["status"] == "rejected"
    assert "series minimum" in sec["outcomes"][-1]["reason"]
    assert any(
        outcome["status"] == "abstained" and "below 0.8" in outcome["reason"]
        for outcome in sec["outcomes"]
    )


def test_equivalent_fobxx_descriptor_cannot_bypass_strict_evidence_gates(
    tmp_path: Path,
) -> None:
    raw = (FIXTURES / "fobxx-price-history-90d-20260822.json").read_bytes()
    source = FOBXX.sources[1]
    retrieved_at = datetime(2026, 8, 22, 3, 4, 44, 564845, tzinfo=timezone.utc)
    store = EvidenceStore(tmp_path)
    digest = store.store(
        raw,
        source_id=source.source_id,
        source_url=source.url,
        retrieved_at=retrieved_at,
        declared_mime=source.expected_mime,
    )
    bindings = {
        "asset_key": FOBXX.asset_key,
        "source_id": source.source_id,
        "source_authority_class": source.authority_class,
        "cadence": source.cadence,
        "observation_adapter": FOBXX.adapters[source.source_id],
        "predicate_type": "observation",
        "approval_state": "proposed",
        "effective_from": "2026-08-22",
        "effective_until": None,
    }
    candidate = _candidate(
        bindings,
        control_id="fobxx-issuer-daily-floor",
        span='"dailyliquidassetratio":"63.7387"',
        operator="non_decreasing",
        expected_value={"field": "daily_liquid_asset_ratio", "value": "0.25"},
    )

    result = compile_evidence(
        DeterministicFixtureProvider(
            json.dumps({"controls": [candidate]}, separators=(",", ":"))
        ),
        evidence_sha256=digest,
        source_manifest=source,
        store=store,
        retrieved_at=retrieved_at,
        asset=replace(FOBXX),
    )

    assert result.outcomes[0].status is CompilationStatus.REJECTED
    assert result.outcomes[0].reason == (
        "latest FOBXX issuer daily liquidity ratio is blank"
    )


def test_repeated_liquidity_value_binds_only_the_period_end_row() -> None:
    raw = (
        (FIXTURES / "fobxx-nmfp3-20260731.xml")
        .read_bytes()
        .replace(
            b"<percentageDailyLiquidAssets>0.6742</percentageDailyLiquidAssets>",
            b"<percentageDailyLiquidAssets>0.6528</percentageDailyLiquidAssets>",
            1,
        )
    )
    filing = FOBXX.normalize(
        "sec-edgar-fobxx-nmfp3",
        raw,
        max_bytes=FOBXX.source_by_id["sec-edgar-fobxx-nmfp3"].max_bytes,
    )

    span = _fobxx_comparison_span(filing, "daily_percentage", "2026-07-31", raw)

    assert span.startswith(
        "<percentageDailyLiquidAssets>0.6528</percentageDailyLiquidAssets>"
    )
    assert "<totalLiquidAssetsNearPercentDate>2026-07-31" in span
    assert "</liquidAssetsDetails>" not in span


def test_invalid_asset_selector_is_refused() -> None:
    with pytest.raises(SystemExit, match="2"):
        compile_controls.main(["--asset", "unknown"])
