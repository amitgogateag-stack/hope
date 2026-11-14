"""Bounded runtime manifests, authenticated independently of the inspected venv.

No installer, imports from the inspected environment, credential reads or host
mutation. A generated inventory is a candidate, never its own approval.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from threading import Lock
import time

from hope.api.snapshot_contract import strict_json

OWNER=0
SCHEMA="hope.runtime-manifest.v1"
MAX_FILES=20000
MAX_BYTES=512*1024*1024
MAX_FILE_BYTES=128*1024*1024
MAX_MANIFEST_BYTES=4*1024*1024
MAX_SECONDS=3.0
REQUIRED={"sqlalchemy","pydantic","pydantic-core","psycopg","psycopg-binary","alembic"}
_CACHE={}
_LOCK=Lock()


def _require(condition,code):
    if not condition:raise RuntimeError("RUNTIME_"+code)


def _sha(value):
    _require(isinstance(value,str) and re.fullmatch("[0-9a-f]{40}",value),"RELEASE_INVALID")
    _require(value!="7e9d341742818a97accce3e2301b1c7f96596795","FROZEN_RELEASE_DENIED")
    return value


def _identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
        info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _trusted(path,*,directory=False,system=False):
    info=path.lstat()
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid in ({0,OWNER} if system else {OWNER}) and not info.st_mode&0o022
        and (directory or info.st_nlink==1),"STORAGE_UNTRUSTED")
    return _identity(info)


def _read(path,limit,*,deadline=None,system=False):
    before=_trusted(path,system=system)
    _require(before[6]<=limit,"FILE_LIMIT")
    flags=os.O_RDONLY|getattr(os,"O_BINARY",0)|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)
    fd=os.open(path,flags)
    try:
        _require(_identity(os.fstat(fd))==before,"FILE_CHANGED")
        result=bytearray()
        while len(result)<=limit:
            if deadline is not None:_require(time.monotonic()<=deadline,"DEADLINE")
            block=os.read(fd,min(65536,limit+1-len(result)))
            if not block:break
            result.extend(block)
        _require(len(result)==before[6] and len(result)<=limit
            and _identity(os.fstat(fd))==before and _trusted(path,system=system)==before,"FILE_CHANGED")
        return bytes(result)
    finally:os.close(fd)


def _fingerprint(path,deadline,*,system=False):
    """Hash large binary/package files with constant bounded read memory."""
    before=_trusted(path,system=system);_require(before[6]<=MAX_FILE_BYTES,"FILE_LIMIT")
    flags=os.O_RDONLY|getattr(os,"O_BINARY",0)|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)
    fd=os.open(path,flags)
    try:
        _require(_identity(os.fstat(fd))==before,"FILE_CHANGED")
        digest=hashlib.sha256();size=0;header=bytearray()
        while True:
            _require(time.monotonic()<=deadline,"DEADLINE")
            block=os.read(fd,65536)
            if not block:break
            size+=len(block);_require(size<=MAX_FILE_BYTES,"FILE_LIMIT");digest.update(block)
            if len(header)<65536:header.extend(block[:65536-len(header)])
        _require(size==before[6] and _identity(os.fstat(fd))==before
            and _trusted(path,system=system)==before,"FILE_CHANGED")
        return digest.hexdigest(),bytes(header)
    finally:os.close(fd)


def _canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def _digest(value):return hashlib.sha256(value).hexdigest()
def _name(value):return re.sub(r"[-_.]+","-",value).lower()


def environment_path(root,sha,mode):
    _sha(sha);_require(mode in {"shared","release"},"MODE_INVALID")
    return root/"venv" if mode=="shared" else root/"release-venvs"/sha


def manifest_path(root,sha,mode):
    environment_path(root,sha,mode)
    return root/"runtime-manifests"/(sha+"-"+mode+".json")


def source_snapshot(release):
    """Authenticate source storage and forbid ignored executable bytecode.

    Git authentication remains owned by operations_deployment.inspect_release.
    This additionally closes the ignored-cache import boundary used by Python.
    """
    root=Path(release)/"src";pending=[root];result={};count=0;deadline=time.monotonic()+MAX_SECONDS
    while pending:
        directory=pending.pop();result[str(directory)]=_trusted(directory,directory=True)
        with os.scandir(directory) as entries:
            for entry in entries:
                count+=1;_require(count<=MAX_FILES and time.monotonic()<=deadline,"SOURCE_LIMIT")
                path=directory/entry.name
                _require(entry.name!="__pycache__" and path.suffix not in {".pyc",".pyo"},"SOURCE_CACHE_DENIED")
                if path.is_dir():
                    result[str(path)]=_trusted(path,directory=True);pending.append(path)
                else:result[str(path)]=_trusted(path)
    return result


def _inventory(root,sha,mode,deadline):
    root=Path(root);_trusted(root,directory=True)
    _require(root.is_absolute() and root.resolve(strict=True)==root,"ROOT_UNTRUSTED")
    environment=environment_path(root,sha,mode)
    if mode=="release":_trusted(root/"release-venvs",directory=True)
    _trusted(environment,directory=True);_trusted(environment/"bin",directory=True)
    python=environment/"bin/python";resolved=python.resolve(strict=True)
    for parent in resolved.parents:
        if parent==root:break
        _trusted(parent,directory=True,system=True)
    executable_identity=_trusted(resolved,system=True)
    _require(executable_identity[2]&0o111,"INTERPRETER_NOT_EXECUTABLE")
    directories={};files={};links={};size=0;count=0
    pending=[environment]
    while pending:
        _require(time.monotonic()<=deadline,"DEADLINE")
        base=pending.pop()
        directories[base.relative_to(environment).as_posix()]=_trusted(base,directory=True)
        with os.scandir(base) as entries:
            for entry in entries:
                _require(time.monotonic()<=deadline,"DEADLINE")
                count+=1;_require(count<=MAX_FILES,"ENTRY_LIMIT")
                path=base/entry.name;relative=path.relative_to(environment).as_posix()
                if path.is_symlink():
                    info=path.lstat();_require(info.st_uid==OWNER,"LINK_UNTRUSTED")
                    target=path.resolve(strict=True)
                    internal=target.is_relative_to(environment)
                    _require(internal or (path.parent==environment/"bin"
                        and re.fullmatch(r"python(?:3(?:\.13)?)?",entry.name)
                        and target==resolved),"LINK_TARGET_DENIED")
                    _require(not internal or target!=environment,"LINK_TARGET_DENIED")
                    links[relative]=("@runtime/"+target.relative_to(environment).as_posix() if internal
                        else "@system/"+str(target),_identity(info))
                elif path.is_dir():
                    _trusted(path,directory=True);pending.append(path)
                else:
                    identity=_trusted(path);size+=identity[6]
                    _require(identity[6]<=MAX_FILE_BYTES and size<=MAX_BYTES,"BYTE_LIMIT")
                    files[relative]=identity
    _require(time.monotonic()<=deadline,"DEADLINE")
    _require("pyvenv.cfg" in files,"VENV_CONFIGURATION_MISSING")
    signature=(directories,files,links,str(resolved),executable_identity)
    return environment,python,resolved,signature


def _pins(release,deadline=None):
    # These bytes are authenticated by the existing Git release verifier before
    # production callers enter this module; this helper never runs Git hooks.
    payload=_read(release/"constraints/ci-test.txt",65536,deadline=deadline)
    pins={}
    for line in payload.decode("utf-8").splitlines():
        line=line.strip()
        if not line or line.startswith("#"):continue
        match=re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9.+_-]+)",line)
        _require(match is not None,"CONSTRAINTS_INVALID")
        name=_name(match[1]);_require(name not in pins,"CONSTRAINTS_INVALID");pins[name]=match[2]
    _require(REQUIRED<=pins.keys(),"CONSTRAINTS_INCOMPLETE")
    return payload,pins


def build_runtime_manifest(root,sha,mode,*,release=None,_deadline=None,provenance_digest=None,build_digest=None):
    """Inventory candidate only. A trusted builder/operator must approve it.

    Never save this automatically beside the runtime during activation: that
    would turn the inspected environment into its own authority.
    """
    root=Path(root);release=Path(release) if release else root/"releases"/_sha(sha)
    deadline=time.monotonic()+MAX_SECONDS if _deadline is None else _deadline
    constraints,pins=_pins(release,deadline)
    environment,python,resolved,before=_inventory(root,sha,mode,deadline)
    configuration={}
    for line in _read(environment/"pyvenv.cfg",65536,deadline=deadline).decode("utf-8").splitlines():
        key,separator,value=line.partition("=")
        if separator:
            key=key.strip();_require(key not in configuration,"VENV_CONFIGURATION_INVALID");configuration[key]=value.strip()
    _require(configuration.get("include-system-site-packages")=="false"
        and re.fullmatch(r"3\.13(?:\.\d+)?",configuration.get("version","")),"VENV_CONFIGURATION_INVALID")
    hashes={};distributions={}
    for relative in before[1]:
        path=environment/relative
        hashes[relative],payload=_fingerprint(path,deadline)
        if path.suffix==".pth":
            _require(before[1][relative][6]<=65536,"IMPORT_HOOK_DENIED")
            for line in payload.decode("utf-8").splitlines():
                line=line.strip()
                if not line or line.startswith("#"):continue
                if line.startswith(("import ","import\t")):
                    _require(path.name=="distutils-precedence.pth","IMPORT_HOOK_DENIED")
                else:_require((path.parent/line).resolve(strict=True).is_relative_to(environment),"IMPORT_PATH_DENIED")
        if path.name=="METADATA" and path.parent.name.endswith(".dist-info"):
            _require(b"\n\n" in payload or len(payload)<65536,"DEPENDENCIES_INVALID")
            header=payload[:65536].split(b"\n\n",1)[0].decode("utf-8")
            fields={}
            for line in header.splitlines():
                key,separator,value=line.partition(":")
                if key in {"Name","Version"}:
                    _require(separator and key not in fields,"DEPENDENCIES_INVALID");fields[key]=value.strip()
            _require(set(fields)=={"Name","Version"},"DEPENDENCIES_INVALID")
            name=_name(fields["Name"]);version=fields["Version"]
            _require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}",name)
                and re.fullmatch(r"[A-Za-z0-9.+_-]{1,128}",version)
                and name not in distributions,"DEPENDENCIES_INVALID")
            _require(name not in pins or version==pins[name],"DEPENDENCIES_MISMATCH")
            distributions[name]=version
    _require(REQUIRED<=distributions.keys(),"DEPENDENCIES_INCOMPLETE")
    interpreter=_fingerprint(resolved,deadline)[0]
    _require(_inventory(root,sha,mode,deadline)[3]==before,"ENVIRONMENT_CHANGED")
    result=dict(schema=SCHEMA,repository="amitgogateag-stack/hope",release_commit=sha,runtime_mode=mode,
        constraints_sha256=_digest(constraints),interpreter=dict(resolved=str(resolved),sha256=interpreter),
        distributions=distributions,files=hashes,links={key:value[0] for key,value in before[2].items()})
    if provenance_digest is not None:
        _require(isinstance(provenance_digest,str) and re.fullmatch("[0-9a-f]{64}",provenance_digest),"PROVENANCE_DIGEST_INVALID")
        result.update(schema="hope.runtime-manifest.v2",package_provenance_sha256=provenance_digest)
    if build_digest is not None:
        _require(provenance_digest is not None and isinstance(build_digest,str) and re.fullmatch("[0-9a-f]{64}",build_digest),"BUILD_DIGEST_INVALID")
        result.update(schema="hope.runtime-manifest.v3",release_build_sha256=build_digest)
    return result


def attest_runtime(root,sha,mode,*,expected_manifest=None,release=None,timeout=MAX_SECONDS,release_source=None):
    """Verify an independently staged root-owned manifest; emit no paths/code."""
    _require(type(timeout) in {int,float} and 0<timeout<=MAX_SECONDS,"DEADLINE")
    _require(expected_manifest is None or (type(expected_manifest) is str
        and re.fullmatch("[0-9a-f]{64}",expected_manifest)),"MANIFEST_CHANGED")
    deadline=time.monotonic()+timeout
    root=Path(root);path=manifest_path(root,sha,mode)
    manifest_directory=_trusted(root/"runtime-manifests",directory=True)
    manifest_identity=_trusted(path)
    payload=_read(path,MAX_MANIFEST_BYTES,deadline=deadline);approved=strict_json(payload)
    fields={"schema","repository","release_commit","runtime_mode","constraints_sha256","interpreter","distributions","files","links"}
    _require(isinstance(approved,dict) and ((approved.get("schema")==SCHEMA and set(approved)==fields)
        or (approved.get("schema")=="hope.runtime-manifest.v2" and set(approved)==fields|{"package_provenance_sha256"})
        or (approved.get("schema")=="hope.runtime-manifest.v3" and set(approved)==fields|{"package_provenance_sha256","release_build_sha256"})),"MANIFEST_INVALID")
    digest=_digest(_canonical(approved))
    _require(expected_manifest is None or hmac.compare_digest(digest,expected_manifest),"MANIFEST_CHANGED")
    _require(approved.get("repository")=="amitgogateag-stack/hope"
        and approved.get("release_commit")==sha and approved.get("runtime_mode")==mode,"MANIFEST_IDENTITY_MISMATCH")
    # Cache only a previously hashed immutable inventory. Every call still scans
    # all names/metadata, the external manifest and pinned constraints. ctime and
    # inode changes invalidate the cache even if bytes/mtime were restored.
    release=Path(release) if release else root/"releases"/sha
    release_identity=_trusted(release,directory=True)
    constraints_directory=_trusted(release/"constraints",directory=True)
    constraints_identity=_trusted(release/"constraints/ci-test.txt")
    constraints,_=_pins(release,deadline)
    inventory=_inventory(root,sha,mode,deadline)[3]
    key=(str(root),sha,mode,digest)
    with _LOCK:cached=_CACHE.get(key)
    if cached!=(inventory,_digest(constraints)):
        actual=build_runtime_manifest(root,sha,mode,release=release,_deadline=deadline,
            provenance_digest=approved.get("package_provenance_sha256"),build_digest=approved.get("release_build_sha256"))
        _require(actual==approved,"CONTENT_MISMATCH")
        _require(_inventory(root,sha,mode,deadline)[3]==inventory,"ENVIRONMENT_CHANGED")
        with _LOCK:
            if len(_CACHE)>=8:_CACHE.clear()
            _CACHE[key]=(inventory,_digest(constraints))
    provenance=dict(state="UNVERIFIED",sha256=None)
    if approved["schema"] in {"hope.runtime-manifest.v2","hope.runtime-manifest.v3"}:
        from hope.api.package_provenance import verify_provenance
        provenance=verify_provenance(root,sha,mode,runtime_manifest=approved,
            expected_digest=approved["package_provenance_sha256"],deadline=deadline)
        _require(_inventory(root,sha,mode,deadline)[3]==inventory,"ENVIRONMENT_CHANGED")
    build=dict(state="UNVERIFIED",sha256=None,builder_id=None)
    if approved["schema"]=="hope.runtime-manifest.v3":
        from hope.api.release_build_evidence import verify_release_build
        build=verify_release_build(root,sha,mode,runtime_manifest=approved,
            expected_digest=approved["release_build_sha256"],deadline=deadline,source_path=release_source)
        _require(_inventory(root,sha,mode,deadline)[3]==inventory,"ENVIRONMENT_CHANGED")
    _require(_read(path,MAX_MANIFEST_BYTES,deadline=deadline)==payload and _trusted(path)==manifest_identity
        and _trusted(root/"runtime-manifests",directory=True)==manifest_directory,"MANIFEST_CHANGED")
    _require(_trusted(release,directory=True)==release_identity
        and _trusted(release/"constraints",directory=True)==constraints_directory
        and _trusted(release/"constraints/ci-test.txt")==constraints_identity,"RELEASE_CHANGED")
    _require(time.monotonic()<=deadline,"DEADLINE")
    result=dict(schema="hope.runtime-integrity.v1",state="VERIFIED",code="MANIFEST_AND_RUNTIME_MATCH",
        release_commit=sha,runtime_mode=mode,manifest_sha256=digest,
        inventory_sha256=_digest(_canonical((inventory,manifest_identity,constraints_identity,provenance.get("identity_sha256"),build.get("identity_sha256")))),process_sha256=None,
        file_count=len(approved["files"]),execution_authorized=False,
        provenance_state=provenance["state"],provenance_sha256=provenance["sha256"],
        build_state=build["state"],build_sha256=build["sha256"],builder_id=build["builder_id"])
    if release_source is not None:
        _require(approved["schema"]=="hope.runtime-manifest.v3" and isinstance(build.get("source_sha256"),str),
            "BUILD_SOURCE_UNVERIFIED")
        result["release_source_sha256"]=build["source_sha256"]
    return result


def diagnostic(root,sha,mode,*,expected_manifest=None,release=None):
    try:return attest_runtime(root,sha,mode,expected_manifest=expected_manifest,release=release)
    except (OSError,ValueError,TypeError,RuntimeError):
        return dict(schema="hope.runtime-integrity.v1",state="UNVERIFIED" if expected_manifest is None else "MISMATCH",
            code="RUNTIME_EVIDENCE_UNVERIFIED",release_commit=sha,runtime_mode=mode,
            manifest_sha256=None,inventory_sha256=None,process_sha256=None,file_count=0,execution_authorized=False,
            provenance_state="UNVERIFIED",provenance_sha256=None)


def unverified(code="LEGACY_JOURNAL_UNBOUND",*,blocked=False):
    return dict(schema="hope.runtime-integrity.v1",state="BLOCKED" if blocked else "UNVERIFIED",
        code=code,release_commit=None,runtime_mode=None,manifest_sha256=None,inventory_sha256=None,process_sha256=None,
        file_count=0,execution_authorized=False,provenance_state="UNVERIFIED",provenance_sha256=None)


def safe_evidence(value):
    if value is None:return unverified()
    fields={"schema","state","code","release_commit","runtime_mode","manifest_sha256","inventory_sha256","process_sha256","file_count","execution_authorized"}
    _require(isinstance(value,dict) and set(value) in (fields,fields|{"provenance_state","provenance_sha256"},
        fields|{"provenance_state","provenance_sha256","build_state","build_sha256","builder_id"}) and value["schema"]=="hope.runtime-integrity.v1"
        and value["state"] in {"VERIFIED","MISMATCH","UNVERIFIED","BLOCKED"}
        and value["code"] in {"MANIFEST_AND_RUNTIME_MATCH","RUNTIME_EVIDENCE_UNVERIFIED",
            "LEGACY_JOURNAL_UNBOUND","ACTIVATION_BLOCKED","RELEASE_OR_FENCE_UNVERIFIED"}
        and value["execution_authorized"] is False
        and type(value["file_count"]) is int and 0<=value["file_count"]<=MAX_FILES,"EVIDENCE_INVALID")
    if value["state"]=="VERIFIED":
        _sha(value["release_commit"])
        _require(value["runtime_mode"] in {"shared","release"}
            and isinstance(value["manifest_sha256"],str) and re.fullmatch("[0-9a-f]{64}",value["manifest_sha256"])
            and isinstance(value["inventory_sha256"],str) and re.fullmatch("[0-9a-f]{64}",value["inventory_sha256"])
            and (value["process_sha256"] is None or (isinstance(value["process_sha256"],str)
                and re.fullmatch("[0-9a-f]{64}",value["process_sha256"])))
            and value["code"]=="MANIFEST_AND_RUNTIME_MATCH" and value["file_count"]>0,"EVIDENCE_INVALID")
    else:
        _require(value["manifest_sha256"] is None and value["inventory_sha256"] is None
            and value["process_sha256"] is None and value["file_count"]==0,"EVIDENCE_INVALID")
        _require(value["release_commit"] is None or (isinstance(value["release_commit"],str)
            and re.fullmatch("[0-9a-f]{40}",value["release_commit"])),"EVIDENCE_INVALID")
        _require(value["runtime_mode"] in {None,"shared","release"},"EVIDENCE_INVALID")
    result=dict(value,provenance_state=value.get("provenance_state","UNVERIFIED"),provenance_sha256=value.get("provenance_sha256"))
    result.update(build_state=value.get("build_state","UNVERIFIED"),build_sha256=value.get("build_sha256"),builder_id=value.get("builder_id"))
    _require((result["build_state"]=="UNVERIFIED" and result["build_sha256"] is None and result["builder_id"] is None)
        or (result["state"]=="VERIFIED" and result["provenance_state"]=="VERIFIED" and result["build_state"]=="VERIFIED"
            and all(isinstance(result[k],str) and re.fullmatch("[0-9a-f]{64}",result[k]) for k in ("build_sha256","builder_id"))),"BUILD_EVIDENCE_INVALID")
    _require((result["provenance_state"]=="UNVERIFIED" and result["provenance_sha256"] is None)
        or (result["state"]=="VERIFIED" and result["provenance_state"]=="VERIFIED"
            and isinstance(result["provenance_sha256"],str) and re.fullmatch("[0-9a-f]{64}",result["provenance_sha256"])),"PROVENANCE_EVIDENCE_INVALID")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description="Read-only HOPE runtime integrity diagnostic; never installs or activates")
    parser.add_argument("target");parser.add_argument("--runtime",choices=("shared","release"),default="shared")
    args=parser.parse_args(argv)
    from hope.api import operations_deployment as operations
    root=Path("/opt/hope-operations")
    try:
        _sha(args.target)
        with operations._activation_guard(root,shared=True):
            operations.inspect_release(root/"releases"/args.target,args.target)
            result=diagnostic(root,args.target,args.runtime)
            operations.inspect_release(root/"releases"/args.target,args.target)
    except (OSError,ValueError,TypeError,RuntimeError):
        result=unverified("RELEASE_OR_FENCE_UNVERIFIED",blocked=True)
    print(json.dumps(result,sort_keys=True));return 0 if result["state"]=="VERIFIED" else 2


if __name__=="__main__":raise SystemExit(main())
