from types import SimpleNamespace
from unittest.mock import Mock,MagicMock
import pytest
from hope.infrastructure.repositories.paper_handoff import SqlAlchemyPaperHandoffRepository,database_identity


def test_unavailable_audit_never_exposes_database_errors():
    connection=Mock();connection.execute.side_effect=RuntimeError('private database secret')
    report=SqlAlchemyPaperHandoffRepository(connection).observation('a'*64)
    assert report['state']=='UNAVAILABLE' and report['execution_authorized'] is False and 'private' not in str(report)


@pytest.mark.parametrize('version,recovery',[(15,False),(16,True),(17,True)])
def test_database_identity_refuses_unsupported_or_standby_clusters(version,recovery):
    connection=Mock();connection.execute.return_value.one.return_value=('private-cluster','private-db',1,version,recovery)
    with pytest.raises(RuntimeError):database_identity(connection)


def test_foreign_proof_type_and_invalid_audit_key_never_query_database():
    connection=MagicMock();repository=SqlAlchemyPaperHandoffRepository(connection)
    with pytest.raises(RuntimeError):repository.observation('invalid')
    with pytest.raises(RuntimeError):repository.claim(object())
    connection.execute.assert_not_called()
