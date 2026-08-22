"""The registry truth gate is deterministic in tests and fail-closed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_onchain_truth", ROOT / "scripts" / "check_onchain_truth.py"
)
assert SPEC and SPEC.loader
check_onchain_truth = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_onchain_truth
SPEC.loader.exec_module(check_onchain_truth)

FIXTURES = json.loads(
    Path(__file__)
    .with_name("onchain_truth_fixtures.json")
    .read_text(encoding="utf-8")
)
KEY = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"


def _encoded_report(sequence: int, status: int, uri: str = "ipfs://report") -> str:
    def word(value: int) -> bytes:
        return value.to_bytes(32, "big")

    text = uri.encode("utf-8")
    padding = b"\x00" * ((32 - len(text) % 32) % 32)
    payload = b"".join(
        (
            word(32),
            b"\x11" * 32,
            b"\x22" * 32,
            b"\x33" * 32,
            word(status),
            word(1_700_000_000),
            word(1_700_086_400),
            word(0x1234),
            word(sequence),
            word(288),
            word(len(text)),
            text,
            padding,
        )
    )
    return "0x" + payload.hex()


class FixtureRPC:
    def __init__(self, chain_id: int, head: int, report: dict[str, int]) -> None:
        self.chain_id = chain_id
        self.head = head
        self.report = report
        self.calls: list[tuple[str, list[object]]] = []

    def call(self, method: str, params: list[object]) -> object:
        self.calls.append((method, params))
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(self.head)
        assert method == "eth_call"
        return _encoded_report(**self.report)


def _truth(payload: dict[str, object]) -> check_onchain_truth.PublicationTruth:
    return check_onchain_truth.PublicationTruth(
        chain_id=196,
        asset_key=KEY,
        sequence=int(payload["sequence"]),
        status=str(payload["status"]),
    )


def test_two_endpoints_agree_at_the_lower_pinned_head() -> None:
    fixture = FIXTURES["agreement"]
    readers = [
        FixtureRPC(196, head, report)
        for head, report in zip(fixture["heads"], fixture["endpoint_reports"])
    ]
    network = check_onchain_truth.Network(
        name="X Layer mainnet",
        chain_id=196,
        registry="0x1111111111111111111111111111111111111111",
        endpoints=("https://one.example", "https://two.example"),
    )

    readings = check_onchain_truth.read_network(network, [KEY], readers=readers)

    assert readings == [
        check_onchain_truth.ChainTruth(
            chain_id=196,
            asset_key=KEY,
            sequence=3,
            status="CONFIRMED",
            block_number=120,
        )
    ]
    for reader in readers:
        assert reader.calls[-1][1][-1] == "0x78"


def test_equal_sequence_status_disagreement_fails() -> None:
    fixture = FIXTURES["status_disagreement"]
    failures = check_onchain_truth.compare_truth(
        [_truth(fixture["site"])], [_truth(fixture["chain"])], max_chain_ahead=3
    ).failures

    assert failures == [
        "chain 196 key " + KEY + ": sequence 3 status site=STALE chain=CONFIRMED"
    ]


def test_site_claiming_more_than_chain_fails() -> None:
    fixture = FIXTURES["site_claims_more"]
    result = check_onchain_truth.compare_truth(
        [_truth(fixture["site"])], [_truth(fixture["chain"])], max_chain_ahead=3
    )

    assert result.failures == [
        "chain 196 key " + KEY + ": FATAL site claims sequence 4, chain proves only 3"
    ]


def test_chain_ahead_within_declared_lag_passes() -> None:
    fixture = FIXTURES["chain_ahead_within_lag"]
    result = check_onchain_truth.compare_truth(
        [_truth(fixture["site"])], [_truth(fixture["chain"])], max_chain_ahead=3
    )

    assert result.failures == []
    assert result.chain_ahead == 1


def test_endpoint_disagreement_fails() -> None:
    fixture = FIXTURES["endpoint_disagreement"]
    readers = [
        FixtureRPC(196, head, report)
        for head, report in zip(fixture["heads"], fixture["endpoint_reports"])
    ]
    network = check_onchain_truth.Network(
        name="X Layer mainnet",
        chain_id=196,
        registry="0x1111111111111111111111111111111111111111",
        endpoints=("https://one.example", "https://two.example"),
    )

    with pytest.raises(check_onchain_truth.OnchainTruthError, match="DISAGREE"):
        check_onchain_truth.read_network(network, [KEY], readers=readers)


@pytest.mark.parametrize("payload", [FIXTURES["short_return"], "0xzz"])
def test_malformed_or_short_return_data_fails(payload: str) -> None:
    with pytest.raises(check_onchain_truth.OnchainTruthError, match="return data"):
        check_onchain_truth.decode_latest_report(payload)


def test_committed_stats_identify_every_publication_and_all_eight_histories() -> None:
    claims = check_onchain_truth.load_publication_claims(
        ROOT / "site2" / "data" / "stats.json"
    )

    assert len(claims) == 8
    assert sum(claim.sequence for claim in claims) == 25


def test_offline_skips_every_file_and_network_read(capsys) -> None:
    assert check_onchain_truth.main(["--offline", "--stats", "missing.json"]) == 0
    assert "no registry claim was verified" in capsys.readouterr().out
