from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from hope.domain.identity.models import IdentityMapping, IdentityStatus
from hope.domain.market_data.models import MarketBar


def test_active_identity_requires_canonical_identity():
    with pytest.raises(ValidationError):
        IdentityMapping(source_symbol="AAA", broker_instrument_id="B1", status=IdentityStatus.ACTIVE)


def test_terminal_identity_cannot_have_canonical_identity():
    with pytest.raises(ValidationError):
        IdentityMapping(source_symbol="AAA", broker_instrument_id="B1", canonical_instrument_id=__import__('uuid').uuid4(), status=IdentityStatus.TERMINAL)


def test_invalid_ohlc_is_rejected_at_domain_boundary():
    t = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketBar(instrument_id="X", event_time=t, available_time=t, ingestion_time=t,
                  open=Decimal("10"), high=Decimal("8"), low=Decimal("9"), close=Decimal("10"), volume=Decimal("1"))
