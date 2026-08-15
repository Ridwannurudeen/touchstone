"""Compare a confirmed API row against the issuer's own onchain oracle.

This is the portfolio's only genuinely independent cross-check: two publications by the
same issuer, one over HTTPS and one onchain, that must agree. It exists to catch a
disagreement between them, not to price the asset.

Three rules shape everything here.

Every read pins an explicit block. Two endpoints can answer from different heights, so a
comparison across an unpinned "latest" is not reproducible and is not evidence.

Chain, address and decimals are verified rather than assumed. The same address is reused
across chains by this issuer, so an unverified read could be answered by a different
contract entirely.

The comparison is only ever made against a **confirmed** row whose date matches the
oracle's own update, within a stated tolerance. Comparing against the feed's provisional
tail would manufacture a disagreement out of a value the issuer has not settled, and a
disagreement is the one thing this check is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from touchstone.normalize.ustb import USTBNavRow


DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 65_536

# Chainlink-compatible USTB oracle, named in Superstate's own documentation. The pair
# (chain, address) is the identity; the address alone is not.
USTB_ORACLE_CHAIN_ID = 1
USTB_ORACLE_ADDRESS = "0x289B5036cd942e619E1Ee48670F98d214E745AAC"
USTB_ORACLE_EXPECTED_DECIMALS = 6

# Chainlink AggregatorV3 selectors.
_SELECTOR_DECIMALS = "0x313ce567"
_SELECTOR_LATEST_ROUND_DATA = "0xfeaf968c"
_SELECTOR_CHAIN_ID = "eth_chainId"


class OracleError(RuntimeError):
    """A typed failure that must never be reported as an asset inconsistency."""


class OracleUnavailable(OracleError):
    """The endpoint could not be reached or would not answer."""


class OracleIdentityError(OracleError):
    """The endpoint answered, but not as the contract this check expects."""


class RPC(Protocol):
    """Injectable JSON-RPC boundary."""

    def call(self, method: str, params: list[object]) -> object:
        """Issue one JSON-RPC call and return its result."""
        ...


@dataclass(frozen=True, slots=True)
class OracleReading:
    """One pinned, identity-verified oracle observation."""

    chain_id: int
    address: str
    block_number: int
    decimals: int
    answer: Decimal
    updated_at: datetime

    @property
    def updated_on(self) -> date:
        return self.updated_at.date()


@dataclass(frozen=True, slots=True)
class OracleComparison:
    """The result of comparing a confirmed row against a pinned oracle reading."""

    agrees: bool
    row_observed_on: date
    row_value: Decimal
    oracle_value: Decimal
    difference: Decimal
    tolerance: Decimal
    reading: OracleReading


class HTTPRPC:
    """Minimal JSON-RPC client with a bounded response."""

    def __init__(self, endpoint: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ValueError("RPC endpoint must be an HTTPS URL")
        self.endpoint = endpoint
        self.timeout = float(timeout)

    def call(self, method: str, params: list[object]) -> object:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "touchstone/0.1.0",
            },
        )
        try:
            with build_opener().open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise OracleUnavailable(f"RPC call {method} failed: {error}") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OracleUnavailable(f"RPC response for {method} exceeds the byte limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OracleUnavailable(f"RPC response for {method} is not JSON") from error
        if not isinstance(payload, dict) or "result" not in payload:
            raise OracleUnavailable(f"RPC response for {method} carries no result")
        return payload["result"]


def _hex_to_int(value: object, context: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise OracleUnavailable(f"{context} is not a hex quantity")
    try:
        return int(value, 16)
    except ValueError as error:
        raise OracleUnavailable(f"{context} is not a hex quantity") from error


def _signed(word: int) -> int:
    """Interpret a 256-bit word as two's-complement, as int256 answers are signed."""
    return word - (1 << 256) if word >= (1 << 255) else word


def read_ustb_oracle(
    rpc: RPC,
    *,
    block_number: int | None = None,
    address: str = USTB_ORACLE_ADDRESS,
    expected_chain_id: int = USTB_ORACLE_CHAIN_ID,
    expected_decimals: int = USTB_ORACLE_EXPECTED_DECIMALS,
) -> OracleReading:
    """Read the oracle at a pinned block, verifying identity before trusting the answer."""
    chain_id = _hex_to_int(rpc.call(_SELECTOR_CHAIN_ID, []), "chain id")
    if chain_id != expected_chain_id:
        raise OracleIdentityError(
            f"endpoint reports chain {chain_id}, expected {expected_chain_id}"
        )

    if block_number is None:
        block_number = _hex_to_int(rpc.call("eth_blockNumber", []), "block number")
    block = hex(block_number)

    code = rpc.call("eth_getCode", [address, block])
    if not isinstance(code, str) or len(code) <= 2:
        raise OracleIdentityError(
            f"no contract code at {address} in block {block_number}"
        )

    decimals_raw = rpc.call(
        "eth_call", [{"to": address, "data": _SELECTOR_DECIMALS}, block]
    )
    decimals = _hex_to_int(decimals_raw, "decimals")
    if decimals != expected_decimals:
        raise OracleIdentityError(
            f"oracle reports {decimals} decimals, expected {expected_decimals}"
        )

    answer_raw = rpc.call(
        "eth_call", [{"to": address, "data": _SELECTOR_LATEST_ROUND_DATA}, block]
    )
    if not isinstance(answer_raw, str) or len(answer_raw) < 2 + 64 * 5:
        raise OracleUnavailable("latestRoundData returned an unexpected payload")
    words = answer_raw[2:]
    answer_word = int(words[64:128], 16)
    updated_at_word = int(words[192:256], 16)
    answer = _signed(answer_word)
    if answer <= 0:
        raise OracleUnavailable("oracle answer is not positive")
    if updated_at_word == 0:
        raise OracleUnavailable("oracle round has no update timestamp")

    return OracleReading(
        chain_id=chain_id,
        address=address,
        block_number=block_number,
        decimals=decimals,
        answer=Decimal(answer) / (Decimal(10) ** decimals),
        updated_at=datetime.fromtimestamp(updated_at_word, timezone.utc),
    )


def compare_confirmed_row(
    row: USTBNavRow | None,
    reading: OracleReading,
    *,
    tolerance: Decimal,
) -> OracleComparison:
    """Compare a confirmed row against a reading whose update date matches it.

    ``row`` is the row the evaluator confirmed across two captures — never the feed's
    provisional tail. Passing ``None`` means nothing was confirmed, which is an abstention
    and not a disagreement.
    """
    if row is None:
        raise OracleUnavailable("no confirmed row is available to compare")
    if not isinstance(tolerance, Decimal) or tolerance < 0:
        raise ValueError("tolerance must be a non-negative Decimal")
    if row.observed_on != reading.updated_on:
        raise OracleUnavailable(
            f"confirmed row is dated {row.observed_on} but the oracle updated on "
            f"{reading.updated_on}; there is nothing comparable"
        )

    difference = abs(row.net_asset_value - reading.answer)
    return OracleComparison(
        agrees=difference <= tolerance,
        row_observed_on=row.observed_on,
        row_value=row.net_asset_value,
        oracle_value=reading.answer,
        difference=difference,
        tolerance=tolerance,
        reading=reading,
    )
