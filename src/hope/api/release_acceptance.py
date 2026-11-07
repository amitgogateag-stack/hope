"""Disposable staging and bounded acceptance observations. No host activation."""
from __future__ import annotations
from datetime import datetime,timezone
import io
import os
import re
from pathlib import Path
import subprocess
import tarfile
import tempfile

from hope.api import runtime_integrity as r,package_provenance as p,operations_deployment as operations
from hope.api.runtime_identity import verify_listener

MARKER=b"hope.operational-acceptance.v1\n"


def process_observation(pid,python,arguments):
    before=operations._process_identity(pid)
    verify_listener(pid,python,identity=operations._process_identity,managed=False,
        expected_identity=before,expected_arguments=arguments)
    return r._digest(r._canonical((pid,before)))


def _lab(root):
    root=Path(root);temporary=Path(tempfile.gettempdir()).resolve(strict=True)
    r._require(root.resolve(strict=True)==root and root.is_relative_to(temporary) and root!=temporary
        and r._read(root/".hope-acceptance",64)==MARKER,"ACCEPTANCE_DISPOSABLE_ROOT_REQUIRED")
    return root


def stage_lab_release(archive,root,source,sha,*,expected_digest):
    root=_lab(root);sha=r._sha(sha);source=Path(source)
    operations.inspect_release(source,sha)
    data=r._read(Path(archive),r.MAX_FILE_BYTES)
    r._require(r._digest(data)==expected_digest,"ACCEPTANCE_ARCHIVE_MISMATCH")
    release=root/"releases"/sha;environment=root/"release-venvs"/sha
    r._require(not release.exists() and not environment.exists(),"ACCEPTANCE_STAGE_EXISTS")
    release.parent.mkdir(exist_ok=True);environment.mkdir(parents=True)
    subprocess.run([operations.GIT_BINARY,"-c","core.hooksPath=/dev/null","clone","--no-hardlinks","--",str(source),str(release)],
        env=operations.GIT_ENVIRONMENT,check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # Preserve the independently verified origin identity after local cloning;
    # no fetch, credential use or external repository mutation occurs.
    operations._git(release,"remote","set-url","origin",operations._git(source,"remote","get-url","origin"))
    files={};total=0
    with tarfile.open(fileobj=io.BytesIO(data),mode="r:") as bundle:
        for member in bundle:
            p._relative(member.name);total+=member.size
            r._require(member.isfile() and member.name not in files and member.mode in {0o644,0o755}
                and member.uid==member.gid==0 and total<=r.MAX_FILE_BYTES and len(files)<r.MAX_FILES,"ACCEPTANCE_ARCHIVE_INVALID")
            prefix,_,name=member.name.partition("/")
            r._require(prefix in {"release","runtime","evidence"} and name and not name.startswith(".git/"),"ACCEPTANCE_ARCHIVE_INVALID")
            if prefix=="evidence":
                r._require(name in {"runtime-manifest.json","package-provenance.json","sbom.json"},"ACCEPTANCE_ARCHIVE_INVALID")
                target=root/"candidate-evidence"/sha/name
            else:target=(release if prefix=="release" else environment)/name
            target.parent.mkdir(parents=True,exist_ok=True)
            r._require(not target.is_symlink() and all(not parent.is_symlink() for parent in target.parents),"ACCEPTANCE_PATH_INVALID")
            payload=bundle.extractfile(member).read(r.MAX_FILE_BYTES+1)
            r._require(len(payload)==member.size,"ACCEPTANCE_ARCHIVE_INVALID")
            target.write_bytes(payload);target.chmod(member.mode);files[member.name]=True
    manifest=r.strict_json((root/"candidate-evidence"/sha/"runtime-manifest.json").read_bytes())
    for name,target in manifest["links"].items():
        p._relative(name);r._require(target.startswith("@runtime/"),"ACCEPTANCE_LINK_INVALID")
        relative=p._relative(target[len("@runtime/"):]);link=environment/name
        r._require(not link.exists() and not link.is_symlink() and (environment/relative).resolve(strict=True).is_relative_to(environment),"ACCEPTANCE_LINK_INVALID")
        link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(os.path.relpath(environment/relative,link.parent),target_is_directory=(environment/relative).is_dir())
    operations.inspect_release(release,sha)
    return release,environment


def acceptance_observation(runtime,observations,*,expected_sha,now=None):
    """A process starting or a successful test alone can never satisfy this."""
    now=now or datetime.now(timezone.utc);runtime=r.safe_evidence(runtime)
    blockers=[]
    if (runtime["state"]!="VERIFIED" or runtime["provenance_state"]!="VERIFIED"
            or runtime["build_state"]!="VERIFIED" or runtime["release_commit"]!=expected_sha):blockers.append("REVIEW_RELEASE_BUILD_AND_RUNTIME")
    required={"release_commit","generated_at","process_identity","database","scheduler","market_data","reconciliation","risk","live_enabled"}
    if not isinstance(observations,dict) or set(observations)!=required:blockers.append("REVIEW_MISSING_EVIDENCE")
    else:
        try:
            stamp=datetime.fromisoformat(observations["generated_at"])
            if stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=300:blockers.append("REFRESH_EXPIRED_EVIDENCE")
        except (ValueError,TypeError):blockers.append("REFRESH_EXPIRED_EVIDENCE")
        if observations["release_commit"]!=expected_sha:blockers.append("REVIEW_DEPLOYMENT_IDENTITY")
        if not isinstance(observations["process_identity"],str) or not re.fullmatch("[0-9a-f]{64}",observations["process_identity"]):blockers.append("REVIEW_PROCESS_IDENTITY")
        for key,allowed,action in (("database",{"COMPATIBLE"},"REVIEW_DATABASE"),("scheduler",{"CURRENT"},"REVIEW_SCHEDULER"),
            ("market_data",{"VERIFIED","CLOSED"},"REFRESH_VERIFIED_MARKET_INPUTS"),("reconciliation",{"CLEAR"},"RECONCILE_DURABLE_CLAIMS"),
            ("risk",{"CLEAR"},"REVIEW_DURABLE_HALT")):
            if not isinstance(observations[key],str) or observations[key] not in allowed:blockers.append(action)
        if observations["live_enabled"] is not False:blockers.append("LIVE_MUST_REMAIN_DISABLED")
    return dict(schema="hope.operational-acceptance.v1",scope="DISPOSABLE_LAB_ONLY",state="BLOCKED" if blockers else "VERIFIED",
        release_commit=expected_sha,generated_at=now.isoformat(),blockers=sorted(set(blockers)),execution_authorized=False)
