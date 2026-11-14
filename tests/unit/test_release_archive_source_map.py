"""Actual deterministic tar bytes; these unit inputs are NOT trust roots."""
import pytest
from hope.api import release_builder as b,release_build_evidence as e,runtime_integrity as r


@pytest.mark.parametrize('fault',[None,'changed','missing','extra','wrong_archive','empty','git','unknown_root'])
def test_complete_archive_source_map_is_mandatory(tmp_path,fault):
    original={'release/docs/dashboard/preview.html':(b'preview',0o644),'release/README.md':(b'reviewed',0o644)}
    expected=r._digest(r._canonical({name[8:]:r._digest(value[0]) for name,value in original.items()}))
    entries=dict(original)
    if fault=='changed': entries['release/README.md']=(b'altered',0o644)
    elif fault=='missing': del entries['release/README.md']
    elif fault=='extra': entries['release/injected.py']=(b'extra',0o644)
    elif fault=='empty': entries={'runtime/file':(b'runtime',0o644)}
    elif fault=='git': entries['release/.git/config']=(b'unapproved',0o644)
    elif fault=='unknown_root': entries['unexpected/file']=(b'unapproved',0o644)
    archive=tmp_path/'release.tar';b._tar(archive,entries,1700000000);payload=archive.read_bytes()
    if fault is None:
        assert set(e._verified_archive(payload,r._digest(payload),expected))==set(original)
    else:
        reason=('BUILD_ARCHIVE_MISMATCH' if fault=='wrong_archive' else
                'BUILD_ARCHIVE_MEMBER_INVALID' if fault in {'git','unknown_root'} else 'BUILD_SOURCE_MAP_MISMATCH')
        with pytest.raises(RuntimeError,match=reason):
            e._verified_archive(payload,'0'*64 if fault=='wrong_archive' else r._digest(payload),expected)
