"""Deterministic offline release candidates in marked disposable directories.

The builder consumes independently signed inputs. It never generates a key,
signs an output, approves a runtime, invokes pip/network, or changes a host.
"""
from __future__ import annotations
import argparse
import base64
import configparser
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

from hope.api import runtime_integrity as r,package_provenance as p,operations_deployment as operations
from hope.api.artifact_approval import verify_document

MARKER=b"hope.release-builder.v1\n"
MAX_ARCHIVE=128*1024*1024


def builder_identity(python):
    python=Path(python).resolve(strict=True);deadline=time.monotonic()+r.MAX_SECONDS
    return dict(python_sha256=r._fingerprint(python,deadline,system=True)[0],
        os_release_sha256=r._digest(r._read(Path("/etc/os-release").resolve(strict=True),65536,system=True)),
        platform=p.target_platform())


def _source(release,sha):
    operations.inspect_release(release,sha);r.source_snapshot(release)
    names=sorted(filter(None,operations._git(release,"ls-files","-z").split("\0")))
    r._require(0<len(names)<=r.MAX_FILES,"BUILD_SOURCE_LIMIT")
    files={};total=0;deadline=time.monotonic()+r.MAX_SECONDS
    for name in names:
        p._relative(name);data=r._read(release/name,r.MAX_FILE_BYTES,deadline=deadline);total+=len(data)
        r._require(total<=MAX_ARCHIVE,"BUILD_SOURCE_LIMIT");files[name]=data
    operations.inspect_release(release,sha)
    return files


def _install(environment,wheels,logical):
    """Install only validated wheel payloads; no package/build-hook execution."""
    site="lib/python3.13/site-packages";claimed=set();installed_bytes=0
    for filename,payload in sorted(wheels.items()):
        info=p.inspect_wheel(payload);owned=[]
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            installed_bytes+=sum(archive.getinfo(name).file_size for name in info["files"])
            r._require(installed_bytes<=r.MAX_BYTES and len(claimed)+len(info["files"])<=r.MAX_FILES,"BUILD_INSTALLATION_LIMIT")
            for name in info["files"]:
                if name==info["directory"]+"/RECORD":continue
                relative=p._installed_path(name,site);r._require(relative not in claimed,"BUILD_FILE_COLLISION")
                claimed.add(relative);target=environment/relative;target.parent.mkdir(parents=True,exist_ok=True)
                r._require(not target.exists() and not target.is_symlink(),"BUILD_FILE_COLLISION")
                target.write_bytes(archive.read(name));target.chmod(0o644);owned.append(relative)
            entry=info["directory"]+"/entry_points.txt"
            if entry in info["files"]:
                parser=configparser.ConfigParser(interpolation=None);parser.read_string(archive.read(entry).decode())
                for section in parser.sections():
                    for script,call in parser[section].items():
                        relative="bin/"+script;r._require(relative not in claimed,"BUILD_FILE_COLLISION");claimed.add(relative)
                        module,attribute=call.strip().split(":")
                        code=("#!"+str(logical/"bin/python")+"\nfrom importlib import import_module\nimport sys\n"
                            +"value=import_module("+repr(module)+")\nfor part in "+repr(attribute.split("."))+": value=getattr(value,part)\n"
                            +"if __name__ == '__main__': sys.exit(value())\n")
                        (environment/relative).write_text(code,encoding="utf-8",newline="\n");(environment/relative).chmod(0o755);owned.append(relative)
        for name,data in (("INSTALLER",b"hope-offline\n"),("REQUESTED",b"")):
            relative=site+"/"+info["directory"]+"/"+name
            r._require(relative not in claimed,"BUILD_FILE_COLLISION");claimed.add(relative)
            (environment/relative).write_bytes(data);owned.append(relative)
        record=site+"/"+info["directory"]+"/RECORD";stream=io.StringIO();writer=csv.writer(stream,lineterminator="\n")
        for relative in sorted(owned):
            data=(environment/relative).read_bytes()
            digest=base64.urlsafe_b64encode(bytes.fromhex(r._digest(data))).decode().rstrip("=")
            writer.writerow((os.path.relpath(relative,site).replace(os.sep,"/"),"sha256="+digest,len(data)))
        writer.writerow((info["directory"]+"/RECORD","",""));(environment/record).write_text(stream.getvalue(),encoding="utf-8",newline="\n")


def build_sbom(descriptor,source_digest):
    artifacts=descriptor["artifacts"];versions={a["name"]:a["version"] for a in artifacts}
    refs={name:"pkg:pypi/"+name+"@"+version for name,version in versions.items()}
    extras={name:set(descriptor["requested_extras"].get(name,[])) for name in versions};edges={name:set() for name in versions}
    for _ in range(p.MAX_WHEELS):
        before=repr(sorted((k,sorted(v)) for k,v in extras.items()))
        for artifact in artifacts:
            for requirement in artifact["requires_dist"]:
                name,spec,marker,needed=p._requirement(requirement)
                if p._marker(marker,sorted(extras[artifact["name"]])):
                    r._require(name in versions and p._satisfies(versions[name],spec),"BUILD_DEPENDENCY_MISMATCH")
                    edges[artifact["name"]].add(name);extras[name].update(needed)
        if before==repr(sorted((k,sorted(v)) for k,v in extras.items())):break
    else:raise RuntimeError("BUILD_DEPENDENCY_LIMIT")
    return dict(bomFormat="CycloneDX",specVersion="1.6",version=1,
        metadata=dict(component=dict(type="application",name="hope",version=descriptor["release_commit"],
            hashes=[dict(alg="SHA-256",content=source_digest)])),
        components=[dict(type="library",name=a["name"],version=a["version"],purl=refs[a["name"]],
            **{"bom-ref":refs[a["name"]]},hashes=[dict(alg="SHA-256",content=a["sha256"])]) for a in sorted(artifacts,key=lambda a:a["name"])],
        dependencies=[dict(ref=refs[name],dependsOn=sorted(refs[n] for n in edges[name])) for name in sorted(versions)])


def _tar(path,files,epoch):
    r._require(sum(len(v[0]) for v in files.values())<=MAX_ARCHIVE,"BUILD_ARCHIVE_LIMIT")
    with tarfile.open(path,"w",format=tarfile.USTAR_FORMAT) as archive:
        for name,(data,mode) in sorted(files.items()):
            p._relative(name);info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode
            info.uid=info.gid=0;info.uname=info.gname="";info.mtime=epoch;archive.addfile(info,io.BytesIO(data))
    r._require(path.stat().st_size<=MAX_ARCHIVE,"BUILD_ARCHIVE_LIMIT")


def build_release(source,work,inputs,signature,public,*,expected_inputs_digest,python):
    """Two independently created runtimes, compared before emitting candidates."""
    source,work,inputs,signature,public=map(Path,(source,work,inputs,signature,public))
    work=work.absolute();temporary=Path(tempfile.gettempdir()).resolve(strict=True)
    r._require(work.resolve(strict=True)==work and work.is_relative_to(temporary) and work!=temporary
        and r._read(work/".hope-builder",64)==MARKER,"BUILD_DISPOSABLE_ROOT_REQUIRED")
    r._require(all(not path.resolve(strict=True).is_relative_to(work) for path in (source,inputs,signature,public)),"BUILD_AUTHORITY_OVERLAP")
    plan=verify_document(inputs,signature,public,expected_digest=expected_inputs_digest)
    fields={"schema","release_commit","builder_id","builder","epoch","deployment_root","artifacts","requested_extras"}
    r._require(isinstance(plan,dict) and set(plan)==fields and plan["schema"]=="hope.build-input.v1"
        and re.fullmatch("[0-9a-f]{64}",plan["builder_id"]) and type(plan["epoch"]) is int and 0<=plan["epoch"]<=4102444800,"BUILD_INPUT_INVALID")
    sha=r._sha(plan["release_commit"]);logical=Path(plan["deployment_root"])/"release-venvs"/sha
    r._require(Path(plan["deployment_root"]).is_absolute() and str(logical).find("\n")==-1
        and " " not in str(logical) and not any(v in {".",".."} for v in logical.parts),"BUILD_PREFIX_INVALID")
    r._require(builder_identity(python)==plan["builder"],"BUILD_BUILDER_MISMATCH")
    source_files=_source(source,sha);source_digest=r._digest(r._canonical({k:r._digest(v) for k,v in source_files.items()}))
    r._require(isinstance(plan["artifacts"],list) and 0<len(plan["artifacts"])<=p.MAX_WHEELS,"BUILD_ARTIFACT_LIMIT")
    wheels={};total=0;artifact_identities={}
    r._trusted(inputs.parent/"wheelhouse",directory=True)
    for artifact in plan["artifacts"]:
        filename=artifact["filename"];p._relative(filename)
        r._require(Path(filename).name==filename and filename.endswith(".whl") and filename not in wheels,"BUILD_ARTIFACT_PATH_INVALID")
        artifact_identities[filename]=r._trusted(inputs.parent/"wheelhouse"/filename)
        payload=r._read(inputs.parent/"wheelhouse"/filename,p.MAX_WHEEL);total+=len(payload)
        actual=p.inspect_wheel(payload)
        r._require(total<=p.MAX_EXPANDED and artifact==dict(filename=filename,sha256=r._digest(payload),name=actual["name"],version=actual["version"]),"BUILD_ARTIFACT_MISMATCH")
        wheels[filename]=payload
    outputs=[]
    for attempt in ("a","b"):
        root=work/attempt;root.mkdir(mode=0o700)
        release=root/"releases"/sha;(release/"constraints").mkdir(parents=True)
        (release/"constraints/ci-test.txt").write_bytes(source_files["constraints/ci-test.txt"])
        environment=root/"release-venvs"/sha;environment.parent.mkdir()
        result=subprocess.run([str(python),"-I","-B","-S","-m","venv","--without-pip","--copies",str(environment)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=False,
            env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","PYTHONDONTWRITEBYTECODE":"1"})
        r._require(result.returncode==0,"BUILD_VENV_FAILED")
        for name in ("activate","activate.csh","activate.fish","Activate.ps1"):(environment/"bin"/name).unlink(missing_ok=True)
        config=environment/"pyvenv.cfg";lines=config.read_text().splitlines()
        config.write_text("\n".join(line for line in lines if not line.startswith(("command =","prompt =")))+"\n",encoding="utf-8",newline="\n")
        _install(environment,wheels,logical)
        runtime=r.build_runtime_manifest(root,sha,"release")
        descriptor=p.build_candidate(root,sha,"release",wheels,runtime_manifest=runtime,requested_extras=plan["requested_extras"])
        sbom=build_sbom(descriptor,source_digest);runtime.update(schema="hope.runtime-manifest.v2",package_provenance_sha256=r._digest(r._canonical(descriptor)))
        runtime["interpreter"]["resolved"]=str(logical/"bin/python")
        evidence={"runtime-manifest.json":r._canonical(runtime),"package-provenance.json":r._canonical(descriptor),"sbom.json":r._canonical(sbom)}
        modes={}
        for row in operations._git(source,"ls-files","--stage","-z").split("\0"):
            if row:
                header,name=row.split("\t",1);mode,_,stage=header.split()
                r._require(stage=="0" and mode in {"100644","100755"},"BUILD_SOURCE_MODE_INVALID");modes[name]=mode
        r._require(set(modes)==set(source_files),"BUILD_SOURCE_CHANGED")
        files={"release/"+k:(v,0o755 if modes[k]=="100755" else 0o644) for k,v in source_files.items()}
        for relative in runtime["files"]:files["runtime/"+relative]=((environment/relative).read_bytes(),0o755 if (environment/relative).stat().st_mode&0o111 else 0o644)
        # Copies keep executable aliases regular. Internal lib64 links are
        # omitted from the archive and declared explicitly for safe staging.
        for name,data in evidence.items():files["evidence/"+name]=(data,0o644)
        archive=root/"release.tar";_tar(archive,files,plan["epoch"])
        hashes={name:r._digest(data) for name,data in evidence.items()};hashes["release.tar"]=r._fingerprint(archive,time.monotonic()+r.MAX_SECONDS)[0]
        outputs.append(dict(root=root,hashes=hashes))
    r._require(outputs[0]["hashes"]==outputs[1]["hashes"],"BUILD_NOT_REPRODUCIBLE")
    r._require(_source(source,sha)==source_files and builder_identity(python)==plan["builder"]
        and all(r._trusted(inputs.parent/"wheelhouse"/name)==identity
            and r._read(inputs.parent/"wheelhouse"/name,p.MAX_WHEEL)==wheels[name] for name,identity in artifact_identities.items())
        and verify_document(inputs,signature,public,expected_digest=expected_inputs_digest)==plan,"BUILD_INPUT_CHANGED")
    report=dict(schema="hope.release-candidate.v1",state="REPRODUCIBLE",approved=False,release_commit=sha,builder_id=plan["builder_id"],
        inputs_sha256=expected_inputs_digest,source_sha256=source_digest,hashes=outputs[0]["hashes"],execution_authorized=False,
        runtime_mode="release",deployment_root=plan["deployment_root"],epoch=plan["epoch"],builder=plan["builder"])
    (work/"candidate.json").write_bytes(r._canonical(report))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline disposable release candidates; never signs or approves")
    for name in ("source","work","inputs","signature","public","python"):parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--inputs-sha256",required=True)
    args=parser.parse_args(argv)
    try:
        values=vars(args);values["expected_inputs_digest"]=values.pop("inputs_sha256")
        report=build_release(**values)
    except Exception:
        print('{"schema":"hope.release-candidate.v1","state":"BLOCKED","execution_authorized":false}');return 2
    print(r._canonical(report).decode());return 0


if __name__=="__main__":raise SystemExit(main())
