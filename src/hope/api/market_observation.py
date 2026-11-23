"""Private provider observations; never a certified dataset or trading input.

No database, scheduler, broker, strategy factory or execution authority is imported.
Provider trial data is kept outside Git and never redistributed by this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from hope.api.snapshot_contract import strict_json

MAX_BYTES=2*1024*1024

@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    exchange: str
    market: str
    currency: str
    timezone: str
    mic: str

    @property
    def key(self):return self.market+':'+self.symbol


# Watchlist candidates only: not index-membership, instrument-registration or eligibility proof.
WATCHLIST=tuple(InstrumentSpec(s,'NSE','INDIA','INR','Asia/Kolkata','XNSE') for s in
    ('RELIANCE','TCS','HDFCBANK','ICICIBANK','INFY','ITC','SBIN','LT','BHARTIARTL','HINDUNILVR'))+tuple(
    InstrumentSpec(s,'NASDAQ' if s not in {'JPM','XOM'} else 'NYSE','USA','USD','America/New_York',
        'XNGS' if s not in {'JPM','XOM'} else 'XNYS') for s in
    ('AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','JPM','XOM'))
SPECS={s.key:s for s in WATCHLIST}
QUOTE_KEYS={'symbol','exchange','mic_code','currency','datetime','timestamp','last_quote_at',
            'open','high','low','close','volume','previous_close','change','percent_change','is_market_open'}
META_KEYS={'symbol','interval','currency','exchange_timezone','exchange','mic_code','type'}
BAR_KEYS={'datetime','open','high','low','close','volume'}
# Provider binary price formatting may differ from its percentage calculation.
# This is 0.0001 percentage point, not a strategy/risk threshold. Display is derived.
PERCENT_REPRESENTATION_TOLERANCE=Decimal('0.0001')


def _require(value,code):
    if not value:raise ValueError('MARKET_OBSERVATION_'+code)


def _time(value):
    _require(type(value) is str,'INVALID_TIMESTAMP')
    try:t=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError,TypeError):raise ValueError('MARKET_OBSERVATION_INVALID_TIMESTAMP') from None
    _require(t.tzinfo is not None and t.utcoffset() is not None,'INVALID_TIMESTAMP')
    return t.astimezone(timezone.utc)


def _decimal(value,*,positive=True,integer=False):
    _require(type(value) is str and len(value)<=48 and re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',value),'INVALID_PRICE')
    try:d=Decimal(value)
    except InvalidOperation:raise ValueError('MARKET_OBSERVATION_INVALID_PRICE') from None
    _require(d.is_finite() and abs(d)<=Decimal('1000000000000') and (d>=Decimal('0.00000001') if positive else d>=0) and (not integer or d==d.to_integral_value()),'INVALID_PRICE')
    return d


def _day(value):
    _require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value),'INVALID_TIMESTAMP')
    try:return date.fromisoformat(value)
    except ValueError:raise ValueError('MARKET_OBSERVATION_INVALID_TIMESTAMP') from None


def _epoch(value,received):
    _require(type(value) is int and 0<value<2**40,'INVALID_TIMESTAMP')
    try:t=datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OverflowError,OSError):raise ValueError('MARKET_OBSERVATION_INVALID_TIMESTAMP') from None
    _require(t<=received,'INVALID_TIMESTAMP');return t


def _ohlcv(row):
    for key in ('open','high','low','close'):_decimal(row[key])
    _decimal(row['volume'],positive=False,integer=True)
    o,h,l,c=(Decimal(row[k]) for k in ('open','high','low','close'))
    _require(l<=min(o,c)<=max(o,c)<=h,'INVALID_PRICE')


def _identity(value,spec,*,history=False):
    for k,v in [('symbol',spec.symbol),('exchange',spec.exchange),('currency',spec.currency),('mic_code',spec.mic)]:
        _require(value.get(k)==v,'IDENTITY_MISMATCH')
    if history:
        _require(value.get('interval')=='1day' and value.get('exchange_timezone')==spec.timezone
            and value.get('type')=='Common Stock','IDENTITY_MISMATCH')


def validated_reply(raw: bytes, *, instrument: str, endpoint: str, received_at: str) -> dict:
    """Freeze allowlisted provider fields; timestamps are receipt time, never backdated availability."""
    _require(type(raw) is bytes and 0<len(raw)<=MAX_BYTES,'INVALID_RESPONSE')
    _require(type(instrument) is str and type(endpoint) is str and instrument in SPECS and endpoint in {'quote','time_series'},'IDENTITY_MISMATCH')
    received=_time(received_at);spec=SPECS[instrument]
    value=strict_json(raw);_require(type(value) is dict and value.get('status')!='error','PROVIDER_ERROR')
    if endpoint=='quote':
        _require(QUOTE_KEYS<=set(value),'INVALID_RESPONSE');_identity(value,spec)
        body={k:value[k] for k in sorted(QUOTE_KEYS)};_ohlcv(body);_decimal(body['previous_close'])
        day=_day(body['datetime']);opened=_epoch(body['timestamp'],received);last=_epoch(body['last_quote_at'],received)
        _require(opened<=last and opened.astimezone(ZoneInfo(spec.timezone)).date()==day
            and last.astimezone(ZoneInfo(spec.timezone)).date()==day,'INVALID_TIMESTAMP')
        _require(type(body['is_market_open']) is bool,'INVALID_RESPONSE')
        for k in ('change','percent_change'):
            x=body[k];_require(type(x) is str and len(x)<=48 and re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',x),'INVALID_PRICE')
        change=Decimal(body['close'])-Decimal(body['previous_close'])
        _require(abs(change-Decimal(body['change']))<=Decimal('0.00001'),'SOURCE_CONTRADICTION')
        percent=change/Decimal(body['previous_close'])*100
        _require(abs(percent-Decimal(body['percent_change']))<=PERCENT_REPRESENTATION_TOLERANCE,'SOURCE_CONTRADICTION')
    else:
        _require(set(value)>={'meta','values'} and type(value['meta']) is dict,'INVALID_RESPONSE')
        _identity(value['meta'],spec,history=True)
        rows=value['values'];_require(type(rows) is list and 2<=len(rows)<=500,'INVALID_HISTORY')
        out=[];seen=set()
        for row in rows:
            _require(type(row) is dict and set(row)==BAR_KEYS,'INVALID_HISTORY')
            d=_day(row['datetime']);_require(d not in seen and d<=received.astimezone(ZoneInfo(spec.timezone)).date(),'INVALID_HISTORY')
            seen.add(d);_ohlcv(row);out.append(dict(row))
        body={'meta':{k:value['meta'][k] for k in sorted(META_KEYS)},'values':sorted(out,key=lambda r:r['datetime'])}
    projected=json.dumps(body,sort_keys=True,separators=(',',':'))
    return {'instrument':instrument,'endpoint':endpoint,'received_at':received.isoformat(),
        'body':projected,'body_sha256':hashlib.sha256(projected.encode()).hexdigest(),
        'transport_sha256':hashlib.sha256(raw).hexdigest()}


def make_market_capture(replies: list[dict], *, deployed_commit: str, generated_at: str) -> bytes:
    """Make a private observation capture; caller supplies no positive authority."""
    _require(type(deployed_commit) is str and re.fullmatch(r'[0-9a-f]{40}',deployed_commit),'RELEASE_INVALID')
    _time(generated_at)
    value={'schema':'hope.market-capture.v1','scope':'PRIVATE_OBSERVATION_ONLY','provider':'TWELVE_DATA',
        'access_mode':'DOCUMENTED_PUBLIC_TRIAL','deployed_commit':deployed_commit,'generated_at':generated_at,
        'execution_authorized':False,'live_enabled':False,'pit_certified':False,'replies':replies}
    payload=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    market_observation(payload,expected_commit=deployed_commit,now=_time(generated_at))
    return payload


def market_observation(payload: bytes, *, expected_commit: str, now: datetime) -> dict:
    """Revalidate private captured bytes on every request and age original source clocks.

    File hashes are integrity/audit references, not independent producer signatures.
    Browser polling or JSON generation never refreshes the provider's price clock.
    """
    _require(type(payload) is bytes and len(payload)<=MAX_BYTES,'INVALID_RESPONSE')
    _require(isinstance(now,datetime) and now.tzinfo is not None,'INVALID_TIMESTAMP');now=now.astimezone(timezone.utc)
    v=strict_json(payload)
    keys={'schema','scope','provider','access_mode','deployed_commit','generated_at','execution_authorized','live_enabled','pit_certified','replies'}
    _require(type(v) is dict and set(v)==keys and v['schema']=='hope.market-capture.v1'
        and v['scope']=='PRIVATE_OBSERVATION_ONLY' and v['provider']=='TWELVE_DATA'
        and v['access_mode']=='DOCUMENTED_PUBLIC_TRIAL','INVALID_RESPONSE')
    _require(all(v[k] is False for k in ('execution_authorized','live_enabled','pit_certified')),'AUTHORITY_FORBIDDEN')
    _require(type(expected_commit) is str and v['deployed_commit']==expected_commit and re.fullmatch(r'[0-9a-f]{40}',expected_commit),'RELEASE_INVALID')
    generated=_time(v['generated_at']);_require(generated<=now,'INVALID_TIMESTAMP')
    _require(type(v['replies']) is list and len(v['replies'])<=2*len(WATCHLIST),'INVALID_RESPONSE')
    observations={};seen=set()
    for reply in v['replies']:
        fields={'instrument','endpoint','received_at','body','body_sha256','transport_sha256'}
        _require(type(reply) is dict and set(reply)==fields and type(reply['body']) is str,'INVALID_RESPONSE')
        _require(type(reply['instrument']) is str and type(reply['endpoint']) is str,'INVALID_RESPONSE')
        pair=reply['instrument'],reply['endpoint'];_require(pair not in seen,'INVALID_RESPONSE');seen.add(pair)
        _require(_time(reply['received_at'])<=generated,'INVALID_TIMESTAMP')
        _require(type(reply['body_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',reply['body_sha256'])
            and hashlib.sha256(reply['body'].encode()).hexdigest()==reply['body_sha256']
            and type(reply['transport_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',reply['transport_sha256']),'INVALID_RESPONSE')
        safe=validated_reply(reply['body'].encode(),instrument=reply['instrument'],endpoint=reply['endpoint'],received_at=reply['received_at'])
        observations[pair]=strict_json(safe['body'])
    rows=[]
    for spec in WATCHLIST:
        q=observations.get((spec.key,'quote'));h=observations.get((spec.key,'time_series'))
        row={'key':spec.key,'symbol':spec.symbol,'exchange':spec.exchange,'market':spec.market,'currency':spec.currency,
            'timezone':spec.timezone,'status':'AVAILABLE' if q or h else 'UNAVAILABLE',
            'reason':None if q or h else 'NO_AUTHORIZED_OBSERVATION','quote':None,'history':None,
            'pit_certified':False,'strategy_eligible':False,'execution_authorized':False}
        if h:row['history']={'bars':h['values'],'available_at':next(r['received_at'] for r in v['replies'] if r['instrument']==spec.key and r['endpoint']=='time_series'),
            'historical_publication_times':'UNVERIFIED','finality':'PROVIDER_REPORTED'}
        if q:
            last=datetime.fromtimestamp(q['last_quote_at'],timezone.utc)
            percent=(Decimal(q['close'])-Decimal(q['previous_close']))/Decimal(q['previous_close'])*100
            row['quote']={'price':q['close'],'previous_close':q['previous_close'],'change_percent':format(percent.quantize(Decimal('0.0001')),'f'),
                'bar_date':q['datetime'],'bar_open_at':datetime.fromtimestamp(q['timestamp'],timezone.utc).isoformat(),
                'observed_at':last.isoformat(),'age_seconds':int((now-last).total_seconds()),
                'freshness':'STALE' if (now-last).total_seconds()>900 else 'RECENT_PROVIDER_OBSERVATION',
                'provider_market_open':q['is_market_open'],'session_calendar_verified':False,'delay_classification':'TRIAL_PROVIDER_REPORTED'}
            if h and q['datetime']==h['values'][-1]['datetime'] and _day(q['datetime'])<now.astimezone(ZoneInfo(spec.timezone)).date():
                _require(Decimal(q['close'])==Decimal(h['values'][-1]['close']) and Decimal(q['previous_close'])==Decimal(h['values'][-2]['close']),'SOURCE_CONTRADICTION')
        rows.append(row)
    return {'schema':'hope.market-observation.v1','scope':'PRIVATE_OBSERVATION_ONLY','provider':'Twelve Data',
        'access_mode':'DOCUMENTED_PUBLIC_TRIAL','generated_at':generated.isoformat(),'observed_at':now.isoformat(),
        'capture_age_seconds':int((now-generated).total_seconds()),'watchlist':rows,
        'indices':[{'name':name,'status':'UNAVAILABLE','reason':'NO_AUTHORIZED_OBSERVATION'} for name in ('Nifty 50','Sensex','Nasdaq Composite','S&P 500')],
        'scanner':{'state':'NOT_EVALUATED','reason':'NO_QUALIFIED_STRATEGY_BINDING','signals':None},
        'paper':{'state':'NOT_EXECUTED','positions':None,'cash':None,'pnl':None},
        'execution_authorized':False,'live_enabled':False,'independent_verified':False,'pit_certified':False}
