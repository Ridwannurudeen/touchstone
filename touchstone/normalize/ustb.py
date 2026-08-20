"""Strict normalization for the three allowlisted USTB JSON payloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import multiprocessing
from multiprocessing.connection import Connection
import re
from typing import TypeAlias

from touchstone.quantities import finite_positive


DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_MAX_DEPTH = 32
# The wall clock covers spawning a fresh interpreter, not only the parse — the spawn
# context re-imports everything — and on the production host (a shared box that has run
# at load ~20 on 8 cores) interpreter startup alone exceeded 2 seconds, so the first
# unattended mainnet slot failed with EPOCH_FAILED before any payload was read. The
# guard exists to bound a hostile payload's parse; 20 seconds bounds it just as firmly,
# and an honest slot on a busy host is not the thing it exists to refuse.
DEFAULT_ISOLATED_TIMEOUT = 20.0
# A terminated worker that ignores SIGTERM must not block the epoch either.
_JOIN_GRACE_SECONDS = 5.0

USTB_NAV_SOURCE_ID = "superstate-ustb-nav-daily"
USTB_YIELD_SOURCE_ID = "superstate-ustb-yield"
USTB_HOLDINGS_SOURCE_ID = "superstate-ustb-holdings"

_NAV_FIELDS = frozenset(
    {
        "fund_id",
        "net_asset_value_date",
        "net_asset_value",
        "subscription_nav_per_share",
        "assets_under_management",
        "outstanding_shares",
        "net_income_expenses",
    }
)
_YIELD_FIELDS = frozenset({"as_of_date", "thirty_day", "seven_day", "one_day"})
_HOLDINGS_FIELDS = frozenset({"as_of_date", "holdings"})
_HOLDING_FIELDS = frozenset(
    {"Security Name", "Base Value/Cost", "Maturity", "Current Yld", "% of Fund"}
)
_DECIMAL_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_CURRENCY_TEXT = re.compile(
    r"\$(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?\Z"
)
_PERCENT_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?%\Z")
_MATURITY_TEXT = re.compile(r"([0-9]{1,2})-([A-Z][a-z]{2})-([0-9]{4})\Z")
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


class NormalizationError(ValueError):
    """A payload was rejected without producing a partial observation."""


@dataclass(frozen=True, slots=True)
class USTBNavRow:
    fund_id: int
    observed_on: date
    net_asset_value: Decimal
    subscription_nav_per_share: Decimal | None
    assets_under_management: Decimal
    outstanding_shares: Decimal | None
    net_income_expenses: Decimal


@dataclass(frozen=True, slots=True)
class USTBNavObservation:
    rows: tuple[USTBNavRow, ...]


@dataclass(frozen=True, slots=True)
class USTBYieldObservation:
    as_of_date: date
    thirty_day: Decimal
    seven_day: Decimal
    one_day: Decimal


@dataclass(frozen=True, slots=True)
class USTBHolding:
    security_name: str
    base_value_cost: Decimal
    maturity: date
    current_yield: Decimal
    percent_of_fund: Decimal


@dataclass(frozen=True, slots=True)
class USTBHoldingsObservation:
    as_of_date: date
    holdings: tuple[USTBHolding, ...]


USTBObservation: TypeAlias = (
    USTBNavObservation | USTBYieldObservation | USTBHoldingsObservation
)


def parse_nav_daily(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> USTBNavObservation:
    """Normalize the USTB nav-daily array atomically."""
    value = _decode_json(
        raw, max_bytes=max_bytes, max_depth=max_depth, expected_root=b"["
    )
    if not isinstance(value, list) or not value:
        raise NormalizationError("USTB nav-daily root must be a non-empty array")

    rows: list[USTBNavRow] = []
    seen_dates: set[date] = set()
    for index, item in enumerate(value):
        context = f"nav row {index}"
        record = _exact_object(item, _NAV_FIELDS, context)
        fund_id = record["fund_id"]
        if type(fund_id) is not int or fund_id != 1:
            raise NormalizationError(f"{context}.fund_id must be integer 1")
        observed_on = _parse_mmddyyyy(
            record["net_asset_value_date"], f"{context}.net_asset_value_date"
        )
        if observed_on in seen_dates:
            raise NormalizationError(
                f"{context}.net_asset_value_date repeats {observed_on.isoformat()}"
            )
        seen_dates.add(observed_on)
        subscription_value = record["subscription_nav_per_share"]
        if subscription_value is None:
            subscription_nav = None
        else:
            subscription_nav = _decimal_text(
                subscription_value, f"{context}.subscription_nav_per_share"
            )

        outstanding_value = record["outstanding_shares"]
        if outstanding_value == "":
            if observed_on != date(2024, 1, 3) or index != len(value) - 1:
                raise NormalizationError(
                    f"{context}.outstanding_shares has an invalid blank sentinel"
                )
            outstanding_shares = None
        else:
            outstanding_shares = _decimal_text(
                outstanding_value, f"{context}.outstanding_shares"
            )

        rows.append(
            USTBNavRow(
                fund_id=fund_id,
                observed_on=observed_on,
                net_asset_value=_decimal_text(
                    record["net_asset_value"], f"{context}.net_asset_value"
                ),
                subscription_nav_per_share=subscription_nav,
                assets_under_management=_decimal_text(
                    record["assets_under_management"],
                    f"{context}.assets_under_management",
                ),
                outstanding_shares=outstanding_shares,
                net_income_expenses=_decimal_text(
                    record["net_income_expenses"],
                    f"{context}.net_income_expenses",
                ),
            )
        )
    return USTBNavObservation(rows=tuple(rows))


def parse_yield(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> USTBYieldObservation:
    """Normalize the USTB yield object without binary-float conversion."""
    value = _decode_json(
        raw, max_bytes=max_bytes, max_depth=max_depth, expected_root=b"{"
    )
    record = _exact_object(value, _YIELD_FIELDS, "USTB yield root")
    return USTBYieldObservation(
        as_of_date=_parse_iso_date(record["as_of_date"], "yield.as_of_date"),
        thirty_day=_json_decimal(record["thirty_day"], "yield.thirty_day"),
        seven_day=_json_decimal(record["seven_day"], "yield.seven_day"),
        one_day=_json_decimal(record["one_day"], "yield.one_day"),
    )


def parse_holdings(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> USTBHoldingsObservation:
    """Normalize the USTB holdings object atomically."""
    value = _decode_json(
        raw, max_bytes=max_bytes, max_depth=max_depth, expected_root=b"{"
    )
    record = _exact_object(value, _HOLDINGS_FIELDS, "USTB holdings root")
    items = record["holdings"]
    if not isinstance(items, list) or not items:
        raise NormalizationError("holdings.holdings must be a non-empty array")

    holdings: list[USTBHolding] = []
    for index, item in enumerate(items):
        context = f"holding {index}"
        holding = _exact_object(item, _HOLDING_FIELDS, context)
        security_name = holding["Security Name"]
        if not isinstance(security_name, str) or not security_name.strip():
            raise NormalizationError(f"{context}.Security Name must be non-empty text")
        holdings.append(
            USTBHolding(
                security_name=security_name,
                base_value_cost=_currency_decimal(
                    holding["Base Value/Cost"], f"{context}.Base Value/Cost"
                ),
                maturity=_parse_maturity(holding["Maturity"], f"{context}.Maturity"),
                current_yield=_percent_decimal(
                    holding["Current Yld"], f"{context}.Current Yld"
                ),
                percent_of_fund=_percent_decimal(
                    holding["% of Fund"], f"{context}.% of Fund"
                ),
            )
        )
    return USTBHoldingsObservation(
        as_of_date=_parse_mmddyyyy(record["as_of_date"], "holdings.as_of_date"),
        holdings=tuple(holdings),
    )


def normalize_ustb_payload(
    source_id: str,
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    isolated: bool = False,
    timeout: float = DEFAULT_ISOLATED_TIMEOUT,
) -> USTBObservation:
    """Dispatch an exact USTB source ID to its strict parser."""
    if isolated:
        return normalize_ustb_payload_isolated(
            source_id,
            raw,
            max_bytes=max_bytes,
            max_depth=max_depth,
            timeout=timeout,
        )
    parser = _parser_for_source(source_id)
    return parser(raw, max_bytes=max_bytes, max_depth=max_depth)


def normalize_ustb_payload_isolated(
    source_id: str,
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    timeout: float = DEFAULT_ISOLATED_TIMEOUT,
) -> USTBObservation:
    """Normalize in a spawned worker with a hard wall-clock timeout."""
    _parser_for_source(source_id)
    content = _prepare_raw(raw, max_bytes=max_bytes, max_depth=max_depth)
    timeout = finite_positive(timeout, "timeout")

    context = multiprocessing.get_context("spawn")
    # Building and starting the worker is I/O like any other, and it can fail before the
    # worker exists: no file descriptors for the pipe, no process slots, a spawn refused.
    # Those escaped as OSError while every failure *inside* the worker was already this
    # module's own error, so the caller had to handle two vocabularies for one operation.
    try:
        receive, send = context.Pipe(duplex=False)
    except OSError as error:
        raise NormalizationError(
            f"USTB normalization worker could not be prepared: {error}"
        ) from error
    process = context.Process(
        target=_isolated_worker,
        args=(send, source_id, content, max_bytes, max_depth),
    )
    try:
        process.start()
    except OSError as error:
        receive.close()
        send.close()
        raise NormalizationError(
            f"USTB normalization worker could not be started: {error}"
        ) from error
    send.close()
    try:
        if not receive.poll(float(timeout)):
            process.terminate()
            process.join(_JOIN_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_JOIN_GRACE_SECONDS)
            raise NormalizationError("USTB normalization worker timed out")
        try:
            status, payload = receive.recv()
        except EOFError as error:
            raise NormalizationError(
                "USTB normalization worker exited without a result"
            ) from error
        except OSError as error:
            # A pipe that fails mid-read is the same outcome as one that closes early:
            # no result was obtained. The worker is reaped by the `finally` either way.
            raise NormalizationError(
                f"USTB normalization worker result could not be read: {error}"
            ) from error
    finally:
        receive.close()
        if process.is_alive():
            process.join(0.1)
            if process.is_alive():
                process.terminate()
                process.join(_JOIN_GRACE_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(_JOIN_GRACE_SECONDS)

    if status == "error":
        raise NormalizationError(payload)
    if status != "ok" or not isinstance(
        payload, (USTBNavObservation, USTBYieldObservation, USTBHoldingsObservation)
    ):
        raise NormalizationError("USTB normalization worker returned an invalid result")
    return payload


def _isolated_worker(
    connection: Connection,
    source_id: str,
    raw: bytes,
    max_bytes: int,
    max_depth: int,
) -> None:
    try:
        result = normalize_ustb_payload(
            source_id, raw, max_bytes=max_bytes, max_depth=max_depth
        )
    except (NormalizationError, TypeError, ValueError) as error:
        connection.send(("error", str(error)))
    else:
        connection.send(("ok", result))
    finally:
        connection.close()


Parser: TypeAlias = Callable[..., USTBObservation]


def _parser_for_source(source_id: str) -> Parser:
    if source_id == USTB_NAV_SOURCE_ID:
        return parse_nav_daily
    if source_id == USTB_YIELD_SOURCE_ID:
        return parse_yield
    if source_id == USTB_HOLDINGS_SOURCE_ID:
        return parse_holdings
    raise NormalizationError(f"unknown USTB source_id: {source_id!r}")


def _decode_json(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int,
    max_depth: int,
    expected_root: bytes,
) -> object:
    content = _prepare_raw(raw, max_bytes=max_bytes, max_depth=max_depth)
    if not content.lstrip().startswith(expected_root):
        shape = "array" if expected_root == b"[" else "object"
        raise NormalizationError(f"invalid JSON: payload must have {shape} magic byte")
    try:
        text = content.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_nonfinite_constant,
        )
    except NormalizationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        InvalidOperation,
        ValueError,
    ) as error:
        raise NormalizationError(f"invalid JSON: {error}") from error


def _prepare_raw(
    raw: bytes | bytearray | memoryview, *, max_bytes: int, max_depth: int
) -> bytes:
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("raw must be bytes-like")
    if type(max_bytes) is not int:
        raise TypeError("max_bytes must be an integer")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if type(max_depth) is not int:
        raise TypeError("max_depth must be an integer")
    if max_depth <= 0:
        raise ValueError("max_depth must be positive")
    content = bytes(raw)
    if len(content) > max_bytes:
        raise NormalizationError(f"payload exceeds size limit of {max_bytes} bytes")
    _check_nesting_depth(content, max_depth)
    return content


def _check_nesting_depth(raw: bytes, max_depth: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > max_depth:
                raise NormalizationError(f"payload exceeds depth limit of {max_depth}")
        elif byte in (0x5D, 0x7D):
            depth = max(0, depth - 1)


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NormalizationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> object:
    raise NormalizationError(f"non-finite JSON number is forbidden: {value}")


def _exact_object(
    value: object, expected_fields: frozenset[str], context: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise NormalizationError(f"{context} must be an object")
    fields = set(value)
    if fields != expected_fields:
        missing = sorted(expected_fields - fields)
        unknown = sorted(fields - expected_fields)
        raise NormalizationError(
            f"{context} has invalid fields: missing={missing}, unknown={unknown}"
        )
    return value


def _decimal_text(value: object, context: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        raise NormalizationError(f"{context} must be a finite decimal string")
    return _finite_decimal(value, context)


def _json_decimal(value: object, context: str) -> Decimal:
    if type(value) is int:
        return Decimal(value)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise NormalizationError(f"{context} must be a finite JSON number")
    return value


def _currency_decimal(value: object, context: str) -> Decimal:
    if not isinstance(value, str) or _CURRENCY_TEXT.fullmatch(value) is None:
        raise NormalizationError(f"{context} must be a dollar amount")
    return _finite_decimal(value[1:].replace(",", ""), context)


def _percent_decimal(value: object, context: str) -> Decimal:
    if not isinstance(value, str) or _PERCENT_TEXT.fullmatch(value) is None:
        raise NormalizationError(f"{context} must be a percentage")
    return _finite_decimal(value[:-1], context)


def _finite_decimal(value: str, context: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise NormalizationError(f"{context} must be a finite decimal") from error
    if not parsed.is_finite():
        raise NormalizationError(f"{context} must be a finite decimal")
    return parsed


def _parse_iso_date(value: object, context: str) -> date:
    if not isinstance(value, str):
        raise NormalizationError(f"{context} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise NormalizationError(f"{context} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise NormalizationError(f"{context} must be an ISO date")
    return parsed


def _parse_mmddyyyy(value: object, context: str) -> date:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value) is None
    ):
        raise NormalizationError(f"{context} must be MM/DD/YYYY")
    month, day, year = (int(part) for part in value.split("/"))
    try:
        return date(year, month, day)
    except ValueError as error:
        raise NormalizationError(f"{context} must be MM/DD/YYYY") from error


def _parse_maturity(value: object, context: str) -> date:
    if not isinstance(value, str):
        raise NormalizationError(f"{context} must be D-Mon-YYYY")
    match = _MATURITY_TEXT.fullmatch(value)
    if match is None or match.group(2) not in _MONTHS:
        raise NormalizationError(f"{context} must be D-Mon-YYYY")
    day, month_text, year = match.groups()
    try:
        return date(int(year), _MONTHS[month_text], int(day))
    except ValueError as error:
        raise NormalizationError(f"{context} must be D-Mon-YYYY") from error
