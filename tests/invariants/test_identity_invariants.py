from uuid import uuid4
from hope.application.identity.resolver import IdentityResolver
from hope.domain.identity.models import IdentityMapping, IdentityStatus


def test_invariant_002_no_two_active_members_share_broker_identity():
    mappings = [
        IdentityMapping(source_symbol="AAA", broker_instrument_id="B1", canonical_instrument_id=uuid4(), status=IdentityStatus.ACTIVE),
        IdentityMapping(source_symbol="BBB", broker_instrument_id="B1", canonical_instrument_id=uuid4(), status=IdentityStatus.ACTIVE),
    ]
    resolved = IdentityResolver().resolve_batch(mappings)
    assert sum(x.evaluable for x in resolved) == 1
