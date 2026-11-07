"""Fixed-path read-only image diagnostics; no deployment readiness authority."""
from pathlib import Path
from hope.api.release_image import verify_image


def image_observation(folder,sha):
    folder=Path(folder)
    try:
        return verify_image(folder/"oci",folder/"approval.json",folder/"images-ed25519.pub",
            folder/"policy.json",folder/"policy-ed25519.pub",expected_sha=sha)
    except Exception:
        # Never return pathnames, signature text, archive data or exception text.
        return dict(schema="hope.image-observation.v1",scope="DISPOSABLE_LAB_ONLY",state="UNVERIFIED",
            release_commit=None,image_sha256=None,expires_at=None,policy_generation=None,execution_authorized=False)
