import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRouterClient } from '@orpc/server';
import type { Context } from '../src/router.js';
vi.mock('../src/db.js',async original=>({...await original<typeof import('../src/db.js')>(),database:()=>({unsafe:vi.fn().mockResolvedValue([])})}));
import { workbenchRouter } from '../src/workbench-router.js';

const draft={sessionId:'f461f1e3-6bcf-4a42-a743-b0dba4b63a32',model:'mart_review',sql:'select 1 as id'};
const result={branch:'workbench/review',diff:'--- sql\n+select 1 as id\n--- yaml\n+name: id',pr_opened:false,url:null,message:'Preview'};
function setup(){
 vi.stubEnv('MDP_WORKBENCH_URL','http://localhost:8085');vi.stubEnv('MDP_WORKBENCH_SERVICE_TOKEN','test-token');
 const requests:Record<string,unknown>[]=[];
 const fetch=vi.fn(async(_url:unknown,init:RequestInit)=>{requests.push(JSON.parse(String(init.body)));return Response.json(result)});
 vi.stubGlobal('fetch',fetch);
 const context:Context={identity:{actor:'dev-user',admin:true,tenant_id:null,tenant_slug:null},db:{unsafe:vi.fn()}};
 return {client:createRouterClient(workbenchRouter,{context}),requests,fetch,context};
}
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();vi.useRealTimers()});
describe('workbench review gate',()=>{
 it('always previews first, requires the same draft and actor, and consumes approval once',async()=>{
  const {client,requests,context}=setup();
  const preview=await client.saveAsPr(draft);
  expect(preview.reviewToken).toBeTruthy();expect(requests.map(r=>r.dryRun)).toEqual([true]);
  await expect(client.saveAsPr({...draft,sql:'select 2 as id',reviewToken:preview.reviewToken})).rejects.toThrow(/Review this exact/);
  const other=createRouterClient(workbenchRouter,{context:{...context,identity:{...context.identity,actor:'another-admin'}}});
  await expect(other.saveAsPr({...draft,reviewToken:preview.reviewToken})).rejects.toThrow(/Review this exact/);
  expect(requests).toHaveLength(1);
  await client.saveAsPr({...draft,reviewToken:preview.reviewToken});
  expect(requests.map(r=>r.dryRun)).toEqual([true,true,false]);
  await expect(client.saveAsPr({...draft,reviewToken:preview.reviewToken})).rejects.toThrow(/Review this exact/);
  expect(requests).toHaveLength(3);
 });
 it('fails closed on expired approval and on changed repository diff',async()=>{
  const {client,requests,fetch}=setup();
  const preview=await client.saveAsPr(draft);
  fetch.mockImplementationOnce(async()=>Response.json({...result,diff:'changed YAML'}));
  await expect(client.saveAsPr({...draft,reviewToken:preview.reviewToken})).rejects.toThrow(/Repository files changed/);
  expect(requests.every(r=>r.dryRun===true)).toBe(true);
  const next=await client.saveAsPr(draft);
  vi.useFakeTimers();vi.setSystemTime(Date.now()+16*60*1000);
  await expect(client.saveAsPr({...draft,reviewToken:next.reviewToken})).rejects.toThrow(/Review this exact/);
 });
});

it('preserves a missing-model response when no local cycle exists', async () => {
 const {client,fetch}=setup();
 fetch.mockImplementationOnce(async()=>Response.json({
  error_class:'model_not_built',
  message:'mart_absent is not built here. Run the local build command in Explorer.',
 },{status:409}));
 await expect(client.previewModel({...draft,model:'mart_absent'})).rejects.toThrow('mart_absent is not built here');
});
