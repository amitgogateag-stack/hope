from uuid import uuid4

import pytest

from hope.domain.trading import TradeLinkContext, validate_trade_links


def test_trade_links_accept_same_canonical_instrument() -> None:
    instrument = uuid4()
    validate_trade_links(
        TradeLinkContext(
            signal_id=uuid4(),
            signal_instrument_id=instrument,
            order_instrument_id=instrument,
            position_instrument_id=instrument,
        )
    )


def test_order_cannot_change_instrument_from_signal() -> None:
    with pytest.raises(ValueError, match="ORDER_SIGNAL_INSTRUMENT_MISMATCH"):
        validate_trade_links(
            TradeLinkContext(
                signal_id=uuid4(),
                signal_instrument_id=uuid4(),
                order_instrument_id=uuid4(),
            )
        )


def test_position_cannot_change_instrument_from_signal() -> None:
    with pytest.raises(ValueError, match="POSITION_SIGNAL_INSTRUMENT_MISMATCH"):
        validate_trade_links(
            TradeLinkContext(
                signal_id=uuid4(),
                signal_instrument_id=(instrument := uuid4()),
                order_instrument_id=instrument,
                position_instrument_id=uuid4(),
            )
        )
