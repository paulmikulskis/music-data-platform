import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { randomUUID, createHash } from 'node:crypto';
import { serve } from '@hono/node-server';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createApp as dataApp } from '../../data-api/src/app.js';
import { entityId } from '../src/explorer-data.js';
import { readFileSync } from 'node:fs';
import { createRouterClient } from '@orpc/server';
import postgres from 'postgres';
import { app } from '../src/app.js';
import { router } from '../src/router.js';
import { database } from '../src/db.js';
import { authenticate } from '../src/auth.js';

const url=process.env.MDP_TENANTS_TEST_URL;
describe.skipIf(!url)('staff console callers', () => {
 let db:postgres.Sql, key:string, reader:string, staffActor:string, adminKey:string, warehouse:postgres.Sql;
 const adminIdentity={actor:`staff-test-${randomUUID()}`,admin:true,tenant_id:null,tenant_slug:null};
 const admin=()=>createRouterClient(router,{context:{identity:adminIdentity,db:database()}});
 const headers=()=>({'x-api-key':key});
 let insertedFunction=false;
 const forwarded:Record<string,unknown>[]=[];
 beforeAll(async()=>{
  db=postgres(url!,{onnotice:()=>{}});
  insertedFunction=(await db`INSERT INTO control.streamline(source_key,layer,writes,cadence_tag) VALUES ('fixture_accounts','bronze',ARRAY['raw.account_snapshots'],'hourly') ON CONFLICT(source_key) DO NOTHING RETURNING id`).length>0;
  const role=new URL(url!);role.username='control_rt';role.password='control_rt';
  vi.stubEnv('MDP_CONTROL_RT_URL',role.toString());vi.stubEnv('CLERK_SECRET_KEY','');vi.stubEnv('MDP_AUTH_MODE','production');
  vi.stubEnv('MDP_WORKBENCH_URL','http://workbench.fixture');vi.stubEnv('MDP_WORKBENCH_SERVICE_TOKEN','fixture');
  vi.stubEnv('MDP_SERVICE_URL','http://service.fixture');vi.stubEnv('MDP_SERVICE_TOKEN','fixture');
  const wh=new URL(url!);wh.pathname='/warehouse';
  warehouse=postgres(wh.toString(),{onnotice:()=>{}});
  vi.stubEnv('MDP_READER_URL',wh.toString());
  await warehouse`CREATE SCHEMA IF NOT EXISTS tenant_stafftest_marts`;
  await warehouse`CREATE TABLE raw.stafftest(id int)`;
  await warehouse`CREATE TABLE tenant_stafftest_marts.stafftest(id int)`;
  adminKey=randomUUID();
  await db`INSERT INTO control.api_key(key_hash,label,role) VALUES (${createHash('sha256').update(adminKey).digest('hex')},${adminIdentity.actor},'admin')`;
  key=(await admin().apiKeys.create({role:'staff',label:adminIdentity.actor,warehouse_role:'analyst_fixture'})).api_key;
  staffActor=(await authenticate(new Request("http://local",{headers:headers()}),database())).actor;
  reader=randomUUID();
  await db`INSERT INTO control.api_key(key_hash,label,role) VALUES (${createHash('sha256').update(reader).digest('hex')},${adminIdentity.actor},'reader')`;
  await db`INSERT INTO control.runbook(slug,title,body_md) VALUES ('forbidden','Console access','Ask an operator for staff access.') ON CONFLICT(slug) DO NOTHING`;
  vi.stubGlobal('fetch',vi.fn(async (url:URL,init:RequestInit)=>{
   const action=String(url).split('/').at(-1);
   const body=JSON.parse(String(init.body||'{}'));forwarded.push(body);
   const values:Record<string,unknown>={sandboxStatus:{sandboxes:[]},queries:{queries:[]},createSession:{sessionId:randomUUID(),scratchSchema:'wb_fixture'},query:{runId:randomUUID(),status:'queued',progress:0,error:null},previewModel:{runId:randomUUID(),status:'queued',progress:0,error:null},backtest:{runId:randomUUID(),status:'queued',progress:0,error:null},saveAsPr:{branch:'workbench/fixture',diff:'+select 1',pr_opened:false,url:null,message:'Review the diff.'}};
   if(action && values[action])return Response.json(values[action]);
   if(String(url).includes('/v1/functions/fixture_accounts?')){
    expect(String(url)).toContain('metadata_only=true');
    const recorded=JSON.parse(readFileSync(new URL('../../../packages/contracts/test/fixtures/_v1_functions_source_key__get.json',import.meta.url),'utf8')).response;
    return Response.json(recorded);
   }
   if(String(url).endsWith('/v1/health/detail'))return Response.json({status:'ok',components:{}});
   throw new Error(`Unexpected service request ${url}`);
  }));
 });
 afterAll(async()=>{
  vi.unstubAllGlobals();vi.unstubAllEnvs();
  if(warehouse){await warehouse`DROP TABLE raw.stafftest`;await warehouse`DROP SCHEMA tenant_stafftest_marts CASCADE`;await warehouse.end();}
  if(db){if(insertedFunction)await db`DELETE FROM control.streamline WHERE source_key='fixture_accounts'`;await db`DELETE FROM control.api_key WHERE label=${adminIdentity.actor}`;await db`DELETE FROM control.audit_log WHERE actor=${adminIdentity.actor} OR actor=${staffActor}`;await db.end();await database().end();}
 });
 it('creates and revokes staff keys through the CLI over HTTP',async()=>{
  let server:ReturnType<typeof serve>;
  const base=await new Promise<string>(resolve=>{server=serve({fetch:app.fetch,hostname:'127.0.0.1',port:0},info=>resolve(`http://127.0.0.1:${info.port}`));});
  const cli=(...args:string[])=>promisify(execFile)('pnpm',['--silent','mdp','keys',...args],{env:{PATH:process.env.PATH,HOME:process.env.HOME,MDP_CONTROL_API_URL:base,MDP_API_KEY:adminKey},timeout:15000});
  try{
   const issued=JSON.parse((await cli('create','--role','staff','--label',adminIdentity.actor,'--warehouse-role','analyst_fixture')).stdout);
   expect(issued).toMatchObject({role:'staff',tenant_id:null,warehouse_role:'analyst_fixture'});
   expect(JSON.parse((await cli('revoke',issued.id)).stdout).revoked_at).toBeTruthy();
  }finally{await new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()));}
 },30000);
 it('keeps raw and tenant explorer entries admin-only',async()=>{
  for(const relation of ['raw.stafftest','tenant_stafftest_marts.stafftest']){
   const path='/explorer/e/'+entityId('table',relation);
   expect((await app.request(path,{headers:headers()})).status).toBe(404);
   expect((await app.request(path,{headers:{'x-api-key':adminKey}})).status).toBe(200);
  }
 });
 it('issues, authenticates and revokes staff keys through the existing commands',async()=>{
  const identity=await authenticate(new Request('http://local',{headers:headers()}),database());
  expect(identity).toMatchObject({admin:false,staff:true,warehouse_role:'analyst_fixture',tenant_id:null});
  const issued=await admin().apiKeys.create({role:'staff',label:adminIdentity.actor});
  await admin().apiKeys.revoke({id:issued.id});
  await expect(authenticate(new Request('http://local',{headers:{'x-api-key':issued.api_key}}),database())).rejects.toMatchObject({status:401});
  expect((await app.request('/workbench',{headers:{'x-api-key':reader}})).status).toBe(403);
  expect((await dataApp(warehouse,db).request('/api/marts/mart_chart_history',{headers:headers()})).status).toBe(403);
 });
 it('keeps an admin metadata read free of previews',async()=>{
  const page=await admin().functions.page({source_key:'fixture_accounts',metadata_only:true,preview_table:'raw.account_snapshots'});
  expect(page.output_preview).toEqual([]);expect(page.rejected_sample).toEqual([]);expect(page.receipts).toEqual([]);
  const calls=vi.mocked(fetch).mock.calls.filter(([url])=>String(url).includes('/v1/functions/fixture_accounts?'));
  expect(String(calls.at(-1)![0])).toMatch(/\?metadata_only=true$/);
 });
 it('opens analysis and read pages and forwards Run under scoped ownership',async()=>{
  for(const path of ['/workbench','/sandbox','/sandboxes','/queries','/explorer','/reference','/ops','/status','/tenants','/functions','/functions/fixture_accounts','/functions/fixture_accounts/part/live','/functions/fixture_accounts/part/rows','/functions/fixture_accounts/part/targets','/functions/fixture_accounts/part/schema','/functions/fixture_accounts/part/settings','/functions/fixture_accounts/part/cursors','/runbooks/forbidden']){
   const response=await app.request(path,{headers:headers()});
   expect(response.status,path).toBe(200);
   const html=await response.text();
   if(path==='/functions/fixture_accounts' || path==='/functions/fixture_accounts/part/rows'){
    expect(html).toContain('Output previews need admin');
    expect(html).not.toContain('data-preview>');
   }
   for(const form of html.matchAll(/<form\b[^>]*action="\/actions\/[\s\S]*?<\/form>/g)){
    expect(form[0]).toContain('<fieldset disabled');expect(form[0]).toContain('/runbooks/forbidden');
   }
  }
  const identity=await authenticate(new Request('http://local',{headers:headers()}),database());
  const staff=createRouterClient(router,{context:{identity,db:database()}});
  const session=await staff.workbench.createSession({});
  expect(await staff.workbench.query({sessionId:session.sessionId,model:'mart_draft',sql:'select 1 as id'})).toMatchObject({status:'queued'});
  expect(forwarded.at(-1)).toMatchObject({staff:true,userId:`staff:${identity.actor}`,warehouseRole:'analyst_fixture'});
  const draft={sessionId:session.sessionId,model:'mart_draft',sql:'select 1 as id'};
  expect(await staff.workbench.previewModel({...draft,cycleId:randomUUID()})).toMatchObject({status:'queued'});
  expect(await staff.workbench.backtest({...draft,cycleA:randomUUID(),cycleB:randomUUID(),keyColumns:['id']})).toMatchObject({status:'queued'});
  const review=await staff.workbench.saveAsPr(draft);
  expect(review.reviewToken).toBeTruthy();
  await staff.workbench.saveAsPr({...draft,reviewToken:review.reviewToken});
  expect(forwarded.at(-1)).toMatchObject({staff:true,dryRun:false});
  await expect(staff.tenants.create({slug:'denied',name:'Denied'})).rejects.toMatchObject({status:403,data:{next_step:expect.any(String),runbook:'/runbooks/forbidden'}});
  await expect(staff.workbench.sandboxAction({schema:'sandbox_fixture',action:'freeze'})).rejects.toMatchObject({status:403});
 });
 it('refuses every operator POST endpoint and form with a role and next step',async()=>{
  const spec=JSON.parse(readFileSync(new URL('../../../packages/contracts/openapi/control-api.json',import.meta.url),'utf8'));
  for(const [path,methods] of Object.entries(spec.paths) as [string,Record<string,unknown>][]){
   if(!methods.post || path.startsWith('/workbench/'))continue;
   const response=await app.request('/api'+path,{method:'POST',headers:{...headers(),'content-type':'application/json'},body:'{}'});
   expect(response.status,path).toBe(403);
   expect(await response.json()).toMatchObject({message:expect.stringContaining('admin'),next_step:expect.any(String),runbook:'/runbooks/forbidden'});
  }
  const source=readFileSync(new URL('../src/pages.tsx',import.meta.url),'utf8');
  const actions=[...source.matchAll(/case ["']([^"']+)["']:/g)].map(m=>m[1]);
  expect(actions.length).toBeGreaterThan(20);
  for(const action of actions){
   const response=await app.request(`/actions/${action}`,{method:'POST',headers:headers()});
   expect(response.status,action).toBe(403);expect(await response.json()).toMatchObject({next_step:expect.any(String)});
  }
  for(const path of ['/sandboxes','/sandbox'])expect((await app.request(path,{method:'POST',headers:headers()})).status).toBe(403);
 });
 it('gives HTML refusals a role, next step and runbook, including restricted previews',async()=>{
  for(const path of ['/sandboxes?operator=1','/functions/fixture_accounts/preview.csv?table=raw.account_snapshots','/functions/fixture_accounts/preview.csv?table=tenant_fixture_marts.private']){
   const response=await app.request(path,{headers:headers()});
   expect(response.status,path).toBe(403);
   const html=await response.text();expect(html).toContain('admin');expect(html).toContain('Ask an operator');expect(html).toContain('/runbooks/forbidden');
  }
 });
});
