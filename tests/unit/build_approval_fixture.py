"""Ephemeral lab authority only. Production code has no signing operation."""
from pathlib import Path
import subprocess
from hope.api import runtime_integrity as r,package_provenance as p
from hope.api.release_builder import _tar,build_sbom


def sign(path,private):
    subprocess.run([str(p.OPENSSL),"pkeyutl","-sign","-rawin","-inkey",str(private),"-in",str(path),"-out",str(path.with_suffix(".sig"))],check=True,capture_output=True)


def approve_fixture_build(root,sha,mode,manifest,private):
    folder=root/"release-builds";folder.mkdir(exist_ok=True)
    public=root/"runtime-trust/artifacts-ed25519.pub"
    for name in ("releases-ed25519.pub","builders-ed25519.pub"):(root/"runtime-trust"/name).write_bytes(public.read_bytes())
    descriptor=r.strict_json((root/"artifact-provenance"/(sha+"-"+mode+".json")).read_bytes())
    source_files={"docs/dashboard/preview.html":b"<h1>HOPE</h1>\n","README.md":b"fixture source\n"}
    source_digest=r._digest(r._canonical({name:r._digest(data) for name,data in source_files.items()}))
    sbom=build_sbom(descriptor,source_digest);sbom_bytes=r._canonical(sbom)
    (folder/(sha+"-"+mode+".sbom.json")).write_bytes(sbom_bytes)
    archive=folder/(sha+"-"+mode+".tar")
    _tar(archive,{"release/"+name:(data,0o644) for name,data in source_files.items()},1700000000)
    plan=dict(schema="hope.build-input.v1",release_commit=sha,builder_id="c"*64,
        builder=dict(python_sha256=manifest["interpreter"]["sha256"],os_release_sha256="f"*64,platform=p.target_platform()),
        epoch=1700000000,deployment_root=str(root),requested_extras=descriptor["requested_extras"],
        artifacts=[{k:a[k] for k in ("filename","sha256","name","version")} for a in descriptor["artifacts"]])
    path=folder/(sha+"-"+mode+".inputs.json");path.write_bytes(r._canonical(plan));sign(path,private)
    approval=dict(schema="hope.release-approval.v1",state="APPROVED",approved=True,release_commit=sha,builder_id=plan["builder_id"],
        inputs_sha256=r._digest(path.read_bytes()),source_sha256=source_digest,execution_authorized=False,runtime_mode=mode,
        deployment_root=str(root),epoch=plan["epoch"],builder=plan["builder"],hashes={"runtime-manifest.json":r._digest(r._canonical(manifest)),
            "package-provenance.json":manifest["package_provenance_sha256"],"sbom.json":r._digest(sbom_bytes),"release.tar":r._digest(archive.read_bytes())})
    path=folder/(sha+"-"+mode+".json");path.write_bytes(r._canonical(approval));sign(path,private)
    return dict(manifest,schema="hope.runtime-manifest.v3",release_build_sha256=r._digest(path.read_bytes()))
