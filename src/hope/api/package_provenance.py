"""Offline wheel provenance. Candidates never carry approval or signing keys.

Approval is an Ed25519 detached signature by a separately controlled builder.
Only a root-owned external public key is accepted. No package code is imported.
"""
from __future__ import annotations

import ast
import base64
import configparser
import csv
from email.parser import BytesParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import time
import zipfile

from hope.api import runtime_integrity as integrity
from hope.api.snapshot_contract import strict_json

SCHEMA="hope.package-provenance.v1"
MAX_WHEELS=128
MAX_ENTRIES=20000
MAX_EXPANDED=256*1024*1024
MAX_WHEEL=64*1024*1024
OPENSSL=Path("/usr/bin/openssl")


def _check(value,code):
    if not value:raise RuntimeError("PROVENANCE_"+code)


def _relative(name):
    _check(isinstance(name,str) and 0<len(name)<=512 and "\\" not in name
        and not name.startswith("/") and all(part not in {"", ".", ".."} for part in name.split("/")),"PATH_INVALID")
    _check(not any(ord(c)<32 for c in name) and ":" not in name,"PATH_INVALID")
    return name


def target_platform():
    return dict(system=platform.system(),machine=platform.machine(),python="3.13",implementation="cpython")


def _marker(expression,extras):
    if not expression:return True
    _check(len(expression)<=1024,"REQUIREMENT_INVALID")
    tree=ast.parse(expression,mode="eval")
    _check(sum(1 for _ in ast.walk(tree))<=64,"REQUIREMENT_INVALID")
    context=dict(python_version="3.13",python_full_version="3.13.0",sys_platform=os.sys.platform,
        platform_system=platform.system(),platform_machine=platform.machine(),
        implementation_name="cpython",platform_python_implementation="CPython")
    def evaluate(node,extra):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
        if isinstance(node,ast.Name):
            _check(node.id in context or node.id=="extra","MARKER_UNSUPPORTED")
            return extra if node.id=="extra" else context[node.id]
        if isinstance(node,ast.BoolOp) and isinstance(node.op,(ast.And,ast.Or)):
            values=[evaluate(item,extra) for item in node.values]
            _check(all(type(v) is bool for v in values),"MARKER_UNSUPPORTED")
            return all(values) if isinstance(node.op,ast.And) else any(values)
        if isinstance(node,ast.Compare) and len(node.ops)==len(node.comparators)==1:
            left=evaluate(node.left,extra);right=evaluate(node.comparators[0],extra);op=node.ops[0]
            _check(isinstance(left,str) and isinstance(right,str),"MARKER_UNSUPPORTED")
            if isinstance(op,(ast.In,ast.NotIn)):return (left in right)==isinstance(op,ast.In)
            if isinstance(node.left,ast.Name) and node.left.id in {"python_version","python_full_version"}:
                left=_version(left);right=_version(right)
            if isinstance(op,ast.Eq):return left==right
            if isinstance(op,ast.NotEq):return left!=right
            if isinstance(op,ast.Gt):return left>right
            if isinstance(op,ast.GtE):return left>=right
            if isinstance(op,ast.Lt):return left<right
            if isinstance(op,ast.LtE):return left<=right
        raise RuntimeError("PROVENANCE_MARKER_UNSUPPORTED")
    return any(evaluate(tree.body,extra) for extra in (extras or [""]))


def _version(value):
    _check(re.fullmatch(r"\d+(?:\.\d+){0,5}",value),"VERSION_UNSUPPORTED")
    result=tuple(int(v) for v in value.split("."))
    return result+(0,)*(6-len(result))


def _requirement(value):
    requirement,_,marker=value.partition(";")
    match=re.fullmatch(r"\s*([A-Za-z0-9_.-]+)(?:\[([A-Za-z0-9_,.-]+)\])?\s*\(?\s*([^()]*)\s*\)?\s*",requirement)
    _check(match is not None,"REQUIREMENT_INVALID")
    name=integrity._name(match[1]);spec=match[3].strip();extras=sorted(filter(None,(match[2] or "").split(",")))
    return name,spec,marker.strip(),extras


def _satisfies(version,spec):
    actual=_version(version)
    for item in filter(None,spec.split(",")):
        match=re.fullmatch(r"\s*(~=|==|!=|>=|<=|>|<)\s*(\d+(?:\.\d+){0,5})\s*",item)
        _check(match is not None,"VERSION_UNSUPPORTED")
        op,wanted=match.groups();target=_version(wanted)
        checks={"==":actual==target,"!=":actual!=target,">=":actual>=target,"<=":actual<=target,">":actual>target,"<":actual<target}
        if op=="~=":
            parts=wanted.split(".");_check(len(parts)>=2,"VERSION_UNSUPPORTED")
            prefix=tuple(int(v) for v in parts[:-1]);checks[op]=actual>=target and actual[:len(prefix)]==prefix
        if not checks[op]:return False
    return True


def _compatible(tag):
    parts=tag.split("-");_check(len(parts)==3,"WHEEL_TAG_INVALID")
    python,abi,target=parts
    if python in {"py3","py2.py3","cp313"} and abi=="none" and target=="any":return True
    if python!="cp313" or abi not in {"cp313","abi3"}:return False
    if platform.system()=="Linux":
        match=re.fullmatch(r"manylinux_(\d+)_(\d+)_(.+)",target)
        if not match or match[3]!=platform.machine():return False
        libc=platform.libc_ver()
        return libc[0]=="glibc" and tuple(map(int,match.group(1,2)))<=tuple(map(int,libc[1].split(".")[:2]))
    return target=={"Windows":"win_amd64","Darwin":"macosx_11_0_arm64"}.get(platform.system())


def inspect_wheel(payload,*,deadline=None):
    """Validate ZIP and RECORD before mapping package bytes to an installation."""
    deadline=deadline or time.monotonic()+integrity.MAX_SECONDS
    _check(len(payload)<=MAX_WHEEL,"WHEEL_LIMIT")
    files={};size=0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        _check(len(archive.infolist())<=MAX_ENTRIES,"ENTRY_LIMIT")
        for entry in archive.infolist():
            _check(time.monotonic()<=deadline,"DEADLINE")
            name=_relative(entry.filename.rstrip("/"))
            _check(name not in files and not entry.flag_bits&1 and (entry.external_attr>>16)&0o170000!=0o120000,"ZIP_INVALID")
            if entry.is_dir():continue
            size+=entry.file_size
            _check(size<=MAX_EXPANDED and entry.file_size<=integrity.MAX_FILE_BYTES,"EXPANSION_LIMIT")
            data=archive.read(entry);_check(len(data)==entry.file_size,"ZIP_INVALID")
            files[name]=data
    metadata=[name for name in files if name.endswith(".dist-info/METADATA") and name.count("/")==1]
    _check(len(metadata)==1,"METADATA_INVALID");directory=metadata[0].split("/")[0]
    _check(len(files[metadata[0]])<=65536 and len(files.get(directory+"/WHEEL",b""))<=16384
        and len(files.get(directory+"/RECORD",b""))<=4*1024*1024,"METADATA_LIMIT")
    message=BytesParser().parsebytes(files[metadata[0]])
    _check(len(message.get_all("Name",[]))==len(message.get_all("Version",[]))==1,"METADATA_INVALID")
    name=integrity._name(message["Name"]);version=message["Version"]
    _check(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}",name),"METADATA_INVALID");_version(version)
    requirements=message.get_all("Requires-Dist",[])
    _check(len(requirements)<=128 and all(len(v)<=1024 for v in requirements),"REQUIREMENT_INVALID")
    wheel=BytesParser().parsebytes(files[directory+"/WHEEL"]);tags=wheel.get_all("Tag",[])
    _check(tags and len(tags)<=64 and any(_compatible(tag) for tag in tags),"PLATFORM_MISMATCH")
    records={}
    for row in csv.reader(io.StringIO(files[directory+"/RECORD"].decode())):
        _check(len(row)==3,"RECORD_INVALID");relative=_relative(row[0]);_check(relative not in records,"RECORD_INVALID")
        records[relative]=row[1:]
    _check(set(records)==set(files),"RECORD_INVALID")
    hashes={}
    for relative,data in files.items():
        digest=integrity._digest(data);hashes[relative]=digest
        if relative==directory+"/RECORD":_check(records[relative]==["",""],"RECORD_INVALID")
        else:
            encoded=base64.urlsafe_b64encode(bytes.fromhex(digest)).decode().rstrip("=")
            _check(records[relative]==["sha256="+encoded,str(len(data))],"RECORD_INVALID")
    scripts=[]
    if directory+"/entry_points.txt" in files:
        parser=configparser.ConfigParser(interpolation=None);parser.read_string(files[directory+"/entry_points.txt"].decode())
        for section in parser.sections():
            _check(section in {"console_scripts","gui_scripts"},"ENTRY_POINT_UNSUPPORTED")
            for script,entry in parser[section].items():
                _check(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",script)
                    and re.fullmatch(r"[A-Za-z_]\w*(?:\.\w+)*:[A-Za-z_]\w*(?:\.\w+)*",entry.strip()),"ENTRY_POINT_UNSUPPORTED")
                scripts.append(script)
    extras=message.get_all("Provides-Extra",[])
    _check(len(extras)<=64 and len(set(extras))==len(extras)
        and all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}",item) for item in extras),"EXTRAS_INVALID")
    return dict(name=name,version=version,requires_dist=requirements,tags=tags,directory=directory,files=hashes,scripts=scripts,extras=extras)


def _installed_path(relative,site):
    parts=relative.split("/")
    if parts[0].endswith(".data"):
        _check(len(parts)>2 and parts[1] in {"purelib","platlib"},"WHEEL_LAYOUT_UNSUPPORTED")
        return site+"/"+"/".join(parts[2:])
    return site+"/"+relative


def build_candidate(root,sha,mode,wheels,*,runtime_manifest,requested_extras=None,deadline=None):
    """Offline candidate generation, with no approval/write/network/install step."""
    deadline=deadline or time.monotonic()+integrity.MAX_SECONDS
    integrity.environment_path(Path(root),sha,mode)
    sitepaths=[p.rsplit("/",2)[0] for p in runtime_manifest["files"] if p.endswith(".dist-info/METADATA")]
    _check(sitepaths and len(set(sitepaths))==1,"INSTALLATION_LAYOUT_INVALID");site=sitepaths[0]
    approved_files=runtime_manifest["files"];mapped={};artifacts=[];seen=set();scripts=set();provided={}
    _check(0<len(wheels)<=MAX_WHEELS,"ARTIFACT_LIMIT")
    for filename,payload in sorted(wheels.items()):
        _check(time.monotonic()<=deadline,"DEADLINE")
        _check(_relative(filename)==PurePosixPath(filename).name and filename.endswith(".whl"),"ARTIFACT_PATH_INVALID")
        wheel=inspect_wheel(payload,deadline=deadline);_check(wheel["name"] not in seen,"DISTRIBUTION_DUPLICATE");seen.add(wheel["name"])
        _check(runtime_manifest["distributions"].get(wheel["name"])==wheel["version"],"DISTRIBUTION_MISMATCH")
        for relative,digest in wheel["files"].items():
            if relative==wheel["directory"]+"/RECORD":continue
            path=_installed_path(relative,site)
            _check(len(mapped)<=MAX_ENTRIES,"ENTRY_LIMIT")
            _check(path not in mapped and approved_files.get(path)==digest,"INSTALLED_BYTES_MISMATCH");mapped[path]=digest
        scripts.update("bin/"+name for name in wheel["scripts"])
        if wheel["name"]=="pip" and "pip" in wheel["scripts"]:scripts.add("bin/pip3.13")
        provided[wheel["name"]]=set(wheel["extras"])
        artifacts.append(dict(filename=filename,sha256=integrity._digest(payload),name=wheel["name"],version=wheel["version"],
            requires_dist=wheel["requires_dist"],tags=wheel["tags"]))
    _check(seen==set(runtime_manifest["distributions"]),"DISTRIBUTIONS_INCOMPLETE")
    generated={}
    allowed={"pyvenv.cfg","bin/python","bin/python3","bin/python3.13","bin/activate","bin/activate.csh","bin/activate.fish","bin/Activate.ps1"}
    for relative,digest in approved_files.items():
        if relative in mapped:continue
        basename=relative.rsplit("/",1)[-1]
        generated_metadata=relative.startswith(site+"/") and ".dist-info/" in relative and basename in {"INSTALLER","REQUESTED","RECORD"}
        _check(relative in allowed or relative in scripts or generated_metadata,"UNAPPROVED_IMPORT_OR_GENERATED_FILE")
        generated[relative]=digest
    extras=requested_extras or {}
    _check(isinstance(extras,dict) and set(extras)<=seen and all(isinstance(v,list) and len(v)<=32
        and all(isinstance(x,str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}",x) for x in v) for v in extras.values()),"EXTRAS_INVALID")
    versions=runtime_manifest["distributions"]
    selected={name:set(extras.get(name,[])) for name in seen}
    _check(all(values<=provided[name] for name,values in selected.items()),"EXTRAS_INVALID")
    for _ in range(MAX_WHEELS):
        before=repr(sorted((k,sorted(v)) for k,v in selected.items()))
        for artifact in artifacts:
            _check(time.monotonic()<=deadline,"DEADLINE")
            for requirement in artifact["requires_dist"]:
                name,spec,marker,needed=_requirement(requirement)
                if _marker(marker,sorted(selected[artifact["name"]])):
                    _check(name in versions and _satisfies(versions[name],spec),"DEPENDENCY_MISMATCH")
                    _check(set(needed)<=provided[name],"EXTRAS_INVALID")
                    selected[name].update(needed)
        if before==repr(sorted((k,sorted(v)) for k,v in selected.items())):break
    else:raise RuntimeError("PROVENANCE_DEPENDENCY_LIMIT")
    return dict(schema=SCHEMA,release_commit=sha,runtime_mode=mode,platform=target_platform(),
        interpreter_sha256=runtime_manifest["interpreter"]["sha256"],artifacts=artifacts,
        requested_extras=extras,generated_files=generated)


def _verify_provenance(root,sha,mode,*,runtime_manifest,expected_digest,deadline=None):
    root=Path(root);deadline=deadline or time.monotonic()+integrity.MAX_SECONDS
    folder=root/"artifact-provenance";trust=root/"runtime-trust";artifacts=root/"wheelhouse"
    directories={str(path):integrity._trusted(path,directory=True) for path in (folder,trust,artifacts)}
    path=folder/(sha+"-"+mode+".json");signature=path.with_suffix(".sig");public=trust/"artifacts-ed25519.pub"
    before={str(p):integrity._trusted(p) for p in (path,signature,public)}
    payload=integrity._read(path,integrity.MAX_MANIFEST_BYTES,deadline=deadline)
    _check(integrity._digest(payload)==expected_digest,"APPROVAL_DIGEST_MISMATCH")
    descriptor=strict_json(payload);_check(payload==integrity._canonical(descriptor),"MANIFEST_NONCANONICAL")
    _check(isinstance(descriptor,dict) and isinstance(descriptor.get("artifacts"),list)
        and 0<len(descriptor["artifacts"])<=MAX_WHEELS,"MANIFEST_INVALID")
    identities={}
    for item in descriptor["artifacts"]:
        name=item["filename"]
        _check(_relative(name)==PurePosixPath(name).name and name not in identities,"ARTIFACT_PATH_INVALID")
        identities[name]=integrity._trusted(artifacts/name)
    key=integrity._read(public,1024,deadline=deadline)
    der=base64.b64decode(b"".join(key.splitlines()[1:-1]),validate=True)
    _check(der.startswith(bytes.fromhex("302a300506032b6570032100")) and len(der)==44,"KEY_INVALID")
    _check(len(integrity._read(signature,64,deadline=deadline))==64,"SIGNATURE_INVALID")
    integrity._trusted(OPENSSL.resolve(strict=True),system=True)
    result=subprocess.run([str(OPENSSL),"pkeyutl","-verify","-rawin","-pubin","-inkey",str(public),
        "-sigfile",str(signature),"-in",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        timeout=max(.001,deadline-time.monotonic()),check=False,
        env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","OPENSSL_CONF":"/dev/null"})
    _check(result.returncode==0,"SIGNATURE_INVALID")
    _check(isinstance(descriptor,dict) and isinstance(descriptor.get("artifacts"),list)
        and 0<len(descriptor["artifacts"])<=MAX_WHEELS,"MANIFEST_INVALID")
    wheels={};total=0
    for item in descriptor["artifacts"]:
        name=item["filename"];_check(_relative(name)==PurePosixPath(name).name and name not in wheels,"ARTIFACT_PATH_INVALID")
        artifact=artifacts/name;_check(identities[name]==integrity._trusted(artifact),"APPROVAL_CHANGED")
        value=integrity._read(artifact,MAX_WHEEL,deadline=deadline);total+=len(value)
        _check(total<=MAX_EXPANDED and integrity._digest(value)==item["sha256"],"ARTIFACT_MISMATCH");wheels[name]=value
    candidate=build_candidate(root,sha,mode,wheels,runtime_manifest=runtime_manifest,requested_extras=descriptor.get("requested_extras"),deadline=deadline)
    _check(candidate==descriptor,"INSTALLATION_OR_PLATFORM_MISMATCH")
    _check(all(integrity._trusted(Path(p),directory=True)==identity for p,identity in directories.items())
        and all(integrity._trusted(Path(p))==identity for p,identity in before.items())
        and all(integrity._trusted(artifacts/name)==identity for name,identity in identities.items())
        and time.monotonic()<=deadline,"APPROVAL_CHANGED")
    return dict(state="VERIFIED",sha256=expected_digest,
        identity_sha256=integrity._digest(integrity._canonical((directories,before,identities))))


def verify_provenance(root,sha,mode,*,runtime_manifest,expected_digest,deadline=None):
    try:
        return _verify_provenance(root,sha,mode,runtime_manifest=runtime_manifest,
            expected_digest=expected_digest,deadline=deadline)
    except Exception:
        # ZIP/parser/OpenSSL errors are untrusted data, not diagnostic output.
        raise RuntimeError("PROVENANCE_UNVERIFIED") from None
