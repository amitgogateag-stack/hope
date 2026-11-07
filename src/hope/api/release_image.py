"""Offline OCI candidates and independent, expiring image approval.

This module never pulls, runs, signs, deploys or approves an image. An OCI
descriptor proves artifact identity, not the identity of a running container.
All outputs remain disposable lab evidence and cannot authorize PAPER jobs.
"""
from __future__ import annotations
import argparse
import base64
import io
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
import time

from hope.api import runtime_integrity as r
from hope.api.artifact_approval import verify_document
from hope.api.snapshot_contract import strict_json

MARKER = b"hope.image-builder.v1\n"
MAX_LAYER = 128 * 1024 * 1024
MAX_FILES = 20000
CONFIG = "application/vnd.oci.image.config.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar"
HASH = re.compile("[0-9a-f]{64}")


def _check(condition):
    r._require(condition, "IMAGE_EVIDENCE_INVALID")


def _hash(value):
    _check(isinstance(value, str) and HASH.fullmatch(value))
    return value


def _name(name):
    _check(isinstance(name, str) and name and len(name) <= 240
        and not name.startswith("/") and "\\" not in name
        and all(v not in {"", ".", ".."} for v in name.split("/"))
        and all(ord(v) >= 32 and ord(v) != 127 for v in name)
        and not any(v.startswith(".wh.") for v in name.split("/")))
    return name


def _members(payload):
    """Validate before writing anything: no devices, hardlinks or extraction API."""
    _check(len(payload) <= MAX_LAYER)
    entries = {}; total = 0; consumed = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            name = _name(member.name.rstrip("/") if member.isdir() else member.name)
            total += member.size
            _check(name not in entries and len(entries) < MAX_FILES and total <= MAX_LAYER
                and member.uid == member.gid == 0 and member.mode in {0o644, 0o755}
                and not member.pax_headers and not member.linkname
                and (member.isfile() or member.isdir()))
            data = archive.extractfile(member).read(MAX_LAYER + 1) if member.isfile() else b""
            _check(len(data) == member.size)
            entries[name] = (data, member.mode, member.isdir())
            consumed=member.offset_data+((member.size+511)//512)*512
    _check(entries and len(payload)-consumed >= 1024 and not any(payload[consumed:]))
    for name in entries:
        for parent in PurePosixPath(name).parents:
            if str(parent) in entries:_check(entries[str(parent)][2])
    return entries


def _tar(entries, epoch):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, (data, mode, directory) in sorted(entries.items()):
            _name(name); member = tarfile.TarInfo(name); member.mode = mode
            member.mtime = epoch; member.uid = member.gid = 0
            member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
            member.size = len(data); archive.addfile(member, None if directory else io.BytesIO(data))
    payload = stream.getvalue(); _check(len(payload) <= MAX_LAYER)
    return payload


def _signed(path, public, digest=None):
    path = Path(path)
    data = r._read(path, r.MAX_MANIFEST_BYTES)
    return verify_document(path, path.with_suffix(".sig"), public,
        expected_digest=digest or r._digest(data))


def _key_digest(path):
    # Revocation follows the Ed25519 key, including across PEM formatting.
    payload=r._read(Path(path),1024)
    return r._digest(base64.b64decode(b"".join(payload.splitlines()[1:-1]),validate=True))


def _policy(path, public, *, now):
    policy = _signed(path, public)
    _check(isinstance(policy, dict) and set(policy) == {
        "schema", "generation", "issued_at", "expires_at", "revoked_images", "revoked_keys"}
        and policy["schema"] == "hope.image-policy.v1"
        and type(policy["generation"]) is int and policy["generation"] > 0
        and type(policy["issued_at"]) is int and type(policy["expires_at"]) is int
        and policy["issued_at"] <= now < policy["expires_at"]
        and 0 < policy["expires_at"] - policy["issued_at"] <= 86400)
    for field in ("revoked_images", "revoked_keys"):
        values = policy[field]
        _check(isinstance(values, list) and len(values) <= 1024 and len(set(values)) == len(values))
        for value in values:_hash(value)
    return policy


def _base(document, payload):
    _check(isinstance(document, dict) and set(document) == {
        "schema", "layer_sha256", "os_release_sha256", "python_path", "python_sha256", "architecture", "inventory_sha256"}
        and document["schema"] == "hope.image-base.v1" and document["architecture"] in {"amd64", "arm64"}
        and document["python_path"] == "/usr/bin/python3.13")
    for key in ("layer_sha256", "os_release_sha256", "python_sha256", "inventory_sha256"):_hash(document[key])
    _check(r._digest(payload) == document["layer_sha256"])
    entries = _members(payload)
    _check("etc/os-release" in entries and "usr/bin/python3.13" in entries
        and not entries["usr/bin/python3.13"][2] and entries["usr/bin/python3.13"][1] == 0o755
        and r._digest(entries["usr/bin/python3.13"][0]) == document["python_sha256"]
        and r._digest(entries["etc/os-release"][0]) == document["os_release_sha256"])
    inventory = {name:dict(sha256=r._digest(data),mode=mode,directory=directory)
        for name,(data,mode,directory) in sorted(entries.items())}
    _check(r._digest(r._canonical(inventory)) == document["inventory_sha256"])
    # Base authority is approving an OS, never ambient deployment authority.
    _check(not any(name == "opt/hope-operations" or name.startswith("opt/hope-operations/") for name in entries))
    return entries


def _release(document, payload, base):
    """Bind M4 approval, archive and its three embedded evidence documents."""
    fields = {"schema","state","approved","release_commit","builder_id","inputs_sha256","source_sha256","hashes",
        "execution_authorized","runtime_mode","deployment_root","epoch","builder"}
    _check(isinstance(document,dict) and set(document) == fields
        and document["schema"] == "hope.release-approval.v1" and document["state"] == "APPROVED"
        and document["approved"] is True and document["execution_authorized"] is False
        and document["runtime_mode"] == "release" and document["deployment_root"] == "/opt/hope-operations"
        and type(document["epoch"]) is int and 0 <= document["epoch"] <= 4102444800)
    sha = r._sha(document["release_commit"])
    for key in ("builder_id","inputs_sha256","source_sha256"):_hash(document[key])
    hashes = document["hashes"]
    _check(isinstance(hashes,dict) and set(hashes) == {"release.tar","runtime-manifest.json","package-provenance.json","sbom.json"})
    for digest in hashes.values():_hash(digest)
    _check(r._digest(payload) == hashes["release.tar"]
        and document["builder"]["python_sha256"] == base["python_sha256"]
        and document["builder"]["os_release_sha256"] == base["os_release_sha256"]
        and document["builder"]["platform"] == dict(system="Linux",machine={"amd64":"x86_64","arm64":"aarch64"}[base["architecture"]],
            python="3.13",implementation="cpython"))
    entries = _members(payload); evidence = {}
    for name, (data, mode, directory) in entries.items():
        prefix, separator, relative = name.partition("/")
        _check(separator and relative and prefix in {"release","runtime","evidence"} and not directory)
        if prefix == "evidence":
            _check(relative in hashes and relative != "release.tar" and r._digest(data) == hashes[relative])
            evidence[relative] = strict_json(data)
        elif prefix == "release":_check(not any(v == ".git" for v in relative.split("/")))
    _check(set(evidence) == set(hashes) - {"release.tar"})
    runtime = evidence["runtime-manifest.json"]; packages = evidence["package-provenance.json"]; sbom = evidence["sbom.json"]
    _check(runtime["release_commit"] == packages["release_commit"] == sha
        and runtime["runtime_mode"] == "release" and runtime["schema"] == "hope.runtime-manifest.v2"
        and runtime["package_provenance_sha256"] == hashes["package-provenance.json"]
        and runtime["interpreter"]["sha256"] == base["python_sha256"])
    from hope.api.release_builder import build_sbom
    _check(build_sbom(packages,document["source_sha256"]) == sbom)
    source = {name[8:]:r._digest(data) for name,(data,_,_) in entries.items() if name.startswith("release/")}
    _check(r._digest(r._canonical(source)) == document["source_sha256"])
    files = {name[8:]:data for name,(data,_,_) in entries.items() if name.startswith("runtime/")}
    _check(set(files) == set(runtime["files"]))
    for name, info in runtime["files"].items():
        # Runtime manifest uses hash strings, not mutable package metadata.
        _check(r._digest(files[name]) == info)
    return sha, entries, runtime


def _descriptor(media, payload):
    return dict(mediaType=media, digest="sha256:"+r._digest(payload), size=len(payload))


def _layout(base_payload, overlay, architecture, epoch, sha):
    layers = [base_payload, overlay]
    config = r._canonical(dict(architecture=architecture,os="linux",
        config=dict(User="65532:65532",WorkingDir="/opt/hope-operations/releases/"+sha,
            Env=["HOPE_LIVE_ENABLED=false","HOPE_DEPLOYED_COMMIT="+sha,"PYTHONDONTWRITEBYTECODE=1"],
            Labels={"org.opencontainers.image.revision":sha,"hope.scope":"DISPOSABLE_LAB_ONLY"},
            Entrypoint=["/usr/bin/python3.13","-I","-B","-S"]),
        rootfs=dict(type="layers",diff_ids=["sha256:"+r._digest(v) for v in layers])))
    manifest = r._canonical(dict(schemaVersion=2,mediaType=MANIFEST,config=_descriptor(CONFIG,config),
        layers=[_descriptor(LAYER,v) for v in layers]))
    index = r._canonical(dict(schemaVersion=2,manifests=[_descriptor(MANIFEST,manifest)]))
    blobs = {r._digest(v):v for v in [*layers,config,manifest]}
    return {"oci-layout":b'{"imageLayoutVersion":"1.0.0"}',"index.json":index,
        **{"blobs/sha256/"+key:value for key,value in blobs.items()}},r._digest(manifest)


def build_image(work, base_document, base_archive, release_document, release_archive, *, base_public, release_public,
                builder_document, builder_public):
    """Construct two content-addressed OCI layouts in a new marked temp root.

    The image is deliberately a lab candidate. Production launch integration,
    Git metadata and deployment receipts require a separate reviewed approval.
    """
    work = Path(work).absolute(); temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    _check(work.resolve(strict=True) == work and work.is_relative_to(temporary) and work != temporary
        and r._read(work/".hope-image-builder",64) == MARKER)
    r._trusted(work,directory=True)
    inputs = tuple(map(Path,(base_document,base_archive,release_document,release_archive,base_public,release_public,
        builder_document,builder_public)))
    _check(all(not path.resolve(strict=True).is_relative_to(work) for path in inputs))
    identities = {path:r._trusted(path) for path in inputs}
    base = _signed(base_document,base_public); release = _signed(release_document,release_public)
    provenance_keys=sorted(_key_digest(path) for path in (base_public,release_public,builder_public))
    _check(len(set(provenance_keys)) == 3)
    plan = _signed(builder_document,builder_public,digest=release["inputs_sha256"])
    from hope.api.release_builder import validate_build_plan
    validate_build_plan(plan)
    _check(plan["schema"] == "hope.build-input.v2" and all(plan[key] == release[key]
        for key in ("release_commit","builder_id","builder","epoch","deployment_root")))
    base_bytes = r._read(Path(base_archive),MAX_LAYER); release_bytes = r._read(Path(release_archive),MAX_LAYER)
    _base(base,base_bytes); sha,entries,runtime = _release(release,release_bytes,base)
    packages=strict_json(entries["evidence/package-provenance.json"][0])
    _check(sorted(plan["artifacts"],key=lambda a:a["filename"]) == sorted([
        {key:a[key] for key in ("filename","sha256","name","version")} for a in packages["artifacts"]],key=lambda a:a["filename"])
        and plan["requested_extras"] == packages["requested_extras"])
    overlay = {}; prefix = "opt/hope-operations/"
    for name,value in entries.items():
        group,relative = name.split("/",1)
        mapped = {"release":"releases/"+sha,"runtime":"release-venvs/"+sha,"evidence":"candidate-evidence/"+sha}[group]
        overlay[prefix+mapped+"/"+relative] = value
    overlay[prefix+"candidate-evidence/"+sha+"/release-approval.json"] = (r._canonical(release),0o644,False)
    overlay[prefix+"candidate-evidence/"+sha+"/base.json"] = (r._canonical(base),0o644,False)
    overlay[prefix+"candidate-evidence/"+sha+"/builder-inputs.json"] = (r._canonical(plan),0o644,False)
    # Link declarations remain evidence, never unchecked tar symlinks. A lab
    # image cannot claim executable compatibility until a real launch verifies it.
    image_a,digest = _layout(base_bytes,_tar(overlay,release["epoch"]),base["architecture"],release["epoch"],sha)
    image_b,digest_b = _layout(base_bytes,_tar(overlay,release["epoch"]),base["architecture"],release["epoch"],sha)
    _check(image_a == image_b and digest == digest_b)
    _check(all(r._trusted(path) == identity for path,identity in identities.items())
        and _signed(base_document,base_public) == base and _signed(release_document,release_public) == release
        and _signed(builder_document,builder_public,digest=release["inputs_sha256"]) == plan)
    report = dict(schema="hope.image-candidate.v1",scope="DISPOSABLE_LAB_ONLY",state="REPRODUCIBLE",
        release_commit=sha,image_sha256=digest,base_sha256=base["layer_sha256"],
        base_inventory_sha256=base["inventory_sha256"],release_approval_sha256=r._digest(r._canonical(release)),
        hashes=release["hashes"],provenance_keys=provenance_keys,execution_authorized=False)
    for name,layout in (("a",image_a),("b",image_b)):
        destination = work/name; destination.mkdir(mode=0o700)
        for relative,payload in layout.items():
            path = destination/relative; path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
    (work/"candidate.json").write_bytes(r._canonical(report))
    return report


def verify_image(layout, approval_path, image_public, policy_path, policy_public, *, expected_sha, now=None, minimum_generation=1):
    """Reverify every content address and current revocation; never cache policy."""
    live_clock=now is None
    now = int(time.time()) if live_clock else now
    _check(type(now) is int and type(minimum_generation) is int and minimum_generation > 0)
    layout = Path(layout); approval_path = Path(approval_path)
    authorities = tuple(map(Path,(approval_path,approval_path.with_suffix(".sig"),image_public,
        policy_path,Path(policy_path).with_suffix(".sig"),policy_public)))
    before = {path:r._trusted(path) for path in authorities}
    policy = _policy(policy_path,policy_public,now=now)
    _check(_key_digest(image_public) != _key_digest(policy_public))
    _check(policy["generation"] >= minimum_generation)
    approval = _signed(approval_path,image_public)
    fields = {"schema","scope","state","release_commit","image_sha256","base_sha256","base_inventory_sha256",
        "release_approval_sha256","hashes","provenance_keys","execution_authorized","issued_at","expires_at"}
    _check(isinstance(approval,dict) and set(approval) == fields and approval["schema"] == "hope.image-approval.v1"
        and approval["scope"] == "DISPOSABLE_LAB_ONLY" and approval["state"] == "APPROVED"
        and approval["execution_authorized"] is False and approval["release_commit"] == r._sha(expected_sha)
        and type(approval["issued_at"]) is int and type(approval["expires_at"]) is int
        and approval["issued_at"] <= now < approval["expires_at"]
        and 0 < approval["expires_at"] - approval["issued_at"] <= 86400)
    for key in ("image_sha256","base_sha256","base_inventory_sha256","release_approval_sha256"):_hash(approval[key])
    _check(isinstance(approval["provenance_keys"],list) and len(approval["provenance_keys"]) == 3
        and len(set(approval["provenance_keys"])) == 3)
    for key in approval["provenance_keys"]:_hash(key)
    _check(_key_digest(image_public) not in approval["provenance_keys"]
        and _key_digest(policy_public) not in approval["provenance_keys"])
    _check(approval["image_sha256"] not in policy["revoked_images"]
        and not set([_key_digest(image_public),*approval["provenance_keys"]]) & set(policy["revoked_keys"]))
    directory = r._trusted(layout,directory=True); observed = {}
    parents={path:r._trusted(path,directory=True) for path in (layout/"blobs",layout/"blobs/sha256")}
    def read(path,limit):
        identity = r._trusted(path);data = r._read(path,limit);observed[path] = identity;return data
    _check(strict_json(read(layout/"oci-layout",64)) == {"imageLayoutVersion":"1.0.0"})
    index = strict_json(read(layout/"index.json",8192))
    _check(set(index) == {"schemaVersion","manifests"} and index["schemaVersion"] == 2
        and isinstance(index["manifests"],list) and len(index["manifests"]) == 1)
    def blob(descriptor, media):
        _check(isinstance(descriptor,dict) and set(descriptor) == {"mediaType","digest","size"}
            and descriptor["mediaType"] == media and isinstance(descriptor["digest"],str)
            and descriptor["digest"].startswith("sha256:") and type(descriptor["size"]) is int
            and 0 <= descriptor["size"] <= MAX_LAYER)
        digest = _hash(descriptor["digest"][7:]);payload = read(layout/"blobs/sha256"/digest,MAX_LAYER)
        _check(len(payload) == descriptor["size"] and r._digest(payload) == digest)
        return payload
    manifest_bytes = blob(index["manifests"][0],MANIFEST)
    _check(r._digest(manifest_bytes) == approval["image_sha256"])
    manifest = strict_json(manifest_bytes)
    _check(set(manifest) == {"schemaVersion","mediaType","config","layers"} and manifest["schemaVersion"] == 2
        and manifest["mediaType"] == MANIFEST and isinstance(manifest["layers"],list) and len(manifest["layers"]) == 2)
    config = strict_json(blob(manifest["config"],CONFIG)); layers = [blob(value,LAYER) for value in manifest["layers"]]
    _check(set(config) == {"architecture","os","config","rootfs"}
        and set(config["config"]) == {"User","WorkingDir","Env","Labels","Entrypoint"}
        and config["config"]["WorkingDir"] == "/opt/hope-operations/releases/"+expected_sha
        and config["config"]["Entrypoint"] == ["/usr/bin/python3.13","-I","-B","-S"]
        and r._digest(layers[0]) == approval["base_sha256"]
        and config["rootfs"] == dict(type="layers",diff_ids=["sha256:"+r._digest(v) for v in layers])
        and config["os"] == "linux" and config["architecture"] in {"amd64","arm64"}
        and config["config"]["User"] == "65532:65532"
        and config["config"]["Env"] == ["HOPE_LIVE_ENABLED=false","HOPE_DEPLOYED_COMMIT="+expected_sha,"PYTHONDONTWRITEBYTECODE=1"]
        and config["config"]["Labels"] == {"org.opencontainers.image.revision":expected_sha,"hope.scope":"DISPOSABLE_LAB_ONLY"})
    inventories = [_members(payload) for payload in layers]
    base_inventory = {name:dict(sha256=r._digest(data),mode=mode,directory=directory)
        for name,(data,mode,directory) in sorted(inventories[0].items())}
    _check(r._digest(r._canonical(base_inventory)) == approval["base_inventory_sha256"])
    # Bind the installed evidence bytes, rather than accepting labels as proof.
    prefix = "opt/hope-operations/candidate-evidence/"+expected_sha+"/"
    release_bytes = inventories[1][prefix+"release-approval.json"][0]
    release = strict_json(release_bytes);base = strict_json(inventories[1][prefix+"base.json"][0])
    _check(r._digest(release_bytes) == approval["release_approval_sha256"]
        and release["hashes"] == approval["hashes"] and release["release_commit"] == expected_sha
        and base["layer_sha256"] == approval["base_sha256"] and base["inventory_sha256"] == approval["base_inventory_sha256"])
    _base(base,layers[0])
    plan_bytes=inventories[1][prefix+"builder-inputs.json"][0];plan=strict_json(plan_bytes)
    from hope.api.release_builder import validate_build_plan
    validate_build_plan(plan)
    _check(r._digest(plan_bytes) == release["inputs_sha256"] and plan["schema"] == "hope.build-input.v2"
        and all(plan[key] == release[key] for key in ("release_commit","builder_id","builder","epoch","deployment_root")))
    restored = {}
    mappings = {"opt/hope-operations/releases/"+expected_sha+"/":"release/",
        "opt/hope-operations/release-venvs/"+expected_sha+"/":"runtime/",prefix:"evidence/"}
    for name,value in inventories[1].items():
        if name in {prefix+"base.json",prefix+"release-approval.json",prefix+"builder-inputs.json"}:continue
        matching = [key for key in mappings if name.startswith(key)]
        _check(len(matching) == 1)
        key = matching[0];restored[mappings[key]+name[len(key):]] = value
    _release(release,_tar(restored,release["epoch"]),base)
    packages=strict_json(inventories[1][prefix+"package-provenance.json"][0])
    _check(sorted(plan["artifacts"],key=lambda a:a["filename"]) == sorted([
        {key:a[key] for key in ("filename","sha256","name","version")} for a in packages["artifacts"]],key=lambda a:a["filename"])
        and plan["requested_extras"] == packages["requested_extras"])
    _check(set(approval["hashes"]) == {"runtime-manifest.json","package-provenance.json","sbom.json","release.tar"})
    for name,digest in approval["hashes"].items():
        _hash(digest)
        if name != "release.tar":_check(r._digest(inventories[1][prefix+name][0]) == digest)
    _check(all(r._trusted(path) == identity for path,identity in {**before,**observed}.items())
        and all(r._trusted(path,directory=True) == identity for path,identity in parents.items())
        and r._trusted(layout,directory=True) == directory
        and _policy(policy_path,policy_public,now=now) == policy and _signed(approval_path,image_public) == approval)
    _check(not live_clock or now <= int(time.time()) < min(approval["expires_at"],policy["expires_at"]))
    return dict(schema="hope.image-observation.v1",scope="DISPOSABLE_LAB_ONLY",state="VERIFIED",
        release_commit=expected_sha,image_sha256=approval["image_sha256"],expires_at=min(approval["expires_at"],policy["expires_at"]),
        policy_generation=policy["generation"],execution_authorized=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description="Build unsigned offline lab OCI candidates; never deploy")
    for name in ("work","base-document","base-archive","release-document","release-archive","base-public","release-public","builder-document","builder-public"):
        parser.add_argument("--"+name,type=Path,required=True)
    try:report=build_image(**vars(parser.parse_args(argv)))
    except Exception:
        print('{"schema":"hope.image-candidate.v1","state":"BLOCKED","execution_authorized":false}');return 2
    print(r._canonical(report).decode());return 0


if __name__ == "__main__":raise SystemExit(main())
