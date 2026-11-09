"""Append-only one-use intent audit; never execution or financial authority."""
from sqlalchemy import text

from hope.api.first_cycle_authorization import VerifiedHandoff,require
from hope.api.paper_preflight import _milliseconds
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository


def database_identity(connection):
    """Hash cluster/database identity; never return public metadata verbatim."""
    from hope.infrastructure.paper_history import digest
    row=connection.execute(text('SELECT s.system_identifier::text,pg_catalog.current_database(),d.oid::bigint, '
        "pg_catalog.current_setting('server_version_num')::int / 10000,pg_catalog.pg_is_in_recovery() "
        'FROM pg_catalog.pg_control_system() s CROSS JOIN pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()')).one()
    require(row[3] in {16,17} and row[4] is False)
    return digest(tuple(row[:4]))


class SqlAlchemyPaperHandoffRepository:
    def __init__(self,connection):self.connection=connection

    def _ready(self,proof):
        require(type(proof) is VerifiedHandoff)
        current=proof.assert_current()
        require(self.connection.dialect.name=='postgresql')
        for setting,limit in (('statement_timeout',30000),('lock_timeout',10000),('idle_in_transaction_session_timeout',5000)):
            value=self.connection.execute(text('SHOW '+setting)).scalar_one()
            require(0<_milliseconds(value)<=limit)
        require(database_identity(self.connection)==current.token['database_sha256'])
        SqlAlchemyPaperEnvironmentControlRepository(self.connection).assert_running()
        return current

    def claim(self,proof):
        """One INSERT, never an upsert/replay. Caller owns transaction commit."""
        with self.connection.begin_nested():
            current=self._ready(proof);token=current.token
            values={key:token[key] for key in ('request_sha256','first_cycle_key_sha256','release_commit',
                'controller_process_sha256','stage_process_sha256','deployment_receipt_sha256','database_sha256','issued_at','expires_at',
                'max_cycles','paper_only','live_enabled')}
            values.update(authorization_sha256=current.authorization_sha256,evidence_sha256=current.evidence_sha256,
                          authorizer_key_sha256=current.authorizer_key_sha256,policy_authority_sha256=current.policy_authority_sha256,
                          minimum_policy_generation=token['minimum_policy_generation'],observed_policy_generation=current.policy_generation,
                          witness_expires_at=current.expires_at)
            columns=','.join(values);parameters=','.join(':'+key for key in values)
            self.connection.execute(text(f'INSERT INTO public.paper_handoff_intents ({columns}) VALUES ({parameters})'),values)
            current.assert_current()
            require(self.connection.execute(text('SELECT clock_timestamp() < to_timestamp(:expiry)'),
                                            {'expiry':current.expires_at}).scalar_one() is True)
        return current.authorization_sha256

    def finish(self,proof,outcome):
        require(outcome in {'ACKNOWLEDGED','ABORTED'})
        with self.connection.begin_nested():
            current=self._ready(proof)
            self.connection.execute(text('INSERT INTO public.paper_handoff_outcomes '
                '(authorization_sha256,controller_process_sha256,evidence_sha256,policy_authority_sha256,observed_policy_generation,outcome,witness_expires_at) '
                'VALUES (:auth,:owner,:evidence,:policy,:generation,:outcome,:expiry)'),
                dict(auth=current.authorization_sha256,owner=current.token['controller_process_sha256'],
                     evidence=current.evidence_sha256,policy=current.policy_authority_sha256,generation=current.policy_generation,
                     outcome=outcome,expiry=current.expires_at))
            current.assert_current()
            require(self.connection.execute(text('SELECT clock_timestamp() < to_timestamp(:expiry)'),
                                            {'expiry':current.expires_at}).scalar_one() is True)

    def observation(self,authorization):
        from hope.api.paper_controller import is_hash
        require(is_hash(authorization))
        try:
            row=self.connection.execute(text('SELECT o.outcome FROM public.paper_handoff_intents i LEFT JOIN public.paper_handoff_outcomes o '
                'USING(authorization_sha256) WHERE i.authorization_sha256=:auth'),{'auth':authorization}).first()
            state='ABSENT' if row is None else row[0] or 'RECOVERY_REQUIRED'
        except Exception:state='UNAVAILABLE'
        return dict(schema='hope.paper-handoff-audit.v1',state=state,
                    authorization_sha256=authorization,execution_authorized=False)
