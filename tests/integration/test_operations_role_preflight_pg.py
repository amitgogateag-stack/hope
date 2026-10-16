from uuid import uuid4

import pytest
from sqlalchemy import text

from hope.api.operations_deployment import DeploymentError, verify_database_role


@pytest.mark.integration
def test_preflight_rejects_privileged_and_write_capable_dashboard_roles(postgres_engine):
    name = "hope_ops_test_" + uuid4().hex
    table = "ops_test_" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{name}" NOLOGIN'))
        connection.execute(text(f'CREATE TABLE public."{table}" (id integer)'))
    try:
        with postgres_engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(DeploymentError, match="ROLE_PRIVILEGED"):
                verify_database_role(connection, component="dashboard")
        with postgres_engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text(f'SET LOCAL ROLE "{name}"'))
            verify_database_role(connection, component="dashboard")
            assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        with postgres_engine.begin() as connection:
            connection.execute(text(f'GRANT INSERT ON public."{table}" TO "{name}"'))
        with postgres_engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text(f'SET LOCAL ROLE "{name}"'))
            with pytest.raises(DeploymentError, match="ROLE_CAN_WRITE"):
                verify_database_role(connection, component="dashboard")
        with postgres_engine.connect() as connection:
            with pytest.raises(DeploymentError, match="REQUIRES_READ_ONLY"):
                verify_database_role(connection, component="paper")
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE public."{table}"'))
            connection.execute(text(f'DROP ROLE "{name}"'))
