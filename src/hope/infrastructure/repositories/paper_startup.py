"""Durable one-use bootstrap/execution states; only certified jobs prove completion."""
from sqlalchemy import text
from hope.api.startup_authority import Permit
from hope.api.first_cycle_authorization import require
from hope.infrastructure.repositories.paper_handoff import SqlAlchemyPaperHandoffRepository


def assert_public_schema(connection):
    """Financial ORM names must not resolve through private or temporary shadows."""
    names=connection.execute(text('SELECT pg_catalog.current_schemas(true)')).scalar_one()
    require(isinstance(names,(list,tuple)) and list(names[:2])==['pg_catalog','public']
            and len(names)<=3 and all(name.startswith('pg_temp_') for name in names[2:]))


class SqlAlchemyPaperStartupRepository:
    def __init__(self,connection):self.connection=connection

    def _ready(self,permit,kind):
        require(type(permit) is Permit and permit.kind==kind)
        current=permit.assert_current()
        assert_public_schema(self.connection)
        SqlAlchemyPaperHandoffRepository(self.connection)._ready(current.handoff)
        return current

    def _common(self,permit):
        return dict(authorization_sha256=permit.authorization_sha256,evidence_sha256=permit.evidence_sha256,
            controller_process_sha256=permit.handoff.token['controller_process_sha256'],
            policy_authority_sha256=permit.policy_authority_sha256,observed_policy_generation=permit.policy_generation,
            witness_expires_at=permit.expires_at)

    def _insert(self,table,values,permit):
        require(table in {'paper_startup_claims','paper_startup_bootstraps','paper_startup_executions','paper_startup_completions','paper_startup_commit_witnesses'})
        columns=','.join(values);parameters=','.join(':'+key for key in values)
        self.connection.execute(text(f'INSERT INTO public.{table} ({columns}) VALUES ({parameters})'),values)
        permit.assert_current()
        require(self.connection.execute(text('SELECT clock_timestamp()<to_timestamp(:expiry)'),{'expiry':permit.expires_at}).scalar_one() is True)

    def claim_startup(self,permit):
        with self.connection.begin_nested():
            current=self._ready(permit,'startup')
            SqlAlchemyPaperHandoffRepository(self.connection).claim(current.handoff)
            values=self._common(current)
            values.update({key:current.token[key] for key in ('request_sha256','handoff_authorization_sha256','release_commit','schema_catalog_sha256',
                'first_cycle_key_sha256','database_sha256','issued_at','expires_at','paper_only','live_enabled','max_cycles')})
            values.update(deployment_receipt_sha256=current.handoff.token['deployment_receipt_sha256'],authorizer_key_sha256=current.authorizer_key_sha256)
            self._insert('paper_startup_claims',values,current)

    def bootstrapped(self,permit,catalog_sha256):
        with self.connection.begin_nested():
            current=self._ready(permit,'startup');require(catalog_sha256==current.token['schema_catalog_sha256'])
            self._insert('paper_startup_bootstraps',dict(self._common(current),schema_catalog_sha256=catalog_sha256),current)

    def claim_execution(self,permit):
        with self.connection.begin_nested():
            current=self._ready(permit,'execution');values=self._common(current)
            values.update({key:current.token[key] for key in ('request_sha256','startup_authorization_sha256','handoff_authorization_sha256',
                'first_cycle_key_sha256','database_sha256','job_run_id','job_key','scheduled_for','issued_at','expires_at','paper_only','live_enabled','max_cycles')})
            values.update(authorizer_key_sha256=current.authorizer_key_sha256,job_run_id=current.run.job_run_id,
                          scheduled_for=current.run.scheduled_for,job_key=current.run.job_key)
            self._insert('paper_startup_executions',values,current)

    def assert_active(self,permit):
        require(type(permit) is Permit and permit.kind=='execution')
        current=permit.assert_current()
        assert_public_schema(self.connection)
        from hope.infrastructure.repositories.paper_handoff import database_identity
        require(database_identity(self.connection)==current.handoff.token['database_sha256'])
        # No second control lock: the financial transaction already holds it.
        # All mutable decisions remain in that transaction's existing gates.
        row=self.connection.execute(text('SELECT e.evidence_sha256,e.controller_process_sha256,e.database_principal, '
            'e.policy_authority_sha256,e.observed_policy_generation,clock_timestamp()<to_timestamp(:expiry), '
            'EXISTS(SELECT 1 FROM public.paper_startup_completions c WHERE c.authorization_sha256=e.authorization_sha256),current_user '
            'FROM public.paper_startup_executions e WHERE e.authorization_sha256=:authorization'),
            dict(expiry=current.expires_at,authorization=current.authorization_sha256)).first()
        require(row is not None and row[0]==current.evidence_sha256 and row[1]==current.handoff.token['controller_process_sha256']
                and row[2]==row[7] and row[3]==current.policy_authority_sha256 and row[4]<=current.policy_generation
                and row[5] is True and row[6] is False)

    def commit_witness(self,permit,run):
        current=self._ready(permit,'execution');require(run==current.run)
        self._insert('paper_startup_commit_witnesses',dict(self._common(current),job_run_id=run.job_run_id),current)

    def complete(self,permit,run):
        with self.connection.begin_nested():
            current=self._ready(permit,'execution');require(run==current.run)
            self._insert('paper_startup_completions',dict(self._common(current),job_run_id=run.job_run_id),current)
            SqlAlchemyPaperHandoffRepository(self.connection).finish(current.handoff,'ACKNOWLEDGED')

    def assert_completed(self,permit):
        current=self._ready(permit,'execution')
        row=self.connection.execute(text("SELECT c.evidence_sha256,c.database_principal=current_user,c.job_run_id "
            "FROM public.paper_startup_completions c JOIN public.paper_startup_executions e USING(authorization_sha256) "
            "JOIN public.job_runs j ON j.job_run_id=c.job_run_id JOIN public.paper_certified_outcomes o ON o.job_run_id=j.job_run_id "
            "JOIN public.paper_startup_commit_witnesses w USING(authorization_sha256) JOIN public.paper_startup_financial_transactions t USING(authorization_sha256) "
            "WHERE c.authorization_sha256=:authorization AND j.status='SUCCEEDED' AND w.financial_transaction_id=t.transaction_id "
            "AND EXISTS (SELECT 1 FROM public.audit_events a WHERE a.event_type='PAPER_RUN_RECONCILED' AND a.entity_id=j.job_run_id::text)"),
            dict(authorization=current.authorization_sha256)).first()
        require(row is not None and row[0]==current.evidence_sha256 and row[1] is True and row[2]==current.run.job_run_id)
        current.assert_current()

    def observation(self,startup_authorization):
        from hope.api.paper_controller import is_hash
        require(is_hash(startup_authorization))
        try:
            row=self.connection.execute(text('SELECT b.authorization_sha256,e.authorization_sha256,c.authorization_sha256 '
                'FROM public.paper_startup_claims s LEFT JOIN public.paper_startup_bootstraps b USING(authorization_sha256) '
                'LEFT JOIN public.paper_startup_executions e ON e.startup_authorization_sha256=s.authorization_sha256 '
                'LEFT JOIN public.paper_startup_completions c ON c.authorization_sha256=e.authorization_sha256 '
                'WHERE s.authorization_sha256=:authorization'),{'authorization':startup_authorization}).first()
            state='ABSENT' if row is None else 'CYCLE_COMPLETED' if row[2] else 'RECOVERY_REQUIRED' if row[1] or not row[0] else 'BOOTSTRAPPED'
        except Exception:state='UNVERIFIED'
        return dict(schema='hope.paper-startup-audit.v1',state=state,authorization_sha256=startup_authorization,execution_authorized=False)
