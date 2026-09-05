from uuid import uuid4
from hope.application.identity.resolver import IdentityResolver
from hope.domain.identity.models import IdentityMapping, IdentityStatus


def mapping(symbol, broker, status=IdentityStatus.ACTIVE):
    return IdentityMapping(source_symbol=symbol, broker_instrument_id=broker, canonical_instrument_id=uuid4(), status=status)


def test_duplicate_broker_identity_yields_one_evaluable_member():
    results = IdentityResolver().resolve_batch([mapping("AAA", "BROKER-1"), mapping("BBB", "BROKER-1")])
    assert sum(r.evaluable for r in results) == 1
    terminal = [r.mapping for r in results if not r.evaluable][0]
    assert terminal.status is IdentityStatus.TERMINAL
    assert terminal.reason == "DATA_UNAVAILABLE_DUPLICATE_OF_CURRENT_MEMBER"


def test_distinct_broker_identities_remain_evaluable():
    results = IdentityResolver().resolve_batch([mapping("AAA", "BROKER-1"), mapping("BBB", "BROKER-2")])
    assert all(r.evaluable for r in results)
