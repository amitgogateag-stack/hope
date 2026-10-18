"""CI must prove the actual PostgreSQL major, not only label an image."""
import os

import pytest
from sqlalchemy import text


@pytest.mark.integration
def test_matrix_database_major_and_read_only_contract(postgres_engine):
    with postgres_engine.begin() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        version=int(connection.execute(text("SHOW server_version_num")).scalar_one())
        major=version//10000
        assert major in {16,17}
        expected=os.getenv("HOPE_TEST_POSTGRES_MAJOR")
        if expected is not None:assert major==int(expected)
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one()=="on"
