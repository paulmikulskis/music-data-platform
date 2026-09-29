import { describe, expect, it } from 'vitest';
import { CoverageMeter, TargetHealth, FailureBanner, DriftPopover, RefusalPopover, LabelChips, CadencePill, ChangedDot, CopyChip, RelativeTime } from '../src/primitives.js';
import { writerScope, healthState, relationLabels, schemaChanges, driftHistory, refusalInfo } from '../src/console-semantics.js';
import { StatusCard, Result, Table } from '../src/pages.js';
import { uiScript } from '../src/ui.js';
import { runCoverage, consoleFailures } from '../src/console-data.js';
import type { DB } from '../src/db.js';
const health={id:'target-a',name:'Example',active:true,resolved:true};
const render=async (value:unknown)=>String(await value);

describe('console semantics',()=>{
 it('counts durable target completion, subtracts failure markers and does not count retries twice',async()=>{
  const records=[
   {run_id:'r',id:'a',completed:['a'],status:'succeeded',reason:'old failure'},
   {run_id:'r',id:'a',completed:['a'],status:'succeeded'},
   {run_id:'r',id:'b',completed:['b','stale_target:b'],status:'partial',reason:'HTTP 404'},
   {run_id:'r',id:'c',completed:[],status:'running'},
  ];
  const db={unsafe:async()=>records} as unknown as DB;
  expect((await runCoverage(db,['r'])).r).toEqual({succeeded:1,total:3,floor:1,failures:[{id:'b',name:'b',reason:'HTTP 404'}]});
 });
 it('uses text, target links, a floor and a native keyboard disclosure for coverage',async()=>{
  const html=await render(CoverageMeter({coverage:{succeeded:58,total:59,floor:.95,failures:[{id:'a',name:'Missing chart',reason:'HTTP 404'}]}}));
  expect(html).toContain('58 / 59 targets succeeded');expect(html).toContain('floor 95%');expect(html).toContain('/targets/a');expect(html).toContain('<summary>');expect(html).toContain('HTTP 404');
  expect(await render(CoverageMeter({}))).toContain('not recorded');
  expect(await render(CoverageMeter({coverage:{succeeded:0,total:0,failures:[]}}))).toContain('No targets');
 });
 it('keeps unknown, stale, dead, parked and recovered targets distinct',async()=>{
  expect(healthState(health)).toBe('not read yet');
  expect(healthState({...health,at:'2026-09-25',http_status:404,missing_streak:1})).toBe('stale');
  expect(healthState({...health,at:'2026-09-25',http_status:410,missing_streak:2})).toBe('dead');
  expect(healthState({...health,parked:true})).toBe('parked');
  expect(healthState({...health,at:'2026-09-25',result:'succeeded',http_status:200,missing_streak:0})).toBe('healthy');
  const html=await render(TargetHealth({health:{...health,http_status:404,missing_streak:2,at:'2026-09-25T00:00:00Z'}}));
  expect(html).toContain('dead');expect(html).toContain('Last HTTP response: 404');expect(html).toContain('Deactivate');expect(html).toContain('/actions/activate');expect(html).not.toContain('Probe target');
  expect(await render(TargetHealth({health:{...health,active:false,probe_action:'/actions/probe-target'}}))).toContain('Reactivate');
  expect(await render(TargetHealth({health:{...health,probe_action:'https://unsafe.example/'}}))).not.toContain('Probe target');
 });
 it('shows the failed model, plain cause and cycle retry even without an alert',async()=>{
  const db={unsafe:async(query:string)=>query.includes('control.cycle')?[{id:'cycle',cadence:'daily',scope:'global',run_id:'run',source_key:'chart',error_class:'partial_not_allowed'}]:[]} as unknown as DB;
  const failures=await consoleFailures(db);
  const html=await render(FailureBanner({failures}));
  expect(html).toContain('bronze_invoke__chart');expect(html).toContain('Some targets failed');expect(html).toContain('/actions/retry');expect(html).toContain('name="cycle_id" value="cycle"');expect(html).toContain('Recovery guide');
  expect(FailureBanner({failures:[]})).toBeNull();
 });
 it('compares types as well as names, keeps tables separate and flags only supplied deploy provenance',async()=>{
  const changes=schemaChanges({count:'integer'},{count:'bigint',owner_class:'text'},['owner_class']);
  const html=await render(DriftPopover({changes,source:'source_a',fingerprint:'fingerprint'}));
  expect(html).toContain('integer → bigint');expect(html).toContain('added text');expect(html).toContain('Declared change');expect(html).toContain('/actions/drift');
  const h=driftHistory([{table:'a',columns:{x:'int'}},{table:'b',columns:{y:'int'}},{table:'a',columns:{x:'bigint'}}]);
  expect(h[1]?.changes).toEqual([]);expect(h[2]?.changes).toEqual([{name:'x',before:'int',after:'bigint',own_code:false}]);
 });
 it('explains every refusal with a recovery path',async()=>{
  for(const code of ['cookie_refused','scrape_blocked','transport_refused','llm_host_refused']){
   expect(refusalInfo(code)).not.toBeNull();const html=await render(RefusalPopover({code,message:'<script>bad</script>'}));
   expect(html).toContain('Next step:');expect(html).toContain('Open trace');expect(html).not.toContain('<script>bad');
  }
  expect(await render(RefusalPopover({code:'parse_error'}))).toContain('View audit');
  const cookies=await render(RefusalPopover({code:'forbidden_path',message:'Cookies are refused because visitor sessions cannot be reused.'}));
  expect(cookies).toContain('declared public hosts without cookies');
  const model=await render(RefusalPopover({code:'forbidden_path',message:'Model calls outside gold are refused because enrichment needs a prompt, budget and lineage.'}));
  expect(model).toContain('declared gold step');
  expect(await render(Table({nextStep:{label:"View functions",href:"/functions"},rows:[{event_type:'transport_refused',message:'Blocked'}]}))).toContain('Next step:');
  // Successful info events read as what happened; only errors and warnings carry the refusal hint.
  const events=await render(Table({nextStep:{label:"View functions",href:"/functions"},rows:[
   {level:'info',event_type:'page_published',message:'Page 1 published'},
   {level:'info',event_type:'budget_unlimited',message:'No budget cap'},
   {level:'error',event_type:'cadence_failed',message:'Daily build failed'},
   {level:'info',event_type:'dump_loaded',error_class:'load_failed',message:'Load failed'},
  ]}));
  expect(events.match(/Request refused/g)).toHaveLength(2);
  expect(events).toContain('page published');expect(events).toContain('budget unlimited');
  expect(events).not.toContain('Request refused · page published');expect(events).not.toContain('Request refused · budget unlimited');
 });
 it('derives labels conservatively and keeps learning separate from resale',async()=>{
  expect(relationLabels('staging.stg_playlist__items').category).toBe('personal');
  expect(relationLabels('tenant_demo_marts.mart_example').tenant).toBe('demo');
  expect(relationLabels('model', ['layer:gold','scope:tenant'])).toEqual({layer:'gold',tenant:'tenant scoped'});
  const html=await render(LabelChips({labels:{layer:'silver',category:'B',tenant:'global',learning_eligible:false,resale_permitted:true}}));
  expect(html).toContain('no learning');expect(html).toContain('resale allowed');expect(html).toContain('category: B');
  expect(await render(LabelChips({labels:{}}))).toContain('learning unknown');
 });
 it('shows cadence due time, open age and optional health without inventing them',async()=>{
  const html=await render(CadencePill({cadence:'daily',scope:'global',interval:86400,lastAge:3600,overdue:false,openAge:600}));
  expect(html).toContain('Next due in 1380 min');expect(html).toContain('open for 10 min');
  const status={heartbeat:null,retry_launcher:{state:'missing' as const,message:'Configure the Retry launcher'},checked_at:new Date().toISOString(),verdict:'attention' as const,reasons:[],rules:{},cadences:[],alerts:[],sends:{failed:'0',pending:'0',skipped:'0',gave_up:'0'},versions:{state:'unknown' as const,control_sha:null,data_sha:null}};
  expect(await render(StatusCard({status}))).toContain('Retry launcher:');
  expect(await render(StatusCard({status:{...status,retry_launcher:{state:'configured',message:'Retry is available'},secrets_health:{state:'missing'}}}))).toContain('Secrets health');
 });
 it('retains audit feedback, relative times, copy controls and a private alert watermark',async()=>{
  expect(await render(Result({result:{audit_id:'audit-a',action:'targets.bulkActivate',result:{active:false}}}))).toContain('/audit/audit-a');
  expect(await render(CopyChip({value:'id-a'}))).toContain('aria-label="Copy id-a"');
  expect(await render(RelativeTime({at:new Date(Date.now()-120000).toISOString()}))).toContain('2m ago');
  expect(await render(ChangedDot({at:'2026-09-25T00:00:00Z'}))).toContain('hidden');
  expect(uiScript).toContain('mdp-alert-visit');expect(uiScript).toContain("e.key==='Escape'");expect(()=>new Function(uiScript)).not.toThrow();
 });
});
it('does not call a later transport refusal dead because of an older 404 streak',()=>{
 expect(healthState({...health,at:'2026-09-25T00:00:00Z',result:'forbidden_path: request refused',http_status:404,missing_streak:3})).toBe('stale');
});

it('labels shared raw writers without hiding tenant material',()=>{
 expect(writerScope([false])).toBeUndefined();
 expect(writerScope([true])).toBe('tenant scoped');
 expect(writerScope([false,true])).toBe('mixed global and tenant');
});
