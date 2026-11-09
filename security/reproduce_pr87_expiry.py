"""Scheduling reproduction of PR87's frozen three-second case, on real PG.

The copied function body is unchanged except its name (no pytest discovery).
It is EXPECTED to fail during admission, before its intended financial SQL.
This is reproduction evidence, not the repaired security proof.
"""
from datetime import timedelta
import time
from uuid import uuid4
import pytest
from sqlalchemy import text,event
from sqlalchemy.exc import DBAPIError
from test_startup_transition_postgres import isolated_database,startup_database,seeded,prepare,certified_case,execute,MIGRATIONS
from hope.api import paper_startup as startup

pytestmark=pytest.mark.integration
ORIGINAL_COMMIT='bda32668051bb5ea8c86d0f1acb854af7a174050'
ORIGINAL_FUNCTION_SHA256='91cfc42087c19aa12df69a62cb3d241bc8e0db25a0060bb91bdcc14af5deb1d5'

def original_pr87_case(prepare,phase):
    value=prepare();case=value['case'];worker=value['worker'];target=uuid4()
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    if phase=='completed':execute(value)
    elif phase!='bootstrap_only':
        if phase=='expired':
            case['execution']['expires_at']=int(time.time())+3;case['pin_authority']('execution')
        startup.execution_callbacks(worker,case['verify_execution']())
        if phase=='expired':time.sleep(4)
    with pytest.raises(DBAPIError,match='PAPER_STARTUP_'):
        with worker.begin() as connection:
            connection.execute(text('INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':target})
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM public.paper_portfolios WHERE portfolio_id=:id'),{'id':target}).scalar_one()==0


def test_original_three_second_case_fails_before_financial_sql(prepare,record_property):
    holder={};financial=[];admission=[]
    def stall(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO public.paper_startup_executions '):
            admission.append(True)
            time.sleep(4)  # actual scheduling delay; original deadline stays 3s
        if statement.startswith('INSERT INTO public.paper_portfolios '):financial.append(True)
    def build():
        value=prepare();holder.update(value)
        event.listen(value['worker'],'after_cursor_execute',stall)
        return value
    try:
        with pytest.raises(RuntimeError,match='DEPLOYMENT_EVIDENCE_INVALID'):
            original_pr87_case(build,'expired')
    finally:
        if holder:event.remove(holder['worker'],'after_cursor_execute',stall)
    assert financial==[]
    with holder['worker'].connect() as connection:
        for table in ('paper_startup_executions','paper_startup_financial_transactions','paper_effects','orders','fills'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0
    record_property('original_commit',ORIGINAL_COMMIT)
    record_property('original_function_sha256',ORIGINAL_FUNCTION_SHA256)
    record_property('original_test_failure_reproduced','DEPLOYMENT_EVIDENCE_INVALID before financial SQL')
    record_property('execution_insert_reached',bool(admission))
