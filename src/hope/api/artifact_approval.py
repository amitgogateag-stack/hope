"""Shared detached approval verification; never generates signing authority."""
from __future__ import annotations
import base64
import hmac
from pathlib import Path
import re
import subprocess
import time
from hope.api import runtime_integrity as r
from hope.api.snapshot_contract import strict_json

OPENSSL=Path("/usr/bin/openssl")


def verify_document(path,signature,public,*,expected_digest,deadline=None):
    r._require(type(expected_digest) is str and re.fullmatch(r"[0-9a-f]{64}",expected_digest),"APPROVAL_INVALID")
    deadline=deadline or time.monotonic()+r.MAX_SECONDS
    paths=tuple(map(Path,(path,signature,public)))
    before={str(p):r._trusted(p) for p in paths}
    parents={str(p.parent):r._trusted(p.parent,directory=True) for p in paths}
    payload=r._read(paths[0],r.MAX_MANIFEST_BYTES,deadline=deadline)
    value=strict_json(payload)
    r._require(payload==r._canonical(value)
        and hmac.compare_digest(r._digest(payload),expected_digest),"APPROVAL_INVALID")
    key=r._read(paths[2],1024,deadline=deadline)
    der=base64.b64decode(b"".join(key.splitlines()[1:-1]),validate=True)
    r._require(len(der)==44 and der.startswith(bytes.fromhex("302a300506032b6570032100")),"APPROVAL_KEY_INVALID")
    r._require(len(r._read(paths[1],64,deadline=deadline))==64,"APPROVAL_SIGNATURE_INVALID")
    r._trusted(OPENSSL.resolve(strict=True),system=True)
    result=subprocess.run([str(OPENSSL),"pkeyutl","-verify","-rawin","-pubin","-inkey",str(paths[2]),
        "-sigfile",str(paths[1]),"-in",str(paths[0])],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        timeout=max(.001,deadline-time.monotonic()),check=False,
        env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","OPENSSL_CONF":"/dev/null"})
    r._require(result.returncode==0,"APPROVAL_SIGNATURE_INVALID")
    r._require(all(r._trusted(Path(p))==identity for p,identity in before.items())
        and all(r._trusted(Path(p),directory=True)==identity for p,identity in parents.items())
        and time.monotonic()<=deadline,"APPROVAL_CHANGED")
    return value
