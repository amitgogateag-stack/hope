// Execute the shipped JavaScript, not a separately implemented browser model.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(){this.children=[];this.dataset={};this.attributes={};this.textContent='';}
 append(x){this.children.push(x);}replaceChildren(){this.children=[];}setAttribute(k,v){this.attributes[k]=v;}addEventListener(){}
}
const elements={},document={getElementById:id=>elements[id]??=new Element(),createElement:()=>new Element(),createElementNS:()=>new Element()};
const context={document,module:{exports:{}},Date,TextEncoder,console};vm.createContext(context);
const html=fs.readFileSync(process.argv[2],'utf8');vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
const api=context.module.exports,stamp=new Date(Date.now()-3600e3).toISOString();
const names=['RELIANCE','TCS','HDFCBANK','ICICIBANK','INFY','ITC','SBIN','LT','BHARTIARTL','HINDUNILVR','AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','JPM','XOM'];
const rows=names.map((symbol,i)=>({key:(i<10?'INDIA':'USA')+':'+symbol,symbol,market:i<10?'INDIA':'USA',currency:i<10?'INR':'USD',status:'UNAVAILABLE',reason:'NO_AUTHORIZED_OBSERVATION',quote:null,history:null,execution_authorized:false,strategy_eligible:false,pit_certified:false}));
const base={schema:'hope.market-observation.v1',scope:'PRIVATE_OBSERVATION_ONLY',provider:'Twelve Data',access_mode:'DOCUMENTED_PUBLIC_TRIAL',execution_authorized:false,live_enabled:false,independent_verified:false,pit_certified:false,generated_at:stamp,watchlist:rows,indices:['Nifty 50','Sensex','Nasdaq Composite','S&P 500'].map(name=>({name,status:'UNAVAILABLE',reason:'NO_AUTHORIZED_OBSERVATION'})),scanner:{state:'NOT_EVALUATED',signals:null},paper:{state:'NOT_EXECUTED',positions:null,cash:null,pnl:null}};
const copy=()=>structuredClone(base);let v=copy();
v.watchlist[10].status='AVAILABLE';v.watchlist[10].quote={price:'102',previous_close:'100',change_percent:'2.0000',observed_at:stamp,session_calendar_verified:false,provider_market_open:true};
v.watchlist[10].history={available_at:stamp,historical_publication_times:'UNVERIFIED',bars:[{datetime:'2026-01-01',open:'99',high:'103',low:'98',close:'100',volume:'10'},{datetime:'2026-01-02',open:'100',high:'104',low:'99',close:'102',volume:'11'}]};
assert.equal(api.validateMarket(v),v);api.renderMarket(v);
assert.equal(elements.available.textContent,'1 / 20');assert.equal(elements.bars.textContent,2);assert.equal(elements.stale.textContent,1);assert.equal(elements.watchlist.children.length,20);assert.equal(elements.indices.children.length,4);assert.equal(elements.chart.children.length,1);assert.match(elements['chart-note'].textContent,/not independently verified/);assert.match(elements.availability.textContent,/never backdated/);
assert.equal(api.freshness(v.watchlist[10].quote),'STALE');assert.match(api.freshness({...v.watchlist[10].quote,observed_at:new Date().toISOString()}),/^RECENT/);
for(const mutate of [x=>x.execution_authorized=true,x=>x.independent_verified=true,x=>x.watchlist[10].quote.change_percent='9',x=>x.watchlist[10].quote.observed_at='bad',x=>x.watchlist[10].currency='INR',x=>x.watchlist[0].status='AVAILABLE',x=>x.watchlist[10].history.bars[0].low='104',x=>x.watchlist[10].history.bars[0].volume='-1',x=>x.watchlist.push(x.watchlist[0]),x=>x.paper.pnl='0',x=>x.scanner.signals=[],x=>x.indices[0].status='AVAILABLE',x=>x.watchlist[0].symbol='<script>bad</script>']){let bad=structuredClone(v);mutate(bad);assert.throws(()=>api.validateMarket(bad));}
api.unavailable();assert.equal(elements.watchlist.children.length,0);assert.equal(elements.chart.children.length,0);assert.equal(elements.available.textContent,'Unavailable');assert.match(elements.fatal.textContent,/UNAVAILABLE/);
assert.ok(!html.includes('innerHTML'));assert.match(html,/does not regenerate a database snapshot/);
console.log('market observation behavior passed');
