"""Isolated local PostgreSQL acceptance of the dashboard's read-only preflight."""
from pathlib import Path
import shutil
import os
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,event,text

from hope.api import dashboard_activation as module
from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_real_scoped_reader_wrong_target_writer_owner_and_checksum_failures(postgres_engine,tmp_path,monkeypatch):
    # Never reuse, modify or drop a preexisting database with this name.
    if postgres_engine.url.host not in {"127.0.0.1","localhost"}:
        pytest.skip("Dedicated loopback PostgreSQL test server required")
    target="hope_m3_restore_test";role="dashboard_test_"+uuid4().hex
    with postgres_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        if admin.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"),{"name":target}).scalar():
            pytest.skip("Existing M3 database must not be touched by tests")
        admin.execute(text(f'CREATE DATABASE "{target}"'))
    owner=create_engine(postgres_engine.url.set(database=target));reader=None
    try:
        with owner.begin() as connection:
            apply_migrations(connection,Path(__file__).parents[2]/"migrations")
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
        reader=create_engine(owner.url)
        @event.listens_for(reader,"connect")
        def assume_role(connection,record):
            with connection.cursor() as cursor:cursor.execute(f'SET ROLE "{role}"')
            connection.commit()
        observed=[]
        @event.listens_for(reader,"after_cursor_execute")
        def read_only_proof(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith("SELECT"):
                with connection.connection.cursor() as check:
                    check.execute("SHOW transaction_read_only");observed.append(check.fetchone()[0])
        environment=tmp_path/"reader.env"
        environment.write_text("HOPE_DATABASE_URL="+owner.url.render_as_string(hide_password=False)+"\n")
        layout=module.Layout(environment=environment)
        monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
        monkeypatch.setattr(module,"create_engine",lambda *a,**k:reader)
        host=module.Host(layout);release=Path(__file__).parents[2]
        host.database(release)
        assert observed and set(observed)=={"on"}
        with owner.begin() as connection:
            connection.execute(text(f'GRANT INSERT ON paper_effects TO "{role}"'))
        with pytest.raises(RuntimeError,match="DASHBOARD_ROLE_CAN_WRITE"):host.database(release)
        # Both runtime contracts must stop before journaling or touching units
        # when the real PostgreSQL role has acquired write capability.
        if os.name=="posix":
            for mode in ("shared","release"):
                root=tmp_path/mode;root.mkdir();(root/"units").mkdir()
                for sha in ("1"*40,"2"*40):(root/"releases"/sha).mkdir(parents=True)
                (root/"current").symlink_to(root/"releases"/("1"*40),target_is_directory=True)
                selected=module.Layout(root=root,units=root/"units",environment=environment)
                guarded=module.Host(selected);guarded.paper_safe=Mock()
                guarded.release=Mock(return_value=release);guarded.runtime=Mock()
                guarded.control=Mock();guarded.stop=Mock()
                monkeypatch.setattr(module,"verify_exact_ci",Mock())
                with pytest.raises(RuntimeError,match="DASHBOARD_ROLE_CAN_WRITE"):
                    module.activate(selected,guarded,target="2"*40,shared="1"*40,
                        ci_run=123,ci_attempt=1,runtime=mode)
                assert guarded.target_runtime==mode
                assert not selected.journal.exists()
                guarded.stop.assert_not_called();guarded.control.assert_not_called()
                assert (root/"current").resolve().name=="1"*40
        with owner.begin() as connection:
            connection.execute(text(f'REVOKE INSERT ON paper_effects FROM "{role}"'))
        # Preserve immutable database history: corrupt only a disposable staged
        # code copy to demonstrate that its migration checksums are incompatible.
        shutil.copytree(release/"migrations",tmp_path/"migrations")
        first=sorted((tmp_path/"migrations").glob("*.sql"))[0]
        first.write_text(first.read_text()+"\n-- incompatible staged test copy\n")
        with pytest.raises(RuntimeError):host.database(tmp_path)
        monkeypatch.setattr(module,"create_engine",lambda *a,**k:owner)
        with pytest.raises(RuntimeError,match="ROLE_PRIVILEGED"):host.database(release)
        monkeypatch.setattr(module,"create_engine",lambda *a,**k:postgres_engine)
        with pytest.raises(RuntimeError,match="WRONG_DATABASE"):host.database(release)
    finally:
        if reader is not None:reader.dispose()
        owner.dispose()
        with postgres_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
            admin.execute(text(f'DROP DATABASE "{target}" WITH (FORCE)'))
            admin.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
