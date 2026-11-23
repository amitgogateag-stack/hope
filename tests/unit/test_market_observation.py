"""Synthetic parser fixtures only. No test approval, signal, or provider acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from hope.api.market_observation import (
    SPECS, WATCHLIST, make_market_capture, market_observation, validated_reply,
)

COMMIT='a'*40
NOW=datetime(2026,11,23,17,tzinfo=timezone.utc)


def raw(value):return json.dumps(value,separators=(',',':')).encode()


def quote(key='USA:AAPL'):
    s=SPECS[key]
    opened=datetime(2026,11,20,14,30,tzinfo=timezone.utc) if s.market=='USA' else datetime(2026,11,20,3,45,tzinfo=timezone.utc)
    return dict(symbol=s.symbol,exchange=s.exchange,mic_code=s.mic,currency=s.currency,
        datetime='2026-11-20',timestamp=int(opened.timestamp()),last_quote_at=int((opened+timedelta(hours=6)).timestamp()),
        open='100',high='104',low='98',close='102',volume='12000',previous_close='100',
        change='2',percent_change='2.00000',is_market_open=False)


def history(key='USA:AAPL'):
    s=SPECS[key]
    return {'meta':dict(symbol=s.symbol,exchange=s.exchange,mic_code=s.mic,currency=s.currency,
        interval='1day',exchange_timezone=s.timezone,type='Common Stock'),
        'values':[dict(datetime='2026-11-20',open='100',high='104',low='98',close='102',volume='12000'),
                  dict(datetime='2026-11-19',open='99',high='103',low='98',close='100',volume='10000')]}


def reply(value=None,key='USA:AAPL',endpoint='quote'):
    return validated_reply(raw(quote(key) if value is None else value),instrument=key,
        endpoint=endpoint,received_at=NOW.isoformat())


def capture(replies=None):
    return make_market_capture([reply()] if replies is None else replies,
        deployed_commit=COMMIT,generated_at=NOW.isoformat())


def view(payload=None,now=NOW):
    return market_observation(capture() if payload is None else payload,expected_commit=COMMIT,now=now)


@pytest.mark.parametrize('key',['USA:AAPL','INDIA:INFY'])
def test_real_shape_observations_keep_market_currency_and_no_authority(key):
    replies=[reply(key=key),reply(history(key),key,'time_series')]
    result=view(capture(replies));row=next(x for x in result['watchlist'] if x['key']==key)
    assert len(WATCHLIST)==20 and len(result['watchlist'])==20
    assert row['currency']==SPECS[key].currency and row['timezone']==SPECS[key].timezone
    assert row['quote']['price']=='102' and row['quote']['change_percent']=='2.0000'
    assert row['quote']['observed_at']!=row['quote']['bar_open_at']
    assert row['quote']['freshness']=='STALE' and row['quote']['session_calendar_verified'] is False
    assert [b['datetime'] for b in row['history']['bars']]==['2026-11-19','2026-11-20']
    assert row['history']['available_at']==NOW.isoformat()
    assert row['history']['historical_publication_times']=='UNVERIFIED'
    assert row['strategy_eligible'] is row['pit_certified'] is row['execution_authorized'] is False
    assert result['independent_verified'] is result['live_enabled'] is result['execution_authorized'] is False
    assert result['scanner']['signals'] is None and result['paper']['pnl'] is None
    assert all(x['status']=='UNAVAILABLE' for x in result['indices'])


def test_polling_ages_original_source_without_refreshing_it():
    payload=capture();a=view(payload);b=view(payload,NOW+timedelta(seconds=60))
    qa=a['watchlist'][10]['quote'];qb=b['watchlist'][10]['quote']
    assert qa['observed_at']==qb['observed_at'] and qb['age_seconds']==qa['age_seconds']+60
    assert b['generated_at']==a['generated_at'] and b['capture_age_seconds']==60


def test_fresh_receipt_does_not_make_old_quote_current():
    assert view()['watchlist'][10]['quote']['freshness']=='STALE'


def test_empty_and_partial_observations_are_explicit_not_zero_metrics():
    result=view(capture([]))
    assert all(x['status']=='UNAVAILABLE' and x['quote'] is None for x in result['watchlist'])
    assert result['paper']==dict(state='NOT_EXECUTED',positions=None,cash=None,pnl=None)
    row=view(capture([reply(history(),endpoint='time_series')]))['watchlist'][10]
    assert row['status']=='AVAILABLE' and row['quote'] is None and len(row['history']['bars'])==2


@pytest.mark.parametrize('field,value',[('symbol','MSFT'),('exchange','NSE'),('currency','INR'),('mic_code','XNSE'),
    ('open','NaN'),('high','101'),('low','103'),('close','-1'),('close',102),('close','1e2'),
    ('volume','1.2'),('volume','-1'),('previous_close','0'),('change','20'),('percent_change','3'),
    ('percent_change','2.01'),('is_market_open',1),('timestamp',True),('last_quote_at',1),
    ('last_quote_at',int((NOW+timedelta(days=1)).timestamp())),('datetime','2026-02-30'),
    ('close','9'*48),('close','１２３')])
def test_malformed_quote_identity_accounting_and_clock_are_denied(field,value):
    q=quote();q[field]=value
    with pytest.raises(ValueError):reply(q)


def test_provider_representation_tolerance_does_not_supply_display_percent():
    q=quote();q.update(close='1098',open='1090',high='1119',low='1080',previous_close='1091.90002',
        change='6.099976',percent_change='0.55865697')
    assert view(capture([reply(q)]))['watchlist'][10]['quote']['change_percent']=='0.5587'
    q['percent_change']='0.56'
    with pytest.raises(ValueError,match='SOURCE_CONTRADICTION'):reply(q)


@pytest.mark.parametrize('mutation',['duplicate','future','bad_ohlcv','missing','timezone','interval','identity','not_list'])
def test_history_is_strict_and_cannot_backdate_availability(mutation):
    h=history()
    if mutation=='duplicate':h['values'][1]=deepcopy(h['values'][0])
    elif mutation=='future':h['values'][0]['datetime']='2026-11-24'
    elif mutation=='bad_ohlcv':h['values'][0]['low']='105'
    elif mutation=='missing':del h['values'][0]['volume']
    elif mutation=='timezone':h['meta']['exchange_timezone']='UTC'
    elif mutation=='interval':h['meta']['interval']='1min'
    elif mutation=='identity':h['meta']['currency']='INR'
    else:h['values']={}
    with pytest.raises(ValueError):reply(h,endpoint='time_series')


@pytest.mark.parametrize('payload',[b'{"status":"error","message":"secret"}',b'{"x":1,"x":2}',b'{"x":NaN}',b'['*40+b']'*40,b'x'*(2*1024*1024+1)], ids=['provider-error','duplicate','nonfinite','deep','oversize'])
def test_provider_errors_duplicate_nonfinite_depth_and_size_rejected(payload):
    with pytest.raises(ValueError):validated_reply(payload,instrument='USA:AAPL',endpoint='quote',received_at=NOW.isoformat())


@pytest.mark.parametrize('key,endpoint',[([], 'quote'),('USA:BAD','quote'),('USA:AAPL',{}),('USA:AAPL','orders')])
def test_unrecognized_instrument_or_endpoint_denied(key,endpoint):
    with pytest.raises(ValueError):reply(key=key,endpoint=endpoint,value=quote())


@pytest.mark.parametrize('mutation',['authority','live','pit','release','future_generation','future_receipt','hash','transport_hash','duplicate','unhashable','extra','wrong_schema'])
def test_capture_revalidation_rejects_changed_authority_or_provenance(mutation):
    v=json.loads(capture())
    if mutation in {'authority','live','pit'}:v[{'authority':'execution_authorized','live':'live_enabled','pit':'pit_certified'}[mutation]]=True
    elif mutation=='release':v['deployed_commit']='b'*40
    elif mutation=='future_generation':v['generated_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif mutation=='future_receipt':v['replies'][0]['received_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif mutation=='hash':v['replies'][0]['body_sha256']='0'*64
    elif mutation=='transport_hash':v['replies'][0]['transport_sha256']='invalid'
    elif mutation=='duplicate':v['replies']*=2
    elif mutation=='unhashable':v['replies'][0]['instrument']=[]
    elif mutation=='extra':v['approval']=True
    else:v['schema']='approved'
    with pytest.raises(ValueError):view(raw(v))


def test_even_rehashed_unsafe_body_is_revalidated():
    v=json.loads(capture());q=json.loads(v['replies'][0]['body']);q['close']='0'
    body=raw(q).decode();v['replies'][0].update(body=body,body_sha256=hashlib.sha256(body.encode()).hexdigest())
    with pytest.raises(ValueError):view(raw(v))


def test_historical_quote_and_history_disagreement_rejected():
    h=history();h['values'][0]['close']='103'
    with pytest.raises(ValueError,match='SOURCE_CONTRADICTION'):capture([reply(),reply(h,endpoint='time_series')])


def test_unknown_fields_are_never_projected_or_disclosed():
    q=quote();q['api_key']='synthetic-do-not-expose';q['html']='<script>bad</script>'
    value=reply(q)
    assert 'api_key' not in value['body'] and 'script' not in value['body']


@pytest.mark.parametrize('state',[True,False])
def test_provider_session_flag_is_not_a_calendar_approval(state):
    q=quote();q['is_market_open']=state
    result=view(capture([reply(q)]))['watchlist'][10]['quote']
    assert result['provider_market_open'] is state and result['session_calendar_verified'] is False
