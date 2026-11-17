"""Experimental local session-terminating broker. NEVER production admission.

Runs as a distinct unprivileged UID with CAP_SYS_PTRACE for kernel observations.
Owns the upstream PostgreSQL connection; does not forward worker-selected SQL.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import select
import secrets
import socket
import struct
import sys
import threading
import time

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from execution_fence import ExecutionFence, FenceDenied

LIMIT = 16384


class Denied(Exception):
    pass


def require(value, reason):
    if not value:
        raise Denied(reason)


def canonical(value):
    return b'hope.session-prototype.v1\x00' + json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def observation(pid):
    root = Path('/proc') / str(pid)
    stat = (root / 'stat').read_text().rsplit(')', 1)[1].split()
    with (root / 'exe').open('rb') as stream:
        executable = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(pid=pid, start_ticks=stat[19], executable_sha256=executable,
                namespaces={n: os.readlink(root / 'ns' / n) for n in ('mnt', 'pid', 'net', 'user')})


def receive(peer, expected, current):
    deadline = time.monotonic() + 12
    while not select.select([peer], [], [], 0.05)[0]:
        current()
        require(time.monotonic() < deadline, 'PEER_TIMEOUT')
    current()
    payload, ancillary, flags, _ = peer.recvmsg(LIMIT, socket.CMSG_SPACE(struct.calcsize('3i')))
    require(payload and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), 'PACKET_INVALID')
    credentials = [struct.unpack('3i', data[:12]) for level, kind, data in ancillary
                   if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
    require(credentials == [expected], 'MESSAGE_KERNEL_IDENTITY_MISMATCH')
    value = json.loads(payload)
    require(isinstance(value, dict), 'PACKET_INVALID')
    return value


def send(peer, value):
    peer.sendall(json.dumps(value, sort_keys=True).encode())


def serve(config_path):
    config = json.loads(Path(config_path).read_text())
    require(sys.platform == 'linux' and os.geteuid() != 0, 'UNPRIVILEGED_LINUX_BROKER_REQUIRED')
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(config['public_key']))
    audit_lock = threading.Lock()
    capacity = threading.BoundedSemaphore(8)

    def audit(value):
        with audit_lock:
            with Path(config['audit']).open('a') as stream:
                stream.write(json.dumps(value, sort_keys=True) + '\n')

    def handle(peer):
        pidfd = None
        fence = None
        nonce = None
        credentials = None
        commit_started = committed = False
        try:
            peer.settimeout(12)
            credentials = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            pid, uid, gid = credentials
            require(uid == config['worker_uid'], 'WRONG_KERNEL_UID')
            pidfd = os.pidfd_open(pid)
            fence = ExecutionFence(pid)
            identity = observation(pid)
            require(identity['executable_sha256'] == config['executable_sha256'], 'WRONG_EXECUTABLE')
            require(identity['namespaces'] == config['namespaces'], 'WRONG_NAMESPACE')

            def current():
                fence.check()
                require(not select.select([pidfd], [], [], 0)[0], 'PROCESS_EXITED')
                require(observation(pid) == identity, 'PROCESS_REPLACED')

            current()
            execution_lease = dict(kind='ptrace-single-thread-since-attach-v1',
                                   tracer_tid=fence.tid, lease_nonce=secrets.token_hex(32))
            fence.resume()

            with psycopg.connect(config['dsn'], autocommit=True) as db:
                db.execute("SET statement_timeout='5000ms'")
                db.execute("SET lock_timeout='2000ms'")
                db.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
                try:
                    # Obtained on the exact connection that will insert/COMMIT.
                    session = db.execute("SELECT pg_backend_pid(),pg_current_xact_id()::text,session_user,"
                        "(SELECT oid FROM pg_database WHERE datname=current_database()),"
                        "(SELECT backend_start::text FROM pg_stat_activity WHERE pid=pg_backend_pid()),"
                        "(SELECT system_identifier::text FROM pg_control_system()),"
                        "extract(epoch FROM clock_timestamp())::double precision").fetchone()
                    state = db.execute('SELECT halted,revoked,generation FROM session_prototype.control WHERE id=1').fetchone()
                    require(not state[0] and not state[1], 'POLICY_DENIED')
                    nonce = secrets.token_hex(32)
                    transcript = dict(nonce=nonce, peer=identity, uid=uid, gid=gid,
                        execution_lease=execution_lease,
                        backend_pid=session[0], xid=session[1], login=session[2], database_oid=session[3],
                        backend_start=session[4], cluster=session[5], expires_at=session[6]+3,
                        generation=state[2], release_tree=config['release_tree'],
                        issuer_role='prototype-independent-authority', scope='ONE_PROTOTYPE_EFFECT', cycle=nonce)
                    send(peer, {'challenge': transcript})
                    response = receive(peer, credentials, current)
                    require(set(response) == {'signature'}, 'UNTRUSTED_FIELDS')
                    key.verify(bytes.fromhex(response['signature']), canonical(transcript))
                    current()
                    db.execute('INSERT INTO session_prototype.approvals(nonce,transcript,expires_at,generation) '
                        'VALUES(%s,%s::jsonb,to_timestamp(%s),%s)',
                        (nonce, json.dumps(transcript), transcript['expires_at'], transcript['generation']))
                    db.execute('INSERT INTO session_prototype.effects VALUES(%s,1)', (nonce,))
                    send(peer, {'prepared': nonce})
                    command = receive(peer, credentials, current)
                    require(set(command) == {'commit'} and command['commit'] is True, 'COMMIT_PROTOCOL')
                    current()
                    with fence.stopped():
                        current()
                        audit(dict(nonce=nonce, result='COMMIT_BARRIER_HELD', peer=identity,
                                   tracer_tid=fence.tid, backend_pid=session[0], xid=session[1]))
                        commit_started = True
                        db.execute('COMMIT')
                        committed = True
                    audit(dict(nonce=nonce, result='COMMITTED', peer=identity, session=list(session[:6])))
                    send(peer, {'result': 'COMMITTED', 'nonce': nonce})
                except Exception:
                    # Never turn a lost COMMIT response into a claim of rollback.
                    # Closing an uncertain connection is not authority to replay.
                    if not commit_started:
                        db.execute('ROLLBACK')
                    raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (Denied, FenceDenied)) else type(exc).__name__
            result = 'COMMITTED_ACK_LOST' if committed else 'RECOVERY_REQUIRED' if commit_started else 'DENIED'
            if isinstance(exc, psycopg.errors.CheckViolation) and exc.diag.message_primary in {
                    'PROTOTYPE_HALTED', 'PROTOTYPE_AUTHORITY_REVOKED', 'PROTOTYPE_AUTHORITY_EXPIRED',
                    'PROTOTYPE_COMMIT_DENIED', 'PROTOTYPE_COMMIT_CONTEXT'}:
                reason = exc.diag.message_primary
                result = 'DENIED'
            audit(dict(nonce=nonce, result=result, reason=reason,
                       peer_pid=None if credentials is None else credentials[0],
                       kernel_events=[] if fence is None else fence.events))
            try:
                send(peer, {'result': result, 'reason': reason})
            except OSError:
                pass
        finally:
            if fence is not None:
                fence.close()
            if pidfd is not None:
                os.close(pidfd)
            peer.close()
            capacity.release()

    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        listener.bind(config['socket'])
        os.chown(config['socket'], -1, config['socket_gid'])
        os.chmod(config['socket'], 0o660)
        listener.listen(16)
        audit({'result': 'LISTENING', 'uid': os.getuid()})
        while True:
            peer, _ = listener.accept()
            if capacity.acquire(blocking=False):
                threading.Thread(target=handle, args=(peer,), daemon=True).start()
            else:
                peer.close()


if __name__ == '__main__':
    serve(sys.argv[1])
