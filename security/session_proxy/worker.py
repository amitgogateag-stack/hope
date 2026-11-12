"""Untrusted disposable controller: forwards packets, never receives DB secrets."""
import json
import os
import socket
import sys
import array
import time

delegation = None
if len(sys.argv) > 2 and sys.argv[2] == 'delegate':
    delegation, child_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    child = os.fork()  # Before connecting/attaching the controller execution lease.
    if child == 0:
        delegation.close()
        for fd in (0, 1, 2): os.close(fd)
        _, ancillary, _, _ = child_end.recvmsg(1, socket.CMSG_SPACE(array.array('i').itemsize))
        fds = array.array('i')
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS: fds.frombytes(data)
        with socket.socket(fileno=fds[0]) as shared:
            shared.sendall(b'{"commit":true}')
            child_end.recv(1)  # Keep SIGCHLD out of the credential experiment.
        os._exit(0)
    child_end.close()

with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as peer:
    peer.connect(sys.argv[1])
    print(peer.recv(16384).decode(), flush=True)
    for line in sys.stdin:
        value=json.loads(line)
        if value == {'replace': True}:
            os.set_inheritable(peer.fileno(), True)
            os.execv('/bin/sleep', ['/bin/sleep', '15'])
        if value == {'same_exec': True}:
            os.set_inheritable(peer.fileno(), True)
            code = "import socket; p=socket.socket(fileno="+str(peer.fileno())+"); p.sendall(b'{\"commit\":true}'); print('UNAUTHORIZED_SECOND_PROGRAM',flush=True); print(p.recv(16384),flush=True)"
            os.execv(sys.executable, [sys.executable, '-c', code])
        if value == {'new_thread': True}:
            import threading
            threading.Thread(target=lambda: time.sleep(15), daemon=True).start()
            continue
        if value == {'drop_ack': True}:
            peer.shutdown(socket.SHUT_RD)
            peer.sendall(b'{"commit":true}')
            print(json.dumps('ACK_UNAVAILABLE'), flush=True)
            sys.stdin.read()
            break
        if value == {'delegate': True}:
            delegation.sendmsg([b'x'], [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[peer.fileno()]))])
        else:
            peer.sendall(line.encode())
        response=peer.recv(16384)
        if not response:break
        print(response.decode(), flush=True)
