from hope.domain.identity.models import IdentityMapping, IdentityStatus

def test_duplicate_broker_identity_is_terminal_and_non_evaluable():
    mapping = IdentityMapping(source_symbol="KALPATPOWR-EQ", broker_instrument_id="KPIL-EQ", status=IdentityStatus.TERMINAL, reason="DATA_UNAVAILABLE_DUPLICATE_OF_CURRENT_MEMBER")
    assert mapping.status is IdentityStatus.TERMINAL
    assert mapping.canonical_instrument_id is None
