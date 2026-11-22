"""Strict prototype parsing and unconditional production refusal."""
from uuid import uuid4
from types import SimpleNamespace
import pytest
from hope.security.write_set_witness import (
    PrototypeAuthorityRoot, WitnessDenied, authenticate_response, canonical_body,
    production_approval, expected_body,
)


def root():
    return PrototypeAuthorityRoot(b'x'*32, 'TEST_ONLY', 'a'*64, 'b'*64)


def test_production_refuses_without_inspecting_hostile_request():
    class Hostile:
        def __getattribute__(self, _name):
            raise AssertionError('untrusted request evaluated')
    with pytest.raises(WitnessDenied, match='INDEPENDENT_WRITE_SET_AUTHORITY_UNAVAILABLE'):
        production_approval(Hostile())


@pytest.mark.parametrize('field,value', [
    ('public_key',b'x'*31), ('public_key','x'*32), ('issuer','bad\nissuer'),
    ('issuer',''), ('issuer',' test'), ('issuer','\u03bb'),
    ('release_sha256','A'*64), ('runtime_image_sha256',True),
])
def test_root_configuration_is_strict(field,value):
    values=dict(public_key=b'x'*32,issuer='TEST',release_sha256='a'*64,runtime_image_sha256='b'*64)
    values[field]=value
    with pytest.raises(WitnessDenied,match='WRITE_SET_ROOT_CONFIG_INVALID'):
        PrototypeAuthorityRoot(**values)


@pytest.mark.parametrize('response,reason', [
    (b'{"body":{},"body":{},"signature":""}', 'DUPLICATE_FIELD'),
    (b'{"body":{"x":1.0}}', 'NUMBER_INVALID'),
    (b'{"body":{"x":NaN}}', 'NUMBER_INVALID'),
    (b'[]','RESPONSE_INVALID'), (b'{}','RESPONSE_INVALID'),
    (b'\xff','RESPONSE_INVALID'), (b'x'*16385,'RESPONSE_INVALID'),
])
def test_bad_wire_input_is_refused_before_database_access(response,reason):
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    with pytest.raises(WitnessDenied,match=reason):
        authenticate_response(connection,uuid4(),'c'*64,response,root())


def test_canonical_bytes_distinguish_boolean_and_integer_and_bind_root():
    assert canonical_body({'x':True}) != canonical_body({'x':1})
    body=expected_body({'nonce':'test'},'c'*64,root())
    assert body['role']=='PROTOTYPE_WRITE_SET_SIGNER'
    assert body['scope']=='ONE_PROTOTYPE_FINANCIAL_TRANSACTION'
    assert body['release_sha256']=='a'*64
