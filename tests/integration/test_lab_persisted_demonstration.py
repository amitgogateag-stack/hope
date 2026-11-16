"""Disposable certified outcomes -> committed readback -> signed LAB HTTP view.

Synthetic test authority is inherited from certified_case, never production
admission. Keys exist only inside this disposable test; no issuer is shipped.
"""
import base64
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from urllib.request import ProxyHandler, build_opener

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text

from hope.api.dashboard_server import DashboardHTTPServer
from hope.application.paper import PaperCycleOutcome
from hope.domain.strategy.candidates import StrategyMarket
from hope.infrastructure.paper_runtime import run_paper_once
from hope.infrastructure.scheduling.paper_reconciliation import authenticate_successful_paper_run
from hope.security import lab_publisher as lab
from test_certified_paper_bridge import certified_case


@pytest.mark.integration
@pytest.mark.parametrize('market',[StrategyMarket.INDIA,StrategyMarket.USA])
@pytest.mark.parametrize('outcome',['NO_TRADE','FILLED','RISK_BLOCKED'])
def test_committed_certified_lifecycle_is_visible_only_as_lab_evidence(certified_case,tmp_path,monkeypatch,market,outcome):
    engine,make=certified_case
    from hope.api.dashboard_m2_snapshot import readers
    from hope.api.dashboard_projection import SECTIONS, verified_projections
    def read_ledger():
        with engine.connect() as connection:
            connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            generated = connection.execute(text('SELECT CURRENT_TIMESTAMP')).scalar_one()
            raw = readers(connection, generated)['execution_ledger']()
            value = {name: {'status': 'UNAVAILABLE'} for name in SECTIONS}
            value['execution_ledger'] = {'status': 'AVAILABLE', **raw}
            result = verified_projections(value, generated)['execution_ledger']
            assert connection.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            connection.rollback()
            return result
    before_ledger = read_ledger()
    data,run,strategy,work,registry=make(market=market,emit=outcome!='NO_TRADE',blocked=outcome=='RISK_BLOCKED')
    clock=lambda:data['as_of']+timedelta(minutes=1)
    assert run_paper_once(engine,run,registry,now=clock) is PaperCycleOutcome.EXECUTED
    # Fresh connections model restart. Reconciliation must not repeat strategy,
    # cash effects, fills or immutable completion/audit publication.
    assert run_paper_once(engine,run,registry,now=clock) is PaperCycleOutcome.SKIPPED_TERMINAL
    assert len(strategy.calls)==1
    with engine.begin() as connection:
        authenticate_successful_paper_run(connection,run)
        authenticate_successful_paper_run(connection,run)
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        assert connection.execute(text("SELECT current_setting('transaction_read_only')")).scalar_one()=='on'
        job=connection.execute(text('SELECT status,completed_at FROM job_runs WHERE job_run_id=:id'),{'id':run.job_run_id}).one()
        actual=connection.execute(text('SELECT outcome,reason_code FROM paper_certified_outcomes WHERE job_run_id=:id'),{'id':run.job_run_id}).one()
        effects=connection.execute(text('SELECT effect_type,entity_id,payload_hash FROM paper_effects WHERE job_run_id=:id ORDER BY effect_type,entity_id'),{'id':run.job_run_id}).all()
        receipts=connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),{'id':str(run.job_run_id)}).scalar_one()
        major=int(connection.execute(text('SHOW server_version_num')).scalar_one())//10000
    assert job.status=='SUCCEEDED' and job.completed_at is not None
    assert actual.outcome==outcome and receipts==1
    # The shared disposable database can contain earlier partial fixtures.
    # Trace this committed case by exact aggregate deltas, never declare that
    # unrelated historical backlog was reconciled or reset it for the test.
    after_ledger = read_ledger()
    for field in ('fills', 'applied_fills', 'fill_applications', 'accounting_receipts',
                  'effect_receipt_fills'):
        assert after_ledger[field] - before_ledger[field] == int(outcome == 'FILLED')
    for field in ('fills_without_application', 'applications_without_pnl',
                  'fills_without_effect_receipt', 'unattributed_effect_receipt_fills', 'effect_receipt_fills_without_application',
                  'accounting_receipts_without_application'):
        assert after_ledger[field] == before_ledger[field]
    assert len(effects)==(5 if outcome=='FILLED' else 1)
    assert actual.reason_code=={'NO_TRADE':'NO_SIGNAL','FILLED':'PORTFOLIO_RISK_APPROVED','RISK_BLOCKED':'MAX_POSITION_NOTIONAL_EXCEEDED'}[outcome]
    evidence=dict(job_run_id=str(run.job_run_id),status=job.status,completed_at=job.completed_at.isoformat(),
        market=market.value,outcome=actual.outcome,reason=actual.reason_code,
        effects=[list(row) for row in effects],reconciliation_receipts=receipts)
    digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    now=int(time.time())
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    verifier=lab.LabPublisherVerifier(public,release_commit='a'*40,session='b'*32,publisher_instance='c'*32,clock=lambda:now)
    body=dict(postgres_major=major,cases=[dict(market=market.value,scenario=outcome,result='PASS',
        reason='NONE' if outcome=='FILLED' else actual.reason_code,evidence_sha256=digest,completed_at=job.completed_at.isoformat())])
    doc=dict(schema='hope.lab-publisher.v1',scope=lab.SCOPE,key_id=verifier.key_id,
        release_commit=verifier.release_commit,session=verifier.session,publisher_instance=verifier.publisher_instance,
        challenge=verifier.challenge,sequence=1,issued_at=now,expires_at=now+15,payload=body)
    doc['signature']=base64.b64encode(key.sign(lab.DOMAIN+lab.canonical(doc))).decode()
    packet=tmp_path/'lab.json';original=lab.canonical(doc);packet.write_bytes(original)
    preview=tmp_path/'preview.html';preview.write_text('<h1>Disposable lab</h1>')
    if os.name=='nt':
        # Native metadata boundary only; Linux CI uses real no-follow reader.
        from hope.infrastructure import durable_file
        def read(path,limit):
            raw=Path(path).read_bytes()
            if len(raw)>limit:raise ValueError('BOUNDED_READ_TOO_LARGE')
            return raw
        monkeypatch.setattr(durable_file,'bounded_regular_read',read)
    with DashboardHTTPServer(0,preview,tmp_path/'absent.json',lab_verifier=verifier,lab_packet=packet) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=build_opener(ProxyHandler({}))
        def get(path):
            with opener.open(f'http://127.0.0.1:{server.server_port}'+path,timeout=5) as response:
                assert response.status==200 and response.headers['Cache-Control'].startswith('no-store')
                return json.load(response)
        try:
            before={path:get(path) for path in ('/acceptance.json','/rehearsal.json')}
            view=get('/paper-lab.json')
            assert view['state']=='LAB_OBSERVED' and view['cases']==body['cases']
            assert view['production_admission']=='BLOCKED' and view['execution_authorized'] is False
            assert view['postgres_major']==major
            for path,baseline in before.items():
                assert get(path)==baseline and baseline['execution_authorized'] is False
            doc['payload']['cases'][0]['reason']='RECONCILIATION_FAILED'
            packet.write_bytes(lab.canonical(doc))
            assert get('/paper-lab.json')==lab.unavailable()
            packet.write_bytes(original)
            assert get('/paper-lab.json')['cases'][0]['evidence_sha256']==digest
        finally:
            server.shutdown();thread.join(timeout=5)
    # Serving/reporting did not execute another cycle or introduce new effects.
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_effects WHERE job_run_id=:id'),{'id':run.job_run_id}).scalar_one()==len(effects)
    assert len(strategy.calls)==1
