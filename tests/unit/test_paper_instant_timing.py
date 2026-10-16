from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from hope.application.paper.jobs import assert_paper_market_data_fresh
from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.market_data.context import PITMarketContext, build_pit_market_context
from hope.domain.market_data.models import MarketBar

NY = ZoneInfo("America/New_York")


def t(minute=0, fold=0):
    return datetime(2026, 11, 1, 1, minute, tzinfo=NY, fold=fold)


def bar(event, **extra):
    return MarketBar(instrument_id=str(INSTRUMENT), event_time=event, available_time=event,
                     ingestion_time=event, open=100, high=101, low=99, close=100, volume=1, **extra)


INSTRUMENT = uuid4()


@pytest.mark.parametrize("field", ["event_time", "available_time", "ingestion_time", "effective_time"])
def test_future_fold_information_is_excluded_and_rejected(field):
    values = bar(t(0)).model_dump()
    values[field] = t(0, fold=1)
    if field == "event_time":
        values.update(available_time=t(0, fold=1), ingestion_time=t(0, fold=1))
    elif field == "available_time":
        values.update(ingestion_time=t(0, fold=1))
    future = MarketBar(**values)
    assert not build_pit_market_context((future,), t(30)).bars
    with pytest.raises(ValidationError, match="UNAVAILABLE_INFORMATION"):
        PITMarketContext(as_of=t(30), bars=(future,))


def test_context_ordering_uses_instants_and_detects_inverted_identical_payloads():
    first, second = bar(t(45)), bar(t(15, fold=1))
    context = build_pit_market_context((second, first), t(59, fold=1))
    assert [b.event_time.astimezone(UTC) for b in context.bars] == [t(45).astimezone(UTC), t(15, fold=1).astimezone(UTC)]
    with pytest.raises(ValidationError, match="DETERMINISTICALLY_ORDERED"):
        PITMarketContext(as_of=t(59, fold=1), bars=(second, first))


@pytest.mark.parametrize("age,stale", [(timedelta(minutes=59), True), (timedelta(hours=1), False)])
def test_paper_freshness_measures_elapsed_age(age, stale):
    context = PITMarketContext(as_of=t(15, fold=1), bars=(bar(t(15)),))
    if stale:
        with pytest.raises(RuntimeError, match="MARKET_DATA_STALE"):
            assert_paper_market_data_fresh(context, (INSTRUMENT,), age)
    else:
        assert_paper_market_data_fresh(context, (INSTRUMENT,), age)


def test_paper_freshness_selects_latest_absolute_event():
    context = build_pit_market_context((bar(t(45)), bar(t(15, fold=1))), t(20, fold=1))
    assert_paper_market_data_fresh(context, (INSTRUMENT,), timedelta(minutes=5))


@pytest.mark.parametrize("decision", [t(50), datetime(2026, 3, 8, 1, 50, tzinfo=NY)])
def test_submission_and_latency_are_elapsed_durations(decision):
    timeline = ExecutionTimeline.from_decision(decision, latency=timedelta(minutes=20),
                                               order_submission_delay=timedelta(minutes=20))
    assert timeline.order_time.astimezone(UTC) == decision.astimezone(UTC) + timedelta(minutes=20)
    assert timeline.fill_eligible_time.astimezone(UTC) == decision.astimezone(UTC) + timedelta(minutes=40)


@pytest.mark.parametrize("case,error", [
    ("order", "ORDER_TIME_PRECEDES"), ("eligible", "FILL_ELIGIBILITY_MUST_EQUAL"),
    ("fill", "FILL_TIME_PRECEDES"), ("quote", "QUOTE_PRECEDES"),
])
def test_timeline_rejects_earlier_fold_even_with_equal_wall_time(case, error):
    with pytest.raises(ValueError, match=error):
        if case == "order":
            ExecutionTimeline(t(0, fold=1), t(0), timedelta(0), t(0))
        elif case == "eligible":
            ExecutionTimeline(t(0), t(0), timedelta(0), t(0, fold=1))
        else:
            timeline = ExecutionTimeline.from_decision(t(0, fold=1), latency=timedelta(0))
            if case == "fill":
                timeline.with_fill_time(t(0))
            else:
                timeline.assert_quote_eligible(t(0))


def order():
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=INSTRUMENT,
                 side=OrderSide.BUY, quantity=Decimal(1), environment=Environment.PAPER)


def test_quote_chronology_is_absolute():
    with pytest.raises(ValueError, match="AVAILABLE_TIME_PRECEDES"):
        ExecutionQuote(INSTRUMENT, t(15, fold=1), Decimal(99), Decimal(100), t(45))


def test_later_fold_availability_cannot_be_used_for_an_earlier_fill():
    quote = ExecutionQuote(INSTRUMENT, t(0), Decimal(99), Decimal(100), t(0, fold=1))
    timeline = ExecutionTimeline.from_decision(t(0), latency=timedelta(0)).with_fill_time(t(30))
    with pytest.raises(ValueError, match="QUOTE_UNAVAILABLE_AT_FILL_TIME"):
        simulate_market_fill(order(), quote, uuid4(), CostModel("v1"), timeline=timeline)


def test_future_quote_event_cannot_price_a_past_fill():
    timeline = ExecutionTimeline.from_decision(t(0), latency=timedelta(0)).with_fill_time(t(30))
    quote = ExecutionQuote(INSTRUMENT, t(0, fold=1), Decimal(99), Decimal(100))
    with pytest.raises(ValueError, match="QUOTE_EVENT_TIME_AFTER_FILL_TIME"):
        simulate_market_fill(order(), quote, uuid4(), CostModel("v1"), timeline=timeline)


def test_valid_quote_crossing_fold_preserves_fill_price_and_costs():
    quote = ExecutionQuote(INSTRUMENT, t(45), Decimal(99), Decimal(100), t(15, fold=1))
    timeline = ExecutionTimeline.from_decision(t(40), latency=timedelta(minutes=5)).with_fill_time(t(30, fold=1))
    fill = simulate_market_fill(order(), quote, uuid4(), CostModel("v1", Decimal("0.01")), timeline=timeline)
    assert fill.price == 100 and fill.commission == 1 and fill.slippage == 0
    assert fill.fill_time.astimezone(UTC) == t(30, fold=1).astimezone(UTC)
