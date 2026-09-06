from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.market_data.context import PITMarketContext, build_pit_market_context
from hope.domain.market_data.models import MarketBar


def make_bar(instrument_id, event_time, available_time, close):
    return MarketBar(
        instrument_id=str(instrument_id),
        event_time=event_time,
        available_time=available_time,
        ingestion_time=available_time,
        open=Decimal(str(close)),
        high=Decimal(str(close)),
        low=Decimal(str(close)),
        close=Decimal(str(close)),
        volume=Decimal("100"),
    )


def test_context_excludes_bar_available_after_as_of():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    late = t0 + timedelta(minutes=5)
    early = make_bar(instrument, t0, t0, "100")
    unavailable = make_bar(instrument, t0 + timedelta(minutes=1), late, "999")

    context = build_pit_market_context([early, unavailable], t0 + timedelta(minutes=1))

    assert context.as_of == t0 + timedelta(minutes=1)
    assert [bar.close for bar in context.bars] == [Decimal("100")]


def test_context_excludes_future_event_even_if_already_available():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    future_event = t0 + timedelta(minutes=10)
    future_bar = make_bar(instrument, future_event, future_event, "999")

    context = build_pit_market_context([future_bar], t0)

    assert context.bars == ()


def test_context_rejects_naive_as_of():
    with pytest.raises(ValueError, match="timezone-aware"):
        PITMarketContext(as_of=datetime(2026, 1, 2, 14, 30), bars=())


def test_context_rejects_direct_injection_of_unavailable_bar():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    late = t0 + timedelta(minutes=1)
    future_bar = make_bar(instrument, t0, late, "999")

    with pytest.raises(ValueError, match="PIT_CONTEXT_CONTAINS_UNAVAILABLE_INFORMATION"):
        PITMarketContext(as_of=t0, bars=(future_bar,))


def test_context_is_immutable():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    context = build_pit_market_context([make_bar(instrument, t0, t0, "100")], t0)

    with pytest.raises((TypeError, ValueError)):
        context.as_of = t0 + timedelta(minutes=1)
