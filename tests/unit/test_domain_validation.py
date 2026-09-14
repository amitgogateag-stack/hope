from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from hope.domain.identity.models import IdentityMapping, IdentityStatus, Instrument
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


@pytest.mark.parametrize("field", ["canonical_symbol", "exchange"])
@pytest.mark.parametrize("value", ["", "   "])
def test_instrument_identity_fields_must_be_nonblank(field, value):
    values = {
        "instrument_id": __import__("uuid").uuid4(),
        "canonical_symbol": "AAA",
        "exchange": "NSE",
    }
    values[field] = value
    with pytest.raises(ValidationError, match="INSTRUMENT_IDENTITY_REQUIRED"):
        Instrument(**values)


@pytest.mark.parametrize("field", ["canonical_symbol", "exchange"])
def test_instrument_identity_fields_must_be_canonical(field):
    values = {
        "instrument_id": __import__("uuid").uuid4(),
        "canonical_symbol": "AAA",
        "exchange": "NSE",
    }
    values[field] = f" {values[field]} "
    with pytest.raises(ValidationError, match="INSTRUMENT_IDENTITY_NOT_CANONICAL"):
        Instrument(**values)


@pytest.mark.parametrize("field", ["source_symbol", "broker_instrument_id"])
@pytest.mark.parametrize("value", ["", "   "])
def test_mapping_identity_fields_must_be_nonblank(field, value):
    values = {
        "source_symbol": "AAA",
        "broker_instrument_id": "BROKER-1",
        "canonical_instrument_id": __import__("uuid").uuid4(),
        "status": IdentityStatus.ACTIVE,
    }
    values[field] = value
    with pytest.raises(ValidationError, match="IDENTITY_MAPPING_VALUE_REQUIRED"):
        IdentityMapping(**values)


@pytest.mark.parametrize("field", ["source_symbol", "broker_instrument_id"])
def test_mapping_identity_fields_must_be_canonical(field):
    values = {
        "source_symbol": "AAA",
        "broker_instrument_id": "BROKER-1",
        "canonical_instrument_id": __import__("uuid").uuid4(),
        "status": IdentityStatus.ACTIVE,
    }
    values[field] = f" {values[field]} "
    with pytest.raises(ValidationError, match="IDENTITY_MAPPING_VALUE_NOT_CANONICAL"):
        IdentityMapping(**values)
