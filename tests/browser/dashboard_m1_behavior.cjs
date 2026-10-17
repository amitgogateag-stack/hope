// Execute the shipped inline JavaScript against deterministic browser surfaces.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const BASE = Date.parse('2026-10-16T12:00:00Z');
const iso = n => new Date(n).toISOString();
const sample = () => ({schema_version:1,source:'hope_postgresql',generated_at:iso(BASE),research:{run_count:0},paper:{environment_state:'HALTED',job_runs:{total:3,claimed:1,succeeded:1,failed:1},recovery:{claimed_with_effects:1,claimed_without_effects:0,claims_older_than_one_hour:1,reconciliation_receipts:1}}});
const ops = () => ({schema_version:1,generated_at:iso(BASE),state:'CURRENT',last_success_at:iso(BASE),cycles_succeeded:1,cycles_failed:0,jobs_executed:0,jobs_skipped:0,paper_only:true,live_enabled:false,deployed_commit:'a'.repeat(40),schema_compatibility:'COMPATIBLE'});
const response = value => ({ok:true,headers:{get:()=>null},text:async()=>JSON.stringify(value)});
const flush = () => new Promise(resolve=>setImmediate(resolve));
function browser() {
 let now=BASE, next=0;
 const elements=new Map(), timers=new Map(), calls=[];
 class Clock extends Date { static now() { return now; } }
 const document={querySelectorAll:()=>[],getElementById:id=>{
  if (!elements.has(id)) elements.set(id,{textContent:'Not connected',className:'',style:{},files:[],listeners:{},classList:{toggle(){}},addEventListener(name,fn){this.listeners[name]=fn;}});
  return elements.get(id);
 }};
 const context=vm.createContext({document,location:{protocol:'file:'},Date:Clock,AbortController,console,
  setInterval:()=>++next,
  setTimeout:(fn,ms)=>{const id=++next;timers.set(id,{fn,time:now+ms});return id;},
  clearTimeout:id=>timers.delete(id),
  fetch:(...args)=>{calls.push(args);return context.fetchImpl(...args);},
  fetchImpl:()=>Promise.reject(Error('unconfigured')),
 });
 vm.runInContext(script+`;globalThis.api={applySnapshot,clearSnapshot,retainSnapshotAfterFailure,applyLegacy,applyHealth,applyOperations,applyReadiness,clearReadiness,ageViews,requestJSON,refreshFeed};`,context);
 return {context,api:context.api,calls,element:document.getElementById,
  advance(ms){now+=ms;for(const [id,timer] of [...timers]) if(timer.time<=now){timers.delete(id);timer.fn();}context.api.ageViews();},
 };
}
async function main() {
 // Zero is evidence, unavailable is unknown, stale is retained visibly.
 {
  const b=browser(); b.api.applySnapshot(sample(),'local file');
  assert.equal(b.element('research-count').textContent,'0');
  assert.equal(b.element('paper-claimed').textContent,'1');
  b.advance(3600001);
  assert.match(b.element('snapshot-status').textContent,/STALE/);
  assert.match(b.element('paper-state').textContent,/STALE/);
  assert.match(b.element('recovery-status').textContent,/STALE/);
  b.api.clearSnapshot('Unavailable');
  assert.equal(b.element('research-count').textContent,'Not connected');
  assert.equal(b.element('paper-claimed').textContent,'Not connected');
  assert.match(html,/id="portfolio-state"[^>]*>UNAVAILABLE/);
 }
 // Concurrent remote behavior: failures retain verified data and its notice
 // while the independent clock still ages it to STALE; recovery clears notice.
 {
  const b=browser();b.api.applySnapshot(sample(),'automatic refresh');
  b.context.fetchImpl=async()=>{throw Error('temporarily unavailable');};
  await b.api.refreshFeed('/snapshot.json',32768,d=>b.api.applySnapshot(d,'automatic refresh'),b.api.retainSnapshotAfterFailure);
  assert.equal(b.element('paper-jobs').textContent,'3');
  assert.match(b.element('snapshot-status').textContent,/retaining last verified snapshot/);
  b.advance(3600001);
  assert.match(b.element('snapshot-status').textContent,/STALE.*retaining last verified snapshot/);
  assert.match(b.element('recovery-status').textContent,/STALE/);
  const fresh=sample();fresh.generated_at=iso(BASE+3600001);
  b.api.applySnapshot(fresh,'automatic refresh');
  assert.doesNotMatch(b.element('snapshot-status').textContent,/retaining|STALE/);
  b.api.clearSnapshot('Unavailable');b.api.retainSnapshotAfterFailure();
  assert.equal(b.element('paper-jobs').textContent,'Not connected');
 }
 {
  const b=browser(), data=sample();
  data.projections={strategy_catalog:{status:'AVAILABLE',total:1,truncated:false,rows:[{strategy_version_id:'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',family_code:'TIME_SERIES_TREND',candidate_state:'RESEARCH',markets:['USA']}]},
   dataset_certification:{status:'AVAILABLE',version_total:2,sealed_versions:1,pit_certified_versions:1,manifest_backed_sealed_versions:1},
   execution_ledger:{status:'AVAILABLE',fills:1,applied_fills:0,fill_applications:0,accounting_receipts:0,fills_without_application:1,applications_without_pnl:0},
   blocked_runs:{status:'AVAILABLE',total:1,by_reason:{PAPER_JOB_INCOMPLETE_PRIOR_CLAIM:0,PAPER_MARKET_DATA_STALE:1,PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT:0,OTHER:0}},
   decision_activity:{status:'AVAILABLE',no_trade_records:0,claimed_no_trade:0,latest_decision_at:null},
   certified_chain:{status:'AVAILABLE',authorization_receipts:1,operational_classifications:0,latest_authorization_at:iso(BASE-1000),eligibility_policy:'UNKNOWN'},
   certified_outcomes:{status:'AVAILABLE',total:1,claimed_records:0,latest_decision_at:iso(BASE-1000),by_outcome:{NO_TRADE:1,RISK_BLOCKED:0,FILLED:0},risk_blocked_reasons:{MAX_DAILY_LOSS_REACHED:0,MAX_DRAWDOWN_REACHED:0,MAX_POSITION_NOTIONAL_EXCEEDED:0,MAX_STRATEGY_EXPOSURE_EXCEEDED:0,CONCENTRATION_CONTEXT_REQUIRED:0,MAX_CONCENTRATION_EXPOSURE_EXCEEDED:0,MAX_GROSS_EXPOSURE_EXCEEDED:0,MAX_OPEN_POSITIONS_EXCEEDED:0,INTELLIGENCE_ENTRY_REVIEW_REQUIRED:0,OTHER:0}}};
  b.api.applySnapshot(data,'local file');
  assert.match(b.element('m2-strategy-catalog').textContent,/TIME_SERIES_TREND.*RESEARCH/);
  assert.match(b.element('m2-execution-ledger').textContent,/1 fills without application/);
  assert.match(b.element('m2-blocked-runs').textContent,/PAPER_MARKET_DATA_STALE: 1/);
  assert.match(b.element('m2-certified-chain').textContent,/1 historical receipts.*0 OPERATIONAL classifications.*eligibility UNKNOWN/);
  assert.match(b.element('m2-certified-outcomes').textContent,/NO_TRADE: 1/);
  b.advance(3600001);assert.match(b.element('m2-certified-outcomes').textContent,/STALE/);
  data.projections.certified_chain.eligibility_policy='ELIGIBLE';
  assert.throws(()=>b.api.applySnapshot(data,'local file'));
  data.projections.certified_chain.eligibility_policy='UNKNOWN';data.projections.strategy_catalog.rows[0].family_code='private';
  assert.throws(()=>b.api.applySnapshot(data,'local file'));
 }
 {
  const b=browser(), data=sample();
  data.projections={datasets:{status:'AVAILABLE',registered:0,pit_certified:0},strategies:{status:'UNAVAILABLE'},
   experiments:{status:'AVAILABLE',total:1,by_status:{CREATED:1,RUNNING:0,COMPLETED:0,FAILED:0,INVALIDATED:0,OTHER:0}},
   decisions:{status:'AVAILABLE',total:1,by_verdict:{SUPPORTED:0,WEAK_EVIDENCE:0,INCONCLUSIVE:1,REJECTED:0,INVALIDATED:0,REQUIRES_MORE_DATA:0}},
   paper_activity:{status:'AVAILABLE',last_completed_at:iso(BASE-1000),succeeded_no_trade:1},
   market_data:{status:'AVAILABLE',bar_count:0,latest_event_at:null,latest_available_at:null,freshness_policy:'UNKNOWN'},portfolio:{status:'UNAVAILABLE'}};
  b.api.applySnapshot(data,'local file');
  assert.match(b.element('projection-datasets').textContent,/0 registered/);
  assert.equal(b.element('projection-strategies').textContent,'UNAVAILABLE');
  assert.match(b.element('projection-experiments').textContent,/CREATED: 1/);
  assert.match(b.element('projection-decisions').textContent,/INCONCLUSIVE: 1/);
  assert.equal(b.element('projection-no-trade').textContent,'1');
  assert.match(b.element('projection-market').textContent,/policy UNKNOWN/);
  b.advance(3600001);assert.match(b.element('projection-no-trade').textContent,/STALE/);
  data.projections.datasets.pit_certified=1;
  assert.throws(()=>b.api.applySnapshot(data,'local file'));
  data.projections.datasets.pit_certified=0;data.projections.portfolio.status='AVAILABLE';
  assert.throws(()=>b.api.applySnapshot(data,'local file'));
 }
 {
  const b=browser(), data=sample(); delete data.paper.recovery;
  b.api.applySnapshot(data,'local file');
  assert.equal(b.element('recovery-with-effects').textContent,'Not connected');
  const invalid=sample(); invalid.paper.job_runs.claimed=true;
  assert.throws(()=>b.api.applySnapshot(invalid,'local file'));
  invalid.generated_at='2026-10-16T12:00:00';
  assert.throws(()=>b.api.applySnapshot(invalid,'local file'));
  const older=sample();older.generated_at=iso(BASE-1000);older.research.run_count=99;
  b.api.applySnapshot(older,'automatic refresh');
  assert.equal(b.element('research-runs').textContent,'0');
 }
 {
  const b=browser(); b.api.applyHealth({schema_version:1,service:'RUNNING',data_feed:'CURRENT',snapshot_generated_at:iso(BASE)});
  assert.equal(b.element('web-service').textContent,'RUNNING');
  b.advance(90001);
  assert.equal(b.element('web-service').textContent,'UNKNOWN');
  assert.equal(b.element('feed-health').textContent,'UNKNOWN');
  assert.throws(()=>b.api.applyHealth({schema_version:1,service:'RUNNING',data_feed:'CURRENT',snapshot_generated_at:null}));
 }
 {
  const b=browser(); b.api.applyOperations(ops());
  assert.equal(b.element('operations-jobs').textContent,'0 / 0');
  assert.equal(b.element('operations-state').textContent,'CURRENT');
  assert.equal(b.element('deployed-commit').textContent,'a'.repeat(40));
  b.advance(300001);
  assert.equal(b.element('operations-state').textContent,'STALE');
  assert.match(b.element('schema-compatibility').textContent,/STALE/);
  b.api.applyOperations({...ops(),generated_at:iso(BASE+300001),state:'UNAVAILABLE',error_code:'PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE'});
  assert.match(b.element('operations-note').textContent,/PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE/);
  b.api.applyOperations({...ops(),generated_at:iso(BASE+300001),state:'UNAVAILABLE',error_code:'PRIVATE_SECRET'});
  assert.doesNotMatch(b.element('operations-note').textContent,/PRIVATE_SECRET/);
  for(const change of [{live_enabled:true},{state:'LIVE'},{last_success_at:null},{cycles_failed:true},{deployed_commit:'private'}]) {
   assert.throws(()=>b.api.applyOperations({...ops(),...change}));
  }
 }
 {
  const b=browser();b.api.applyLegacy({schema_version:1,source:'hope_legacy_research_inventory',schema_compatible:true,paper:null,generated_at:iso(BASE),research:{registered_experiments:0,registered_strategy_versions:2,registered_datasets:3}});
  assert.equal(b.element('legacy-experiments').textContent,'0');
  b.advance(3600001);assert.match(b.element('legacy-status').textContent,/STALE/);
 }
 // An ignored abort still cannot later publish or overlap the old apply cycle.
 {
  const b=browser();let resolve;
  b.context.fetchImpl=()=>new Promise(r=>{resolve=r;});
  const first=b.api.requestJSON('/snapshot.json',32768);
  assert.equal(first,b.api.requestJSON('/snapshot.json',32768));
  assert.equal(b.calls.length,1);
  const failure=assert.rejects(first,/Read timeout/);
  b.advance(10000);await failure;
  assert.equal(b.calls[0][1].signal.aborted,true);
  resolve(response(sample()));await flush();
  assert.equal(b.element('paper-jobs').textContent,'Not connected');
  b.context.fetchImpl=async()=>response(sample());
  const read=()=>b.api.refreshFeed('/snapshot.json',32768,d=>b.api.applySnapshot(d,'automatic refresh'),()=>b.api.clearSnapshot('unavailable'));
  await Promise.all([read(),read()]);assert.equal(b.calls.length,2);
  assert.equal(b.element('paper-jobs').textContent,'3');
 }
 // Selecting a new local file invalidates an already outstanding network read.
 {
  const b=browser();let resolve;
  b.context.fetchImpl=()=>new Promise(r=>{resolve=r;});
  const network=b.api.refreshFeed('/snapshot.json',32768,d=>b.api.applySnapshot(d,'automatic refresh'),()=>b.api.clearSnapshot('failed'));
  const chosen=sample();chosen.research.run_count=8;
  b.element('snapshot-file').files=[{size:100,text:async()=>JSON.stringify(chosen)}];
  await b.element('snapshot-file').listeners.change();
  resolve(response(sample()));await network;
  assert.equal(b.element('research-count').textContent,'8');
  let oldFile;
  b.element('snapshot-file').files=[{size:100,text:()=>new Promise(r=>{oldFile=r;})}];
  const first=b.element('snapshot-file').listeners.change();
  const newest=sample();newest.research.run_count=12;
  b.element('snapshot-file').files=[{size:100,text:async()=>JSON.stringify(newest)}];
  await b.element('snapshot-file').listeners.change();oldFile(JSON.stringify(sample()));await first;
  assert.equal(b.element('research-count').textContent,'12');
 }
 // M3 observations distinguish unreachable health from web liveness and age independently.
 {
  const b=browser();
  const data={schema:'hope.operational-readiness.v1',generated_at:iso(BASE),server:'RUNNING',paper_health:'UNAVAILABLE',reason:'HEALTH_MISSING',worker_report:'UNKNOWN',snapshot:'CURRENT',database:'OBSERVED_AT_SNAPSHOT',reconciliation:'UNVERIFIED',last_successful_poll_at:null,last_successful_paper_at:null,last_failure_at:null,last_failure_code:null,execution_authorized:false,paper_only:true,live_enabled:false};
  b.api.applyReadiness(data);assert.equal(b.element('readiness-state').textContent,'UNAVAILABLE');assert.match(b.element('readiness-note').textContent,/HEALTH_MISSING/);
  b.api.applyReadiness({...data,paper_health:'READY',reason:'CURRENT_OBSERVATIONS_ONLY',worker_report:'CURRENT'});
  assert.equal(b.element('readiness-state').textContent,'READY');
  b.advance(300001);assert.equal(b.element('readiness-state').textContent,'UNAVAILABLE');assert.match(b.element('readiness-note').textContent,/STALE/);
  assert.throws(()=>b.api.applyReadiness({...data,execution_authorized:true}));
  assert.throws(()=>b.api.applyReadiness({...data,reason:'secret-payload'}));
  b.api.clearReadiness();assert.equal(b.element('readiness-state').textContent,'UNAVAILABLE');
 }
 console.log('Dashboard M1: aging, unavailable/zero, compatibility, heartbeat, deadlines, serialization and generation fencing passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
