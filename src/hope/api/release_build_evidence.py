"""Independent release approval and SBOM binding; no authority generation."""
from __future__ import annotations
from pathlib import Path
import re
import time
from hope.api import runtime_integrity as r
from hope.api.snapshot_contract import strict_json
from hope.api.artifact_approval import verify_document

_CACHE={}


def _verify_release_build(root,sha,mode,*,runtime_manifest,expected_digest,deadline=None,source_path=None):
    deadline=deadline or time.monotonic()+r.MAX_SECONDS
    root=Path(root);folder=root/"release-builds";key=root/"runtime-trust/releases-ed25519.pub"
    directory=r._trusted(folder,directory=True);path=folder/(sha+"-"+mode+".json")
    before=r._trusted(path);approval=verify_document(path,path.with_suffix(".sig"),key,expected_digest=expected_digest,deadline=deadline)
    fields={"schema","state","approved","release_commit","builder_id","inputs_sha256","source_sha256","hashes",
        "execution_authorized","runtime_mode","deployment_root","epoch","builder"}
    r._require(isinstance(approval,dict) and set(approval)==fields and approval["schema"]=="hope.release-approval.v1"
        and approval["state"]=="APPROVED" and approval["approved"] is True and approval["execution_authorized"] is False
        and approval["release_commit"]==sha and approval["runtime_mode"]==mode and approval["deployment_root"]==str(root),"BUILD_APPROVAL_INVALID")
    hashes=approval["hashes"]
    r._require(all(isinstance(approval[k],str) and re.fullmatch("[0-9a-f]{64}",approval[k])
        for k in ("builder_id","inputs_sha256","source_sha256")) and type(approval["epoch"]) is int,"BUILD_APPROVAL_INVALID")
    inputs=folder/(sha+"-"+mode+".inputs.json");builder_key=root/"runtime-trust/builders-ed25519.pub"
    input_identity=r._trusted(inputs)
    plan=verify_document(inputs,inputs.with_suffix(".sig"),builder_key,expected_digest=approval["inputs_sha256"],deadline=deadline)
    r._require((plan["schema"]=="hope.build-input.v1" or (plan["schema"]=="hope.build-input.v2"
        and plan.get("os_python")=="/usr/bin/python3.13")) and all(plan[k]==approval[k]
        for k in ("release_commit","builder_id","builder","epoch","deployment_root"))
        and approval["builder"]["python_sha256"]==runtime_manifest["interpreter"]["sha256"],"BUILD_BUILDER_MISMATCH")
    r._require(isinstance(hashes,dict) and set(hashes)=={"runtime-manifest.json","package-provenance.json","sbom.json","release.tar"},"BUILD_APPROVAL_INVALID")
    previous=dict(runtime_manifest);previous.pop("release_build_sha256",None);previous["schema"]="hope.runtime-manifest.v2"
    r._require(r._digest(r._canonical(previous))==hashes["runtime-manifest.json"]
        and previous["package_provenance_sha256"]==hashes["package-provenance.json"],"BUILD_RUNTIME_MISMATCH")
    sbom_path=folder/(sha+"-"+mode+".sbom.json");sbom_identity=r._trusted(sbom_path)
    sbom_bytes=r._read(sbom_path,r.MAX_MANIFEST_BYTES,deadline=deadline);sbom=strict_json(sbom_bytes)
    r._require(r._digest(sbom_bytes)==hashes["sbom.json"] and sbom["bomFormat"]=="CycloneDX"
        and sbom["specVersion"]=="1.6" and sbom["metadata"]["component"]["version"]==sha,"BUILD_SBOM_INVALID")
    versions={component["name"]:component["version"] for component in sbom["components"]}
    r._require(len(versions)==len(sbom["components"]) and versions==runtime_manifest["distributions"],"BUILD_SBOM_MISMATCH")
    descriptor_path=root/"artifact-provenance"/(sha+"-"+mode+".json")
    descriptor_bytes=r._read(descriptor_path,r.MAX_MANIFEST_BYTES,deadline=deadline);descriptor=strict_json(descriptor_bytes)
    from hope.api.release_builder import build_sbom
    r._require(r._digest(descriptor_bytes)==hashes["package-provenance.json"]
        and sorted(plan["artifacts"],key=lambda a:a["filename"])==sorted([
            {k:a[k] for k in ("filename","sha256","name","version")} for a in descriptor["artifacts"]],key=lambda a:a["filename"])
        and build_sbom(descriptor,approval["source_sha256"])==sbom,"BUILD_SBOM_MISMATCH")
    archive=folder/(sha+"-"+mode+".tar");identity=r._trusted(archive);cache_key=(str(archive),identity,hashes["release.tar"])
    if cache_key not in _CACHE:
        r._require(r._fingerprint(archive,deadline)[0]==hashes["release.tar"],"BUILD_ARCHIVE_MISMATCH")
        if len(_CACHE)>=8:_CACHE.clear()
        _CACHE[cache_key]=True
    source_sha256=None
    if source_path is not None:
        r._require(isinstance(source_path,str) and source_path and not source_path.startswith("/")
            and "\\" not in source_path and all(part not in {"", ".", ".."} for part in source_path.split("/")),
            "BUILD_SOURCE_PATH_INVALID")
        from hope.api.release_image import _members
        entries=_members(r._read(archive,128*1024*1024,deadline=deadline))
        entry=entries.get("release/"+source_path)
        r._require(entry is not None and entry[2] is False,"BUILD_SOURCE_MISSING")
        source_sha256=r._digest(entry[0])
    r._require(r._trusted(archive)==identity and r._trusted(sbom_path)==sbom_identity
        and r._trusted(path)==before and r._trusted(folder,directory=True)==directory
        and r._trusted(inputs)==input_identity
        and verify_document(inputs,inputs.with_suffix(".sig"),builder_key,expected_digest=approval["inputs_sha256"],deadline=deadline)==plan
        and verify_document(path,path.with_suffix(".sig"),key,expected_digest=expected_digest,deadline=deadline)==approval,"BUILD_EVIDENCE_CHANGED")
    result=dict(state="VERIFIED",sha256=expected_digest,builder_id=approval["builder_id"],
        identity_sha256=r._digest(r._canonical((directory,before,sbom_identity,identity,input_identity))))
    if source_path is not None:result["source_sha256"]=source_sha256
    return result


def verify_release_build(root,sha,mode,*,runtime_manifest,expected_digest,deadline=None,source_path=None):
    try:return _verify_release_build(root,sha,mode,runtime_manifest=runtime_manifest,
        expected_digest=expected_digest,deadline=deadline,source_path=source_path)
    except Exception:raise RuntimeError("RELEASE_BUILD_UNVERIFIED") from None
