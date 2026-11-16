"""Test-only grant helper must refuse non-disposable schema targets before I/O."""
import pytest
from dashboard_reader_fixture import restricted_dashboard_reader


@pytest.mark.parametrize('schema',[None,1,'','public','dashboard_m1_nothex','dashboard_m1_'+('a'*31), 'dashboard_m1_'+('a'*32)+';DROP SCHEMA public'])
def test_non_disposable_schema_is_denied_before_connection(schema):
    with pytest.raises(RuntimeError,match='ISOLATED_SCHEMA_REQUIRED'):
        with restricted_dashboard_reader(None,schema):pytest.fail('Unsafe schema accepted')
