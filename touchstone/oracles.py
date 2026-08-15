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

The comparison is only ever made against a **confirmed** row, for a NAV date the caller
states explicitly, within a stated tolerance. Comparing against the feed's provisional tail
would manufacture a disagreement out of a value the issuer has not settled, and the mapping
from an oracle publication time to the NAV date it represents is not documented — the audit
records a publication on 08-12 carrying the 08/11 NAV — so this module refuses to infer it.
A disagreement is the one thing this check exists to detect, so it must not invent one.
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
from touchstone.quantities import finite_positive


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
    effective_date: date
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
        # A timeout of NaN or infinity is not a long timeout — it is the absence of one,
        # and urlopen turns it into a socket that waits forever. Refusing it here means the
        # daemon stops at configuration rather than hanging in the middle of an epoch.
        self.timeout = finite_positive(timeout, "timeout")

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
    address: str | None = None,
) -> OracleReading:
    """Read the oracle at a pinned block, verifying identity before trusting the answer.

    The identity is pinned in this module and is **not** a parameter. Chain id, address
    and decimals are compared against the constants above, so a caller cannot relax the
    check by declaring what it expects to find — an earlier version took those as
    arguments, and supplying chain 137 with 18 decimals produced a perfectly valid-looking
    reading from the wrong contract.
    """
    if address is None:
        address = USTB_ORACLE_ADDRESS
    elif address.lower() != USTB_ORACLE_ADDRESS.lower():
        raise OracleIdentityError(
            f"{address} is not the pinned USTB oracle {USTB_ORACLE_ADDRESS}"
        )
    chain_id = _hex_to_int(rpc.call(_SELECTOR_CHAIN_ID, []), "chain id")
    if chain_id != USTB_ORACLE_CHAIN_ID:
        raise OracleIdentityError(
            f"endpoint reports chain {chain_id}, expected {USTB_ORACLE_CHAIN_ID}"
        )

    if block_number is None:
        block_number = _hex_to_int(rpc.call("eth_blockNumber", []), "block number")
    # A caller-supplied block was sent to the endpoint unexamined. `bool` is an `int`
    # subclass, so `True` travelled as block 1 and was then stored as `True` in the
    # reading; a negative number was formatted as "-0x1", which is not a quantity any
    # node will answer. Both produce a reading that names a block nobody read.
    elif type(block_number) is not int or block_number < 0:
        raise ValueError("block_number must be a non-negative integer")
    block = hex(block_number)

    code = rpc.call("eth_getCode", [address, block])
    if (
        not isinstance(code, str)
        or not code.startswith("0x")
        or len(code) <= 2
        or len(code) % 2 != 0
        or any(character not in "0123456789abcdefABCDEF" for character in code[2:])
        or int(code, 16) == 0
    ):
        raise OracleIdentityError(
            f"no contract bytecode at {address} in block {block_number}"
        )

    decimals_raw = rpc.call(
        "eth_call", [{"to": address, "data": _SELECTOR_DECIMALS}, block]
    )
    decimals = _hex_to_int(decimals_raw, "decimals")
    if decimals != USTB_ORACLE_EXPECTED_DECIMALS:
        raise OracleIdentityError(
            f"oracle reports {decimals} decimals, expected "
            f"{USTB_ORACLE_EXPECTED_DECIMALS}"
        )

    answer_raw = rpc.call(
        "eth_call", [{"to": address, "data": _SELECTOR_LATEST_ROUND_DATA}, block]
    )
    # Length alone let a malformed payload through to `int(..., 16)`, which raises a bare
    # ValueError, and a round-data word too large to be an instant reached
    # `datetime.fromtimestamp`, which refuses it differently per platform — OSError here,
    # OverflowError or ValueError elsewhere. None of those is this module's typed failure,
    # so a caller catching OracleUnavailable saw the process die instead.
    if (
        not isinstance(answer_raw, str)
        or not answer_raw.startswith("0x")
        or len(answer_raw) != 2 + 64 * 5
        or any(
            character not in "0123456789abcdefABCDEF" for character in answer_raw[2:]
        )
    ):
        raise OracleUnavailable("latestRoundData returned an unexpected payload")
    words = answer_raw[2:]
    answer_word = int(words[64:128], 16)
    updated_at_word = int(words[192:256], 16)
    answer = _signed(answer_word)
    if answer <= 0:
        raise OracleUnavailable("oracle answer is not positive")
    if updated_at_word == 0:
        raise OracleUnavailable("oracle round has no update timestamp")
    try:
        updated_at = datetime.fromtimestamp(updated_at_word, timezone.utc)
    except (OSError, OverflowError, ValueError) as error:
        raise OracleUnavailable(
            f"oracle round timestamp {updated_at_word} is not a representable instant"
        ) from error

    return OracleReading(
        chain_id=chain_id,
        address=address,
        block_number=block_number,
        decimals=decimals,
        answer=Decimal(answer) / (Decimal(10) ** decimals),
        updated_at=updated_at,
    )


def compare_confirmed_row(
    row: USTBNavRow | None,
    reading: OracleReading,
    *,
    tolerance: Decimal,
    effective_date: date,
) -> OracleComparison:
    """Compare a confirmed row against the NAV date a reading is stated to represent.

    ``row`` is the row the evaluator confirmed across two captures — never the feed's
    provisional tail. ``None`` means nothing was confirmed, which is an abstention rather
    than a disagreement.

    ``effective_date`` must be supplied and is deliberately **not** derived from the
    reading's publication time. The two differ: ``SOURCE_AUDIT.md`` records an oracle
    publication on 08-12 carrying the NAV effective 08/11. That mapping is not documented
    by the issuer, so this module refuses to infer it. The caller states which NAV date a
    reading represents and this function checks the row matches. Until the mapping is
    established from issuer documentation no caller can state it honestly, which is why
    this comparison has no production caller yet.
    """
    if row is None:
        raise OracleUnavailable("no confirmed row is available to compare")
    # `is_finite()` before the comparison, and before any ordering is attempted on it.
    # An infinite tolerance is not a lenient bound, it is the absence of one: every
    # finite difference satisfies it, so the control reports agreement without ever
    # comparing anything. NaN is worse — it fails `< 0` by raising InvalidOperation,
    # which is not this function's refusal.
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite():
        raise ValueError("tolerance must be a finite Decimal")
    if tolerance < 0:
        raise ValueError("tolerance must be a non-negative Decimal")
    if type(effective_date) is not date:
        raise ValueError("effective_date must be a date")
    if row.observed_on != effective_date:
        raise OracleUnavailable(
            f"confirmed row is dated {row.observed_on} but the reading is stated to "
            f"represent {effective_date}; there is nothing comparable"
        )

    difference = abs(row.net_asset_value - reading.answer)
    return OracleComparison(
        agrees=difference <= tolerance,
        effective_date=effective_date,
        row_observed_on=row.observed_on,
        row_value=row.net_asset_value,
        oracle_value=reading.answer,
        difference=difference,
        tolerance=tolerance,
        reading=reading,
    )
