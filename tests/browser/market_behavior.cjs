// Execute the shipped JavaScript, not a separately implemented browser model.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(){this.children=[];this.dataset={};this.attributes={};this.textContent='';this.listeners={};}
 append(x){this.children.push(x);}replaceChildren(){this.children=[];}setAttribute(k,v){this.attributes[k]=v;}addEventListener(type,listener){this.listeners[type]=listener;}
}
const elements={},document={getElementById:id=>elements[id]??=new Element(),createElement:()=>new Element(),createElementNS:()=>new Element()};
const context={document,module:{exports:{}},Date,TextEncoder,console};vm.createContext(context);
const html=fs.readFileSync(process.argv[2],'utf8');vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
const api=context.module.exports,stamp=new Date(Date.now()-3600e3).toISOString();
const names=['RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','SBIN','LT','AXISBANK','ITC','BHARTIARTL','AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','JPM','AMD','SPY'];
const rows=names.map((symbol,i)=>({key:(i<10?'INDIA':'USA')+':'+symbol,symbol,market:i<10?'INDIA':'USA',currency:i<10?'INR':'USD',status:'UNAVAILABLE',reason:'NO_AUTHORIZED_OBSERVATION',quote:null,history:null,execution_authorized:false,strategy_eligible:false,pit_certified:false}));
const base={schema:'hope.market-observation.v1',scope:'PRIVATE_OBSERVATION_ONLY',provider:'Twelve Data',access_mode:'DOCUMENTED_PUBLIC_TRIAL',execution_authorized:false,live_enabled:false,independent_verified:false,pit_certified:false,generated_at:stamp,watchlist:rows,indices:['Nifty 50','Sensex','Nasdaq Composite','S&P 500'].map(name=>({name,status:'UNAVAILABLE',reason:'NO_AUTHORIZED_OBSERVATION'})),scanner:{state:'NOT_EVALUATED',signals:null},paper:{state:'NOT_EXECUTED',positions:null,cash:null,pnl:null}};
const copy=()=>structuredClone(base);let v=copy();
v.watchlist[10].status='AVAILABLE';v.watchlist[10].quote={price:'102',previous_close:'100',change_percent:'2.0000',observed_at:stamp,session_calendar_verified:false,provider_market_open:true};
v.watchlist[10].history={available_at:stamp,historical_publication_times:'UNVERIFIED',bars:[{datetime:'2026-01-01',open:'99',high:'103',low:'98',close:'100',volume:'10'},{datetime:'2026-01-02',open:'100',high:'104',low:'99',close:'102',volume:'11'}]};
assert.equal(api.validateMarket(v),v);api.renderMarket(v);
assert.equal(elements.available.textContent,'1 / 20');assert.equal(elements.bars.textContent,2);assert.equal(elements.stale.textContent,1);assert.equal(elements.watchlist.children.length,20);assert.equal(elements.indices.children.length,4);assert.equal(elements.chart.children.length,1);assert.match(elements['chart-note'].textContent,/not independently verified/);assert.match(elements.availability.textContent,/never backdated/);
assert.equal(api.freshness(v.watchlist[10].quote),'STALE');assert.match(api.freshness({...v.watchlist[10].quote,observed_at:new Date().toISOString()}),/^RECENT/);
for(const mutate of [x=>x.execution_authorized=true,x=>x.independent_verified=true,x=>x.watchlist[10].quote.change_percent='9',x=>x.watchlist[10].quote.observed_at='bad',x=>x.watchlist[10].currency='INR',x=>x.watchlist[0].status='AVAILABLE',x=>x.watchlist[10].history.bars[0].low='104',x=>x.watchlist[10].history.bars[0].volume='-1',x=>x.watchlist.push(x.watchlist[0]),x=>x.paper.pnl='0',x=>x.scanner.signals=[],x=>x.indices[0].status='AVAILABLE',x=>x.watchlist[0].symbol='<script>bad</script>']){let bad=structuredClone(v);mutate(bad);assert.throws(()=>api.validateMarket(bad));}
assert.equal(api.marketDay('2026-11-23T02:00:00Z','INDIA'),'2026-11-23');
assert.equal(api.marketDay('2026-11-23T02:00:00Z','USA'),'2026-11-22');
assert.equal(api.marketDay('2026-11-01T04:30:00Z','USA'),'2026-11-01');
assert.equal(api.marketDay('2026-11-02T04:30:00Z','USA'),'2026-11-01');
let boundary=structuredClone(v);boundary.watchlist[10].history.available_at='2026-11-23T02:00:00Z';boundary.watchlist[10].history.bars[0].datetime='2026-11-22';boundary.watchlist[10].history.bars[1].datetime='2026-11-23';assert.throws(()=>api.validateMarket(boundary));
let indiaRow={...boundary.watchlist[10],key:'INDIA:INFY',symbol:'INFY',market:'INDIA',currency:'INR'};boundary.watchlist[2]=indiaRow;boundary.watchlist[10]={...rows[10]};assert.equal(api.validateMarket(boundary),boundary);
// Older valid v1 projections remain visible; absent new details are not invented.
assert.equal(elements.watchlist.children[10].children[4].textContent,'Unavailable');
assert.equal(elements.watchlist.children[10].children[6].textContent,'Unavailable');
assert.match(elements['bar-readout'].textContent,/2026-01-02.*Open 100.*High 104.*Low 99.*Close 102 USD.*Volume 11/);
assert.equal(elements['bar-inspector'].hidden,false);
assert.equal(elements['bar-index'].max,'1');assert.equal(elements['bar-index'].value,'1');
const volumeRects=elements['volume-chart'].children[0].children.slice(0,2);
assert.equal(Number(volumeRects[1].attributes.height),70);
assert.equal(Number(volumeRects[0].attributes.height),10/11*70);
elements['bar-index'].value='0';elements['bar-index'].listeners.input();
assert.match(elements['bar-readout'].textContent,/2026-01-01.*Close 100 USD.*Volume 10/);
elements['bar-index'].value='999';elements['bar-index'].listeners.input();
assert.match(elements['bar-readout'].textContent,/Unavailable/);
let detailed=structuredClone(v);detailed.watchlist[10].quote.change_absolute='2';detailed.watchlist[10].quote.volume='12000';
api.renderMarket(detailed);
assert.equal(elements.watchlist.children[10].children[4].textContent,'2');
assert.equal(elements.watchlist.children[10].children[6].textContent,'12000');
assert.match(elements.watchlist.children[10].children[8].textContent,/^1h /);
let quoteOnly=structuredClone(detailed);quoteOnly.watchlist[10].history=null;api.unavailable();api.renderMarket(quoteOnly);assert.match(elements.session.textContent,/Open.*provider reported at receipt.*calendar UNVERIFIED/);assert.equal(elements['bar-inspector'].hidden,true);assert.equal(elements['volume-chart'].children.length,0);assert.equal(elements.watchlist.children[10].children[6].textContent,'12000');api.renderMarket(detailed);
for(const mutate of [x=>x.watchlist[10].quote.volume=null,x=>x.watchlist[10].quote.volume='-1',x=>x.watchlist[10].quote.volume='1.1',x=>x.watchlist[10].quote.volume='1000000000001',x=>x.watchlist[10].quote.change_absolute='3',x=>x.watchlist[10].quote.change_absolute=null,x=>x.watchlist[10].history.bars[0].datetime='2026-02-30',x=>x.watchlist[10].history.bars[1].volume='1000000000001']){let bad=structuredClone(detailed);mutate(bad);assert.throws(()=>api.validateMarket(bad));}
let exact=structuredClone(detailed);Object.assign(exact.watchlist[10].quote,{price:'0.30000000000000000000000000000000000001',previous_close:'0.1',change_absolute:'0.20000000000000000000000000000000000001',change_percent:'200'});
assert.equal(api.validateMarket(exact),exact);exact.watchlist[10].quote.change_absolute='0.2';assert.throws(()=>api.validateMarket(exact));
let wide=structuredClone(detailed);Object.assign(wide.watchlist[10].quote,{price:'1000000000000',previous_close:'0.'+'0'.repeat(7)+'1'+'0'.repeat(37)+'1',change_absolute:'999999999999.'+'9'.repeat(7)+'8'+'9'.repeat(38),change_percent:'9999999999999999999900.0000'});assert.ok(wide.watchlist[10].quote.change_absolute.length>48);assert.equal(api.validateMarket(wide),wide);wide.watchlist[10].quote.change_absolute='999999999999.99999999';assert.throws(()=>api.validateMarket(wide));
let zero=structuredClone(detailed);Object.assign(zero.watchlist[10].quote,{price:'100',change_absolute:'0',change_percent:'0',volume:'0'});zero.watchlist[10].history.bars.forEach(b=>b.volume='0');api.renderMarket(zero);
assert.equal(elements.watchlist.children[10].children[6].textContent,'0');assert.equal(Number(elements['volume-chart'].children[0].children[0].attributes.height),0);
let down=structuredClone(detailed);Object.assign(down.watchlist[10].quote,{price:'99',change_absolute:'-1',change_percent:'-1'});api.renderMarket(down);assert.equal(elements.watchlist.children[10].children[4].textContent,'-1');
const clock=Date.now();context.Date=class extends Date{static now(){return clock+60000;}};api.renderMarket(detailed);assert.match(elements.watchlist.children[10].children[8].textContent,/^1h 1m /);assert.equal(detailed.watchlist[10].quote.observed_at,stamp);assert.equal(detailed.watchlist[10].quote.volume,'12000');context.Date=Date;
api.unavailable();assert.equal(elements.watchlist.children.length,0);assert.equal(elements.chart.children.length,0);assert.equal(elements['volume-chart'].children.length,0);assert.equal(elements['bar-inspector'].hidden,true);assert.equal(elements['bar-readout'].textContent,'Unavailable');assert.equal(elements.available.textContent,'Unavailable');assert.match(elements.fatal.textContent,/UNAVAILABLE/);
assert.ok(!html.includes('innerHTML'));assert.match(html,/does not regenerate a database snapshot/);
console.log('market observation behavior passed');
