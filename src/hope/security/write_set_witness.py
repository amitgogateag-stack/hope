"""Experimental transaction witness client; NO production authority producer.

The SQL installer is disposable-only. A pinned prototype public key can verify
cryptographic context, but cannot attest its own custody, runtime or provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import Connection, text

DOMAIN = b'hope.prototype-financial-write-set.v1\x00'
MAX_RESPONSE = 16384


class WitnessDenied(RuntimeError):
    pass


def production_approval(request: object) -> None:
    """No configuration/worker proof can manufacture the missing managed root."""
    raise WitnessDenied('INDEPENDENT_WRITE_SET_AUTHORITY_UNAVAILABLE')


def _digest(value: object) -> bool:
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


@dataclass(frozen=True)
class PrototypeAuthorityRoot:
    public_key: bytes
    issuer: str
    release_sha256: str
    runtime_image_sha256: str

    def __post_init__(self) -> None:
        if (type(self.public_key) is not bytes or len(self.public_key) != 32
                or type(self.issuer) is not str or not 1 <= len(self.issuer) <= 128
                or not self.issuer.isascii() or not self.issuer.isprintable() or self.issuer != self.issuer.strip()
                or not _digest(self.release_sha256) or not _digest(self.runtime_image_sha256)):
            raise WitnessDenied('WRITE_SET_ROOT_CONFIG_INVALID')


def canonical_body(body: dict) -> bytes:
    return DOMAIN + json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WitnessDenied('WRITE_SET_RESPONSE_DUPLICATE_FIELD')
        result[key] = value
    return result


def _not_json_number(_value):
    raise WitnessDenied('WRITE_SET_RESPONSE_NUMBER_INVALID')


def expected_body(challenge: dict, writes_sha256: str, root: PrototypeAuthorityRoot) -> dict:
    """Pure prototype signing format, not an authority or observation adapter."""
    if type(challenge) is not dict or type(root) is not PrototypeAuthorityRoot or not _digest(writes_sha256):
        raise WitnessDenied('WRITE_SET_CHALLENGE_INVALID')
    return dict(protocol='hope.prototype-financial-write-set.v1', issuer=root.issuer,
        key_sha256=sha256(root.public_key).hexdigest(), role='PROTOTYPE_WRITE_SET_SIGNER',
        scope='ONE_PROTOTYPE_FINANCIAL_TRANSACTION', challenge=challenge,
        writes_sha256=writes_sha256, release_sha256=root.release_sha256,
        runtime_image_sha256=root.runtime_image_sha256)


def authenticate_response(connection: Connection, nonce: UUID, writes_sha256: str,
                          response: bytes, root: PrototypeAuthorityRoot) -> dict:
    """Verify against the ACTUAL current DB claim, never worker-supplied context.

    This returns prototype authentication evidence only, with no approval write.
    Root configuration/provisioning, signer isolation and runtime truth remain
    external requirements. Production dispatch never calls this component.
    """
    if (connection.dialect.name != 'postgresql' or type(nonce) is not UUID
            or type(response) is not bytes or not 0 < len(response) <= MAX_RESPONSE
            or type(root) is not PrototypeAuthorityRoot or not _digest(writes_sha256)):
        raise WitnessDenied('WRITE_SET_RESPONSE_INVALID')
    try:
        value = json.loads(response, object_pairs_hook=_unique, parse_float=_not_json_number,
                           parse_constant=_not_json_number)
    except (ValueError, UnicodeError) as exc:
        raise WitnessDenied('WRITE_SET_RESPONSE_INVALID') from exc
    if (type(value) is not dict or set(value) != {'body', 'signature'}
            or type(value['body']) is not dict or type(value['signature']) is not str
            or re.fullmatch('[0-9a-f]{128}', value['signature']) is None):
        raise WitnessDenied('WRITE_SET_RESPONSE_INVALID')
    challenge = connection.execute(text('SELECT financial_witness.challenge(:nonce)'), {'nonce': nonce}).scalar_one()
    now = connection.execute(text('SELECT extract(epoch FROM clock_timestamp())::bigint')).scalar_one()
    if type(challenge.get('expires_at')) is not int or now >= challenge['expires_at']:
        raise WitnessDenied('WRITE_SET_RESPONSE_EXPIRED')
    expected = canonical_body(expected_body(challenge, writes_sha256, root))
    # Canonical BYTES refuse bool/int equality substitutions and extra fields.
    if canonical_body(value['body']) != expected:
        raise WitnessDenied('WRITE_SET_RESPONSE_BINDING_MISMATCH')
    try:
        Ed25519PublicKey.from_public_bytes(root.public_key).verify(bytes.fromhex(value['signature']), expected)
    except (ValueError, InvalidSignature) as exc:
        raise WitnessDenied('WRITE_SET_RESPONSE_SIGNATURE_INVALID') from exc
    return dict(response_sha256=sha256(response).hexdigest(), nonce=str(nonce),
        cryptographic_context_authenticated=True, production_authorized=False,
        independent_root_provenance='BLOCKED', signer_isolation='BLOCKED')


def capture_snapshot(connection: Connection, nonce: UUID) -> tuple[str, str]:
    if connection.dialect.name != 'postgresql' or type(nonce) is not UUID:
        raise WitnessDenied('WRITE_SET_SNAPSHOT_CONTEXT_INVALID')
    row = connection.execute(text('SELECT financial_witness.snapshot(:nonce)::text, '
        'financial_witness.digest(financial_witness.snapshot(:nonce))'), {'nonce': nonce}).one()
    return row[0], row[1]


def inspect_reservation(connection: Connection, authorization_sha256: str) -> dict:
    """Read-only durable reconciliation. No state ever permits replay/dispatch."""
    if connection.dialect.name != 'postgresql' or not _digest(authorization_sha256):
        raise WitnessDenied('WRITE_SET_RECOVERY_CONTEXT_INVALID')
    settings = connection.execute(text("SELECT current_setting('transaction_read_only'),"
        "current_setting('transaction_isolation')")).one()
    if tuple(settings) != ('on', 'repeatable read'):
        raise WitnessDenied('WRITE_SET_RECOVERY_REQUIRES_READ_ONLY_SNAPSHOT')
    row = connection.execute(text(
        "SELECT c.nonce,c.transaction_id::text,e.transaction_id::text,s.transaction_id::text,"
        "s.event_count,s.events_sha256,count(v.event_no),"
        "financial_witness.digest(coalesce(jsonb_agg(jsonb_build_object('relation',v.relation_name,"
        "'operation',v.operation,'before',v.old_row,'after',v.new_row) ORDER BY v.event_no) "
        "FILTER(WHERE v.event_no IS NOT NULL),'[]'::jsonb)),"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity p WHERE p.pid=c.backend_pid "
        "AND p.backend_start=c.backend_start AND p.backend_xid::text::numeric=mod(c.transaction_id::text::numeric,4294967296)), "
        "EXISTS(SELECT 1 FROM financial_witness.approvals a WHERE a.nonce=c.nonce "
        "AND financial_witness.digest(a.expected_events)=s.events_sha256), "
        "coalesce(bool_and(v.transaction_id=c.transaction_id),false), "
        "EXISTS(SELECT 1 FROM public.job_runs j JOIN public.paper_certified_outcomes o USING(job_run_id) "
        "JOIN public.paper_startup_financial_transactions t ON t.authorization_sha256=c.authorization_sha256 "
        "JOIN public.paper_startup_commit_witnesses w ON w.authorization_sha256=t.authorization_sha256 "
        "WHERE j.job_run_id=c.job_run_id AND j.status='SUCCEEDED' AND o.outcome=c.outcome "
        "AND t.transaction_id=c.transaction_id AND w.financial_transaction_id=t.transaction_id) "
        "FROM financial_witness.claims c LEFT JOIN financial_witness.envelopes e USING(nonce) "
        "LEFT JOIN financial_witness.seals s USING(nonce) LEFT JOIN financial_witness.events v USING(nonce) "
        "WHERE c.authorization_sha256=:authorization GROUP BY c.nonce,e.nonce,s.nonce"
    ), {'authorization': authorization_sha256}).one_or_none()
    state = 'ABSENT' if row is None else 'RECOVERY_REQUIRED'
    if (row is not None and row[1] == row[2] == row[3] and row[4] == row[6]
            and row[5] == row[7] and row[8] is False and all(row[i] is True for i in (9, 10, 11))):
        state = 'FINANCIAL_COMMIT_RECORDED'
    return dict(schema='hope.prototype-write-set-recovery.v1', state=state,
        execution_authorized=False, replay_permitted=False, production_verification=False)
