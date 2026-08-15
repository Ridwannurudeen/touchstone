"""The oracle check must refuse to compare rather than invent a disagreement."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from touchstone.normalize.ustb import USTBNavRow
from touchstone.oracles import (
    USTB_ORACLE_ADDRESS,
    HTTPRPC,
    OracleIdentityError,
    OracleReading,
    OracleUnavailable,
    compare_confirmed_row,
    read_ustb_oracle,
)


# The audit records a publication on 08-12 carrying the NAV effective 08/11, so the
# fixture deliberately puts publication and effective date on different days.
PUBLISHED_AT = int(datetime(2026, 8, 12, 13, 13, 0, tzinfo=timezone.utc).timestamp())
UPDATED_AT = PUBLISHED_AT
EFFECTIVE_DATE = date(2026, 8, 11)


def word(value: int) -> str:
    return f"{value & ((1 << 256) - 1):064x}"


def round_data(answer: int, updated_at: int = UPDATED_AT) -> str:
    """roundId, answer, startedAt, updatedAt, answeredInRound."""
    return "0x" + word(1) + word(answer) + word(updated_at) + word(updated_at) + word(1)


class FakeRPC:
    """Scripted JSON-RPC endpoint; every call is recorded so pinning can be asserted."""

    def __init__(self, **overrides: object) -> None:
        self.responses: dict[str, object] = {
            "eth_chainId": "0x1",
            "eth_blockNumber": "0x1889f6f",
            "eth_getCode": "0x60806040",
            "decimals": f"0x{6:064x}",
            "latestRoundData": round_data(11_175_588),
        }
        self.responses.update(overrides)
        self.calls: list[tuple[str, list[object]]] = []

    def call(self, method: str, params: list[object]) -> object:
        self.calls.append((method, params))
        if method != "eth_call":
            return self.responses[method]
        data = params[0]["data"]
        if data == "0x313ce567":
            return self.responses["decimals"]
        return self.responses["latestRoundData"]


def row(observed_on: date, nav: str) -> USTBNavRow:
    return USTBNavRow(
        fund_id=1,
        observed_on=observed_on,
        net_asset_value=Decimal(nav),
        subscription_nav_per_share=None,
        assets_under_management=Decimal("958406746.9500"),
        outstanding_shares=Decimal("85758954.871099"),
        net_income_expenses=Decimal("92362.32361600"),
    )


def test_a_reading_pins_a_block_and_verifies_identity() -> None:
    rpc = FakeRPC()

    reading = read_ustb_oracle(rpc, block_number=25_756_018)

    assert reading.chain_id == 1
    assert reading.address == USTB_ORACLE_ADDRESS
    assert reading.block_number == 25_756_018
    assert reading.decimals == 6
    assert reading.answer == Decimal("11.175588")
    assert reading.updated_on == date(2026, 8, 12)
    pinned = [params for method, params in rpc.calls if method == "eth_call"]
    assert pinned and all(params[1] == hex(25_756_018) for params in pinned), (
        "every contract read must be pinned to the same block"
    )
    assert all(
        params[1] == hex(25_756_018)
        for method, params in rpc.calls
        if method == "eth_getCode"
    )


def test_a_wrong_chain_is_refused_before_any_value_is_read() -> None:
    rpc = FakeRPC(eth_chainId="0x89")

    with pytest.raises(OracleIdentityError, match="chain 137"):
        read_ustb_oracle(rpc, block_number=1)
    assert not [method for method, _ in rpc.calls if method == "eth_call"]


def test_an_address_with_no_code_is_refused() -> None:
    rpc = FakeRPC(eth_getCode="0x")

    with pytest.raises(OracleIdentityError, match="no contract bytecode"):
        read_ustb_oracle(rpc, block_number=1)


def test_unexpected_decimals_are_refused() -> None:
    """Decimals differ across this issuer's contracts, so a mismatch means wrong contract."""
    rpc = FakeRPC(decimals=f"0x{18:064x}")

    with pytest.raises(OracleIdentityError, match="18 decimals"):
        read_ustb_oracle(rpc, block_number=1)


@pytest.mark.parametrize(
    "payload", [round_data(0), round_data(-1), round_data(11_175_588, updated_at=0)]
)
def test_an_unusable_round_is_unavailable_not_a_disagreement(payload: str) -> None:
    rpc = FakeRPC(latestRoundData=payload)

    with pytest.raises(OracleUnavailable):
        read_ustb_oracle(rpc, block_number=1)


def test_agreement_within_tolerance() -> None:
    reading = read_ustb_oracle(FakeRPC(), block_number=1)

    comparison = compare_confirmed_row(
        row(EFFECTIVE_DATE, "11.17558800"),
        reading,
        tolerance=Decimal("0.000001"),
        effective_date=EFFECTIVE_DATE,
    )

    assert comparison.agrees
    assert comparison.difference == Decimal("0")
    assert comparison.oracle_value == Decimal("11.175588")


def test_a_real_disagreement_is_reported() -> None:
    reading = read_ustb_oracle(FakeRPC(), block_number=1)

    comparison = compare_confirmed_row(
        row(EFFECTIVE_DATE, "11.99999999"),
        reading,
        tolerance=Decimal("0.000001"),
        effective_date=EFFECTIVE_DATE,
    )

    assert not comparison.agrees
    assert comparison.difference > comparison.tolerance


def test_a_date_mismatch_refuses_to_compare() -> None:
    """The oracle updates on its own schedule; comparing across dates invents a conflict."""
    reading = read_ustb_oracle(FakeRPC(), block_number=1)

    with pytest.raises(OracleUnavailable, match="nothing comparable"):
        compare_confirmed_row(
            row(date(2026, 8, 13), "11.17774800"),
            reading,
            tolerance=Decimal("0.01"),
            effective_date=EFFECTIVE_DATE,
        )


def test_no_confirmed_row_is_an_abstention_not_a_disagreement() -> None:
    reading = read_ustb_oracle(FakeRPC(), block_number=1)

    with pytest.raises(OracleUnavailable, match="no confirmed row"):
        compare_confirmed_row(
            None, reading, tolerance=Decimal("0.01"), effective_date=date(2026, 8, 11)
        )


def test_tolerance_must_be_a_non_negative_decimal() -> None:
    reading = read_ustb_oracle(FakeRPC(), block_number=1)

    for bad in (Decimal("-0.01"), 0.01, "0.01"):
        with pytest.raises(ValueError, match="tolerance"):
            compare_confirmed_row(
                row(EFFECTIVE_DATE, "11.17"),
                reading,
                tolerance=bad,
                effective_date=EFFECTIVE_DATE,
            )


def test_the_rpc_client_requires_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HTTPRPC("http://rpc.example.com")


def test_a_reading_is_a_frozen_record() -> None:
    reading = OracleReading(
        chain_id=1,
        address=USTB_ORACLE_ADDRESS,
        block_number=1,
        decimals=6,
        answer=Decimal("11.175588"),
        updated_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    with pytest.raises(Exception):
        reading.answer = Decimal("0")  # type: ignore[misc]


def test_publication_date_and_effective_date_differ_and_only_the_stated_one_counts() -> None:
    """The audit records an 08-12 publication carrying the 08/11 NAV.

    The fixture reproduces that gap: the reading publishes on 08-12, and the comparison
    succeeds against a row dated 08/11 **because the caller states 08/11**, not because
    anything inferred it. Had the module used the publication date, this would fail.
    """
    reading = read_ustb_oracle(FakeRPC(), block_number=1)
    assert reading.updated_on == date(2026, 8, 12)
    assert EFFECTIVE_DATE == date(2026, 8, 11)

    comparison = compare_confirmed_row(
        row(EFFECTIVE_DATE, "11.17558800"),
        reading,
        tolerance=Decimal("0.000001"),
        effective_date=EFFECTIVE_DATE,
    )

    assert comparison.agrees
    assert comparison.effective_date != reading.updated_on

    # And a wrongly stated effective date is still refused.
    with pytest.raises(OracleUnavailable, match="nothing comparable"):
        compare_confirmed_row(
            row(EFFECTIVE_DATE, "11.17558800"),
            reading,
            tolerance=Decimal("0.01"),
            effective_date=date(2026, 8, 10),
        )


def test_an_effective_date_must_be_supplied() -> None:
    reading = read_ustb_oracle(FakeRPC(), block_number=1)

    with pytest.raises(TypeError):
        compare_confirmed_row(
            row(EFFECTIVE_DATE, "11.17"), reading, tolerance=Decimal("0.01")
        )


def test_a_different_address_is_refused_even_if_it_has_code() -> None:
    with pytest.raises(OracleIdentityError, match="is not the pinned USTB oracle"):
        read_ustb_oracle(
            FakeRPC(), block_number=1, address="0x0000000000000000000000000000000000000001"
        )


def test_non_hex_code_is_refused() -> None:
    with pytest.raises(OracleIdentityError, match="no contract bytecode"):
        read_ustb_oracle(FakeRPC(eth_getCode="0xnothexatall"), block_number=1)


def test_the_pinned_identity_cannot_be_relaxed_by_the_caller() -> None:
    """An earlier version took expectations as arguments, so declaring chain 137 with 18
    decimals produced a valid-looking reading from the wrong contract."""
    import inspect

    signature = inspect.signature(read_ustb_oracle)

    assert "expected_chain_id" not in signature.parameters
    assert "expected_decimals" not in signature.parameters
    for bad in (
        {"eth_chainId": "0x89"},
        {"decimals": f"0x{18:064x}"},
        {"eth_getCode": "0x0"},
        {"eth_getCode": "0x123"},
    ):
        with pytest.raises(OracleIdentityError):
            read_ustb_oracle(FakeRPC(**bad), block_number=1)


def test_the_committed_transcript_matches_the_pinned_identity() -> None:
    """The transcript proves the reading only; it asserts nothing about the NAV date."""
    import json
    from pathlib import Path

    transcript = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "ustb-oracle-transcript.json").read_text(
            encoding="utf-8"
        )
    )

    assert transcript["chain_id"] == "0x1"
    assert transcript["address"] == USTB_ORACLE_ADDRESS
    assert transcript["block_hash"].startswith("0x") and len(transcript["block_hash"]) == 66
    assert int(transcript["calls"]["decimals"]["result"], 16) == 6
    assert transcript["code_bytes"] > 0
    assert "NAV date" in transcript["_note"]
