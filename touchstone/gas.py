"""How many more publications the publisher can actually afford.

Every number here comes from something that already happened. The balance is read at one
confirmed block, and the cost per publication is the largest amount this publisher has in
fact spent — ``gas_used x effective_gas_price`` from receipts that succeeded. No fee oracle,
no estimate, no configured ceiling standing in for a measurement. An estimate would make the
runway optimistic in precisely the conditions that make it matter, because the moment fees
spike is the moment the estimate is furthest from the truth.

The answer is a count and a date, or it is ``UNKNOWN``. There is no fallback figure. A
runway that quietly degrades to a guess is worse than one that admits it cannot be computed:
the first is acted on, the second is investigated.

This is the defect class from T7 in arithmetic form — a quotient of two independently read
chain values — so both operands are materialised once, validated, and only then divided.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


UNKNOWN = "UNKNOWN"


class GasError(RuntimeError):
    """The runway could not be established."""


@dataclass(frozen=True, slots=True)
class Runway:
    """What the balance covers, or an honest refusal to say."""

    balance_wei: int | None
    maximum_cost_wei: int | None
    remaining_publications: int | None
    funded_through: date | None
    samples: int
    status: str
    detail: str

    @property
    def known(self) -> bool:
        return self.status != UNKNOWN

    def covers(self, until: date) -> bool:
        """Whether funding reaches a date. Unknown never counts as covered."""
        if type(until) is not date:
            raise GasError("until must be a plain date")
        return self.funded_through is not None and self.funded_through >= until


def measured_costs(entries: Sequence[Mapping[str, object]]) -> list[int]:
    """Extract real spends from verified transparency-log entries.

    Only successful publications count. A reverted transaction still costs gas, but it is
    not the cost of *publishing*, and including one would raise the estimate on the
    strength of an event that published nothing.
    """
    costs: list[int] = []
    for entry in tuple(entries):
        if not isinstance(entry, Mapping):
            raise GasError("each transparency entry must be a mapping")
        publication = entry.get("publication")
        if not isinstance(publication, Mapping):
            continue
        receipt = publication.get("receipt")
        if not isinstance(receipt, Mapping):
            continue
        if receipt.get("status") != 1:
            continue
        gas_used = receipt.get("gas_used")
        price = receipt.get("effective_gas_price")
        if not _is_positive_integer(gas_used) or not _is_positive_integer(price):
            # An entry written before the price was recorded, or one whose node omitted
            # it. Skipped rather than guessed: a missing operand is not a zero.
            continue
        costs.append(gas_used * price)
    return costs


def runway(
    *,
    balance_wei: object,
    entries: Sequence[Mapping[str, object]],
    schedule: Sequence[date],
    read_at_block: object,
) -> Runway:
    """Divide a balance read once by the largest cost actually paid.

    ``schedule`` is the remaining publication dates in order. The answer names the last
    slot the balance fully funds, which is the form an operator can act on — "eleven more"
    means nothing without knowing when they fall.
    """
    if not _is_non_negative_integer(balance_wei):
        raise GasError("balance_wei must be a non-negative integer")
    if not _is_non_negative_integer(read_at_block):
        raise GasError("read_at_block must be a non-negative integer")
    slots = tuple(schedule)
    if any(type(slot) is not date for slot in slots):
        raise GasError("each scheduled slot must be a plain date")
    if list(slots) != sorted(slots):
        raise GasError("the schedule must be in order")

    costs = measured_costs(entries)
    if not costs:
        return Runway(
            balance_wei=balance_wei,
            maximum_cost_wei=None,
            remaining_publications=None,
            funded_through=None,
            samples=0,
            status=UNKNOWN,
            detail=(
                "no successful publication has recorded both gas and an effective price, "
                "so there is no measured cost to divide by"
            ),
        )

    maximum = max(costs)
    if maximum <= 0:
        # Unreachable through `measured_costs`, which refuses non-positive operands, and
        # asserted anyway: this is the denominator, and a zero here would produce an
        # infinite runway from arithmetic rather than from money.
        return Runway(
            balance_wei=balance_wei,
            maximum_cost_wei=None,
            remaining_publications=None,
            funded_through=None,
            samples=len(costs),
            status=UNKNOWN,
            detail="the measured cost was not positive",
        )

    remaining = balance_wei // maximum
    funded = slots[min(remaining, len(slots)) - 1] if remaining and slots else None
    return Runway(
        balance_wei=balance_wei,
        maximum_cost_wei=maximum,
        remaining_publications=remaining,
        funded_through=funded,
        samples=len(costs),
        status="OK",
        detail=(
            f"{remaining} publications at the highest measured cost of {maximum} wei, "
            f"from {len(costs)} samples, against a balance read at block {read_at_block}"
        ),
    )


def daily_schedule(start: date, through: date) -> list[date]:
    """Every day from ``start`` to ``through`` inclusive.

    Daily rather than business-daily on purpose: the operations window commits to a
    scheduled epoch every day, and weekend slots that reconfirm still cost gas.
    """
    if type(start) is not date or type(through) is not date:
        raise GasError("start and through must be plain dates")
    if through < start:
        raise GasError("through must not precede start")
    return [
        start + timedelta(days=offset) for offset in range((through - start).days + 1)
    ]


def render(value: Runway) -> str:
    """One block an operator can read without opening the code."""
    lines = [f"status: {value.status}", f"detail: {value.detail}"]
    if value.known:
        lines.append(f"balance_wei: {value.balance_wei}")
        lines.append(f"maximum_measured_cost_wei: {value.maximum_cost_wei}")
        lines.append(f"remaining_publications: {value.remaining_publications}")
        lines.append(f"funded_through: {value.funded_through}")
    return "\n".join(lines)


def _is_positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


def _is_non_negative_integer(value: object) -> bool:
    # `type(...) is int` rather than isinstance, because `True` is an int and a balance of
    # True is not a balance anyone meant.
    return type(value) is int and value >= 0


def utc_today(now: datetime) -> date:
    """The operating day an instant belongs to."""
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise GasError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc).date()
