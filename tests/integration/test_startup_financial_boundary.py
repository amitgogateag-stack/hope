"""Direct-SQL adversarial boundary tests using disposable restricted logins."""
from datetime import timedelta
import time
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_startup_transition_postgres import isolated_database,startup_database,seeded,prepare,certified_case,execute,MIGRATIONS
from hope.api import paper_startup as startup

pytestmark=pytest.mark.integration

@pytest.mark.parametrize('phase',['bootstrap_only','claimed_without_job','completed','expired'])
def test_direct_financial_commit_requires_live_same_transaction_execution(prepare,phase):
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
