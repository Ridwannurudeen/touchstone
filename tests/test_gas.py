"""The runway must be a measurement or an admission, never an estimate."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from touchstone.gas import (
    UNKNOWN,
    GasError,
    Indeterminate,
    daily_schedule,
    measured_costs,
    render,
    runway,
    utc_today,
)


SEPT_3 = date(2026, 9, 3)


def entry(gas_used: int | None = 100_000, price: int | None = 1_000_000_000, status=1):
    receipt: dict[str, object] = {"status": status}
    if gas_used is not None:
        receipt["gas_used"] = gas_used
    if price is not None:
        receipt["effective_gas_price"] = price
    return {"publication": {"receipt": receipt}}


def schedule(days: int = 20):
    return daily_schedule(
        date(2026, 8, 15), date(2026, 8, 15) + timedelta(days=days - 1)
    )


def test_the_runway_is_a_count_and_a_date() -> None:
    """ "Eleven more" means nothing without knowing when they fall."""
    value = runway(
        balance_wei=10 * 100_000 * 1_000_000_000,
        entries=[entry()],
        schedule=schedule(),
        read_at_block=38_361_687,
    )

    assert value.known
    assert value.remaining_publications == 10
    assert value.funded_through == date(2026, 8, 24)
    assert value.maximum_cost_wei == 100_000 * 1_000_000_000
    assert value.samples == 1


def test_the_cost_is_the_largest_actually_paid_not_the_average() -> None:
    """An average is optimistic exactly when fees spike, which is when this matters."""
    value = runway(
        balance_wei=10 * 300_000 * 1_000_000_000,
        entries=[entry(100_000), entry(300_000), entry(200_000)],
        schedule=schedule(),
        read_at_block=1,
    )

    assert value.maximum_cost_wei == 300_000 * 1_000_000_000
    assert value.remaining_publications == 10
    assert value.samples == 3


def test_no_measured_cost_is_unknown_rather_than_a_guess() -> None:
    """A runway that degrades to a guess is acted on; one that admits it is investigated."""
    value = runway(balance_wei=10**18, entries=[], schedule=schedule(), read_at_block=1)

    assert value.status == UNKNOWN
    assert not value.known
    assert value.remaining_publications is None
    assert value.funded_through is None
    assert not value.covers(SEPT_3)


@pytest.mark.parametrize(
    "ignorable",
    [
        entry(status=0),
        {"publication": {"receipt": "not a mapping"}},
        {"publication": "not a mapping"},
        {},
    ],
)
def test_an_entry_that_is_not_a_successful_publication_is_skipped(ignorable) -> None:
    """A reverted transaction published nothing, so its gas is not a publication cost."""
    assert measured_costs([ignorable]) == []


@pytest.mark.parametrize(
    "incomplete",
    [
        entry(gas_used=None),
        entry(price=None),
        entry(gas_used=0),
        entry(price=0),
        entry(gas_used=True),
    ],
)
def test_a_successful_publication_with_no_readable_cost_is_indeterminate(
    incomplete,
) -> None:
    """Skipping it would produce a confident number from incomplete data.

    The answer is the *maximum* cost, so the sample that could not be read might have been
    the maximum. A missing operand is not a zero, and it is not an absence either.
    """
    with pytest.raises(Indeterminate):
        measured_costs([incomplete])

    value = runway(
        balance_wei=10**18,
        entries=[entry(), incomplete],
        schedule=schedule(),
        read_at_block=1,
    )
    assert value.status == UNKNOWN, "one unreadable sample makes the whole answer unknown"
    assert not value.covers(SEPT_3)


def test_a_reverted_publication_does_not_raise_the_measured_cost() -> None:
    """It cost gas. It is not the cost of publishing, because it published nothing."""
    costs = measured_costs([entry(100_000), entry(900_000, status=0)])

    assert costs == [100_000 * 1_000_000_000]


def test_a_balance_short_of_one_publication_funds_nothing() -> None:
    value = runway(
        balance_wei=100_000 * 1_000_000_000 - 1,
        entries=[entry()],
        schedule=schedule(),
        read_at_block=1,
    )

    assert value.remaining_publications == 0
    assert value.funded_through is None
    assert not value.covers(date(2026, 8, 15))


def test_funding_beyond_the_schedule_stops_at_the_last_slot() -> None:
    """The answer is bounded by what is actually scheduled, not by the division."""
    slots = schedule(5)
    value = runway(
        balance_wei=1000 * 100_000 * 1_000_000_000,
        entries=[entry()],
        schedule=slots,
        read_at_block=1,
    )

    assert value.remaining_publications == 1000
    assert value.funded_through == slots[-1]


def test_the_sept_3_gate_is_answered_directly() -> None:
    """The operating requirement is coverage through Sept 3, so that is a method."""
    slots = daily_schedule(date(2026, 8, 15), date(2026, 9, 10))
    funded = runway(
        balance_wei=40 * 100_000 * 1_000_000_000,
        entries=[entry()],
        schedule=slots,
        read_at_block=1,
    )
    short = runway(
        balance_wei=3 * 100_000 * 1_000_000_000,
        entries=[entry()],
        schedule=slots,
        read_at_block=1,
    )

    assert funded.covers(SEPT_3)
    assert not short.covers(SEPT_3)


@pytest.mark.parametrize("balance", [-1, True, "100", None, 1.5])
def test_a_balance_that_is_not_a_balance_is_refused(balance: object) -> None:
    """`True` is an int in Python, and a balance of True is not a balance."""
    with pytest.raises(GasError, match="balance_wei"):
        runway(
            balance_wei=balance,
            entries=[entry()],
            schedule=schedule(),
            read_at_block=1,
        )


@pytest.mark.parametrize("block", [-1, True, "1", None])
def test_a_block_that_is_not_a_block_is_refused(block: object) -> None:
    with pytest.raises(GasError, match="read_at_block"):
        runway(
            balance_wei=10**18,
            entries=[entry()],
            schedule=schedule(),
            read_at_block=block,
        )


def test_an_unordered_schedule_is_refused() -> None:
    """Out of order, "the last slot funded" names a date the balance does not reach."""
    with pytest.raises(GasError, match="in order"):
        runway(
            balance_wei=10**18,
            entries=[entry()],
            schedule=[date(2026, 8, 20), date(2026, 8, 15)],
            read_at_block=1,
        )


def test_a_schedule_of_the_wrong_type_is_refused() -> None:
    with pytest.raises(GasError, match="plain date"):
        runway(
            balance_wei=10**18,
            entries=[entry()],
            schedule=[datetime(2026, 8, 15, tzinfo=timezone.utc)],
            read_at_block=1,
        )


def test_entries_are_materialised_once() -> None:
    """A generator validated by one pass and read by another yields nothing the second."""
    slots = schedule()
    once = (item for item in [entry(), entry(200_000)])

    value = runway(balance_wei=10**18, entries=once, schedule=slots, read_at_block=1)

    assert value.samples == 2, "both samples survived the single pass"


def test_the_schedule_covers_weekends_because_weekend_slots_cost_gas() -> None:
    slots = daily_schedule(date(2026, 8, 15), date(2026, 8, 21))

    assert len(slots) == 7
    assert slots[0] == date(2026, 8, 15)
    assert slots[-1] == date(2026, 8, 21)


def test_a_backwards_schedule_window_is_refused() -> None:
    with pytest.raises(GasError, match="must not precede"):
        daily_schedule(date(2026, 8, 21), date(2026, 8, 15))


def test_covers_refuses_anything_that_is_not_a_plain_date() -> None:
    value = runway(
        balance_wei=10**18, entries=[entry()], schedule=schedule(), read_at_block=1
    )

    with pytest.raises(GasError, match="plain date"):
        value.covers(datetime(2026, 9, 3, tzinfo=timezone.utc))


def test_the_rendered_form_states_unknown_without_inventing_numbers() -> None:
    value = runway(balance_wei=10**18, entries=[], schedule=schedule(), read_at_block=1)
    text = render(value)

    assert "UNKNOWN" in text
    assert "remaining_publications" not in text


def test_the_operating_day_needs_an_aware_instant() -> None:
    assert utc_today(datetime(2026, 8, 15, 23, 30, tzinfo=timezone.utc)) == date(
        2026, 8, 15
    )
    with pytest.raises(GasError, match="timezone-aware"):
        utc_today(datetime(2026, 8, 15, 23, 30))
