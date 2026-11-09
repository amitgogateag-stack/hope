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


def model_external_admission(admin,role,monkeypatch):
    def approve(table,values):
        assert table in {'paper_handoff_intents','paper_handoff_outcomes','paper_startup_claims',
            'paper_startup_bootstraps','paper_startup_executions','paper_startup_commit_witnesses','paper_startup_completions'}
        values=dict(values,database_principal=role)
        with admin.begin() as connection:
            connection.execute(text('INSERT INTO public.paper_admission_approvals '
                '(target_table,authorization_sha256,principal_oid,approved_row,valid_until) '
                'SELECT :table,:authorization,r.oid,to_jsonb(jsonb_populate_record(NULL::public.'+table+',CAST(:payload AS jsonb)))-\'recorded_at\',:expiry '
                'FROM pg_catalog.pg_roles r WHERE r.rolname=:role ON CONFLICT DO NOTHING'),
                dict(table=table,authorization=values['authorization_sha256'],role=role,
                    payload=json.dumps(values,default=str),expiry=values['witness_expires_at']))

    original_insert=SqlAlchemyPaperStartupRepository._insert
    @wraps(original_insert)
    def insert(repository,table,values,permit):
        permit.assert_current()
        approve(table,values)
        return original_insert(repository,table,values,permit)
    monkeypatch.setattr(SqlAlchemyPaperStartupRepository,'_insert',insert)

    original_claim=SqlAlchemyPaperHandoffRepository.claim
    @wraps(original_claim)
    def claim(repository,proof):
        current=proof.assert_current();token=current.token
        values={key:token[key] for key in ('request_sha256','first_cycle_key_sha256','release_commit',
            'controller_process_sha256','stage_process_sha256','deployment_receipt_sha256','database_sha256',
            'issued_at','expires_at','max_cycles','paper_only','live_enabled')}
        values.update(authorization_sha256=current.authorization_sha256,evidence_sha256=current.evidence_sha256,
            authorizer_key_sha256=current.authorizer_key_sha256,policy_authority_sha256=current.policy_authority_sha256,
            minimum_policy_generation=token['minimum_policy_generation'],observed_policy_generation=current.policy_generation,
            witness_expires_at=current.expires_at)
        approve('paper_handoff_intents',values)
        return original_claim(repository,proof)
    monkeypatch.setattr(SqlAlchemyPaperHandoffRepository,'claim',claim)

    original_finish=SqlAlchemyPaperHandoffRepository.finish
    @wraps(original_finish)
    def finish(repository,proof,outcome):
        current=proof.assert_current()
        approve('paper_handoff_outcomes',dict(authorization_sha256=current.authorization_sha256,
            controller_process_sha256=current.token['controller_process_sha256'],evidence_sha256=current.evidence_sha256,
            policy_authority_sha256=current.policy_authority_sha256,observed_policy_generation=current.policy_generation,
            outcome=outcome,witness_expires_at=current.expires_at))
        return original_finish(repository,proof,outcome)
    monkeypatch.setattr(SqlAlchemyPaperHandoffRepository,'finish',finish)
    return approve
