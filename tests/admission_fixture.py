"""Explicit TEST-ONLY external admission model; not a production verifier.

Real independent database-owner connection records exact approvals after genuine
signature validation. Native runtime/observer approval remains modeled as in the
original startup fixture. No application code can write these protected records.
"""
import json
from functools import wraps
from sqlalchemy import text
from hope.infrastructure.repositories.paper_startup import SqlAlchemyPaperStartupRepository
from hope.infrastructure.repositories.paper_handoff import SqlAlchemyPaperHandoffRepository
from hope.api.startup_authority import Permit
from hope.api.first_cycle_authorization import VerifiedHandoff


def model_external_admission(admin,role,monkeypatch):
    def approve(table,values):
        assert table in {'paper_handoff_intents','paper_handoff_outcomes','paper_startup_claims',
            'paper_startup_bootstraps','paper_startup_executions','paper_startup_commit_witnesses','paper_startup_completions'}
        values=dict(values,database_principal=role)
        with admin.begin() as connection:
            connection.execute(text('INSERT INTO public.paper_admission_approvals '
                '(target_table,authorization_sha256,principal_oid,approved_row,valid_until) '
                'SELECT :table,:authorization,r.oid,to_jsonb(jsonb_populate_record(NULL::public.'+table+',CAST(:payload AS jsonb)))-ARRAY[\'recorded_at\',\'claimed_at\'],:expiry '
                'FROM pg_catalog.pg_roles r WHERE r.rolname=:role ON CONFLICT DO NOTHING'),
                dict(table=table,authorization=values['authorization_sha256'],role=role,
                    payload=json.dumps(values,default=str),expiry=values['witness_expires_at']))

    for repository_type in (SqlAlchemyPaperStartupRepository,SqlAlchemyPaperHandoffRepository):
        original=repository_type._insert
        def model(original):
            @wraps(original)
            def insert(repository,table,values,permit):
                # The original repository's _ready has just performed genuine
                # verification. This fixture models an external verifier; do
                # not run another self-process verifier inside its short DB
                # witness interval. All production closing checks stay active.
                assert type(permit) in {Permit,VerifiedHandoff}
                approve(table,values)
                return original(repository,table,values,permit)
            return insert
        monkeypatch.setattr(repository_type,'_insert',model(original))
    return approve
