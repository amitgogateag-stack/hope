"""Authenticated *lab-only* observations, never production admission.

The supplied public key is a configured laboratory trust input. This module
does not provision/attest signer custody, observe another controller's kernel
or DB session, issue authority, sign documents, connect to a broker or run a
cycle. Production publisher_observation deliberately does NOT consume it.
Cryptography is loaded only when this optional laboratory API is invoked.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import secrets
from threading import Lock
import time
import re

from hope.api.snapshot_contract import strict_json

SCOPE = 'DISPOSABLE_LAB_ONLY'
DOMAIN = b'HOPE-LAB-PUBLISHER-V1\x00'
SCENARIOS = frozenset({'NO_TRADE', 'FILLED', 'RISK_BLOCKED', 'PARTIAL_FILL',
    'REJECTED', 'RESTART', 'DUPLICATE_CALLBACK', 'STALE_DATA', 'MISSING_DATA',
    'FAILED_RECONCILIATION', 'UNKNOWN_COMMIT', 'DATABASE_INTERRUPTION',
    'BULLISH_MECHANICS', 'BEARISH_MECHANICS'})
REASONS = frozenset({'NONE', 'NO_SIGNAL', 'MAX_POSITION_NOTIONAL_EXCEEDED',
    'STALE_DATA', 'NO_QUOTE', 'RECONCILIATION_FAILED', 'COMMIT_UNKNOWN',
    'BACKEND_LOST', 'DUPLICATE_DENIED', 'PARTIAL_COMPLETE', 'EXPECTED_REJECTION'})


class LabEvidenceError(ValueError):
    pass


def require(value, code):
    if not value:
        raise LabEvidenceError('LAB_PUBLISHER_' + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def hex_string(value, length):
    return type(value) is str and re.fullmatch(f'[0-9a-f]{{{length}}}', value) is not None


def unavailable():
    return dict(schema='hope.paper-lab-observation.v1', scope=SCOPE,
        state='UNAVAILABLE', signature='NOT_OBSERVED', cases=[],
        last_lab_cycle_at=None, production_admission='BLOCKED',
        execution_authorized=False, reason='LAB_EVIDENCE_UNAVAILABLE')


def validate_payload(value, issued):
    require(type(value) is dict and set(value) == {'postgres_major', 'cases'}, 'PAYLOAD_INVALID')
    require(value['postgres_major'] is None or
        (type(value['postgres_major']) is int and value['postgres_major'] in {16, 17}), 'PAYLOAD_INVALID')
    rows = value['cases']
    require(type(rows) is list and 0 < len(rows) <= 64, 'PAYLOAD_INVALID')
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == {'market', 'scenario', 'result',
            'reason', 'evidence_sha256', 'completed_at'}, 'PAYLOAD_INVALID')
        require(type(row['market']) is str and row['market'] in {'INDIA', 'USA'}
            and type(row['scenario']) is str and row['scenario'] in SCENARIOS
            and type(row['result']) is str and row['result'] in {'PASS', 'FAIL', 'NOT_EXECUTED'}
            and type(row['reason']) is str and row['reason'] in REASONS
            and hex_string(row['evidence_sha256'], 64), 'PAYLOAD_INVALID')
        identity = (row['market'], row['scenario'])
        require(identity not in seen, 'DUPLICATE_CASE')
        seen.add(identity)
        stamp = row['completed_at']
        require(stamp is None or (type(stamp) is str and len(stamp) <= 40), 'PAYLOAD_INVALID')
        if stamp is not None:
            try:
                parsed = datetime.fromisoformat(stamp)
                require(parsed.tzinfo is not None and parsed.astimezone(timezone.utc).isoformat() == stamp
                    and parsed.timestamp() <= issued, 'PAYLOAD_INVALID')
            except (ValueError, OverflowError) as exc:
                raise LabEvidenceError('LAB_PUBLISHER_PAYLOAD_INVALID') from exc
    return value


class LabPublisherVerifier:
    """One observer incarnation; concurrent reads cannot rewind its watermark.

    A fresh random challenge is mandatory on EVERY observer construction. An
    observer restart therefore rejects old signatures even within their TTL.
    Publisher restarts require a separately selected expected incarnation and
    a new verifier/challenge. There is no reattach/adopt/automatic reset route.
    Identical polls are idempotent but never refresh signed generation/expiry.
    The laboratory key input is NOT an independent production trust root.
    """
    def __init__(self, public_key: bytes, *, release_commit: str,
                 session: str, publisher_instance: str, clock=time.time):
        require(type(public_key) is bytes and len(public_key) == 32, 'KEY_INVALID')
        require(hex_string(release_commit, 40) and hex_string(session, 32)
            and hex_string(publisher_instance, 32), 'CONTEXT_INVALID')
        self._key = bytes(public_key)
        self.key_id = hashlib.sha256(public_key).hexdigest()
        self.release_commit = release_commit
        self.session = session
        self.publisher_instance = publisher_instance
        self.challenge = secrets.token_hex(32)
        self._clock = clock
        self._lock = Lock()
        self._revoked = False
        self._sequence = 0
        self._last_digest = None
        self._last_issued = 0

    def revoke(self):
        with self._lock:
            self._revoked = True

    def observe(self, packet: bytes):
        require(type(packet) is bytes and 0 < len(packet) <= 32768, 'PACKET_INVALID')
        try:
            document = strict_json(packet)
            require(type(document) is dict and set(document) == {'schema', 'scope',
                'key_id', 'release_commit', 'session', 'publisher_instance',
                'challenge', 'sequence', 'issued_at', 'expires_at', 'payload', 'signature'}
                and canonical(document) == packet, 'PACKET_INVALID')
            require(document['schema'] == 'hope.lab-publisher.v1' and document['scope'] == SCOPE,
                'SCOPE_INVALID')
            require(document['key_id'] == self.key_id
                and document['release_commit'] == self.release_commit
                and document['session'] == self.session
                and document['publisher_instance'] == self.publisher_instance
                and document['challenge'] == self.challenge, 'CONTEXT_MISMATCH')
            sequence, issued, expiry = (document[k] for k in ('sequence', 'issued_at', 'expires_at'))
            require(type(sequence) is int and 1 <= sequence <= 9007199254740991
                and type(issued) is int and type(expiry) is int and 0 < issued < expiry <= issued + 15,
                'TIME_OR_SEQUENCE_INVALID')
            require(type(document['signature']) is str, 'SIGNATURE_INVALID')
            signature = base64.b64decode(document['signature'], validate=True)
            require(len(signature) == 64 and base64.b64encode(signature).decode() == document['signature'],
                'SIGNATURE_INVALID')
            signed = {k: v for k, v in document.items() if k != 'signature'}
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            try:
                Ed25519PublicKey.from_public_bytes(self._key).verify(signature, DOMAIN + canonical(signed))
            except InvalidSignature as exc:
                raise LabEvidenceError('LAB_PUBLISHER_SIGNATURE_INVALID') from exc
            payload = validate_payload(document['payload'], issued)
            digest = hashlib.sha256(packet).hexdigest()
            with self._lock:
                require(not self._revoked, 'KEY_REVOKED')
                require(issued <= self._clock() < expiry, 'EXPIRED_OR_FUTURE')
                require((sequence == self._sequence and digest == self._last_digest)
                    or (sequence == self._sequence + 1 and issued >= self._last_issued), 'REPLAY_OR_GAP')
                self._sequence, self._last_digest, self._last_issued = sequence, digest, issued
            completed = [r['completed_at'] for r in payload['cases'] if r['completed_at'] is not None]
            return dict(schema='hope.paper-lab-observation.v1', scope=SCOPE, state='LAB_OBSERVED',
                signature='VALID_FOR_CONFIGURED_LAB_KEY', release_commit=self.release_commit,
                sequence=sequence, generated_at=issued, expires_at=expiry,
                postgres_major=payload['postgres_major'], cases=payload['cases'],
                last_lab_cycle_at=max(completed) if completed else None,
                production_admission='BLOCKED', execution_authorized=False,
                reason='LAB_KEY_IS_NOT_PRODUCTION_ADMISSION')
        except LabEvidenceError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, binascii.Error) as exc:
            raise LabEvidenceError('LAB_PUBLISHER_PACKET_INVALID') from exc


def read_observation(path, verifier):
    """Read-only bounded lab projection; no production fallback or authority."""
    if path is None or type(verifier) is not LabPublisherVerifier:
        return unavailable()
    try:
        from hope.infrastructure.durable_file import bounded_regular_read
        return verifier.observe(bounded_regular_read(path, 32768))
    except (OSError, ValueError, ImportError):
        return unavailable()
