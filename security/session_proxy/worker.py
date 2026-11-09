"""Untrusted disposable controller: forwards packets, never receives DB secrets."""
import json
import os
import socket
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as peer:
    peer.connect(sys.argv[1])
    print(peer.recv(16384).decode(), flush=True)
    for line in sys.stdin:
        value=json.loads(line)
        if value == {'replace': True}:
            os.set_inheritable(peer.fileno(), True)
            os.execv('/bin/sleep', ['/bin/sleep', '15'])
        if value == {'delegate': True}:
            child=os.fork()
            if child == 0:
                peer.sendall(b'{"commit":true}')
                os._exit(0)
            os.waitpid(child,0)
        else:
            peer.sendall(line.encode())
        response=peer.recv(16384)
        if not response:break
        print(response.decode(), flush=True)
