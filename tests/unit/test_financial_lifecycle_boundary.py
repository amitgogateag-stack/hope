from dataclasses import replace
from uuid import uuid4

import pytest

from hope.security.financial_lifecycle import CycleReference, LifecycleDenied, dispatch_one_cycle


def reference():
    return CycleReference('a'*64, 'b'*64, uuid4(), '123', 'restricted', uuid4())


@pytest.mark.parametrize('field,value', [
    ('database_sha256', 'A'*64), ('authorization_sha256', '0'*63),
    ('job_run_id', 'not-a-uuid'), ('portfolio_id', None),
    ('financial_xid', '0'), ('financial_xid', '01'), ('financial_xid', str(2**64)),
    ('financial_xid', 123), ('financial_principal', ''),
    ('financial_principal', 'x\n'), ('financial_principal', 'x'*64),
])
def test_invalid_reference_is_refused(field, value):
    with pytest.raises(LifecycleDenied):
        replace(reference(), **{field: value})


@pytest.mark.parametrize('document', [None, {}, {'execution_authorized': True},
    {'sql': 'INSERT INTO paper_portfolios VALUES (...)'},
    {'signature': 'a'*128, 'scope': 'ONE_CERTIFIED_PAPER_CYCLE'},
    {'history_consistent': True, 'production_verification': True},
    {'observer': 'trusted', 'signer': 'independent', 'permit': object()},
])
def test_no_worker_document_or_history_can_enable_dispatch(document):
    with pytest.raises(LifecycleDenied, match='INDEPENDENT_FINANCIAL_ADMISSION_UNAVAILABLE'):
        dispatch_one_cycle(document)


def test_dispatch_does_not_evaluate_worker_object_or_factory():
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError('untrusted object evaluated')
    with pytest.raises(LifecycleDenied, match='INDEPENDENT_FINANCIAL_ADMISSION_UNAVAILABLE'):
        dispatch_one_cycle(Hostile())
