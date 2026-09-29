import { describe,it,expect,vi } from 'vitest';
import { Hono } from 'hono';
import type { Context } from '../src/router.shared.js';
const service=vi.hoisted(()=>({
 createSession:vi.fn(),query:vi.fn(),status:vi.fn(),backtest:vi.fn(),cancel:vi.fn(),models:vi.fn(),draft:vi.fn(),history:vi.fn(),saveAsPr:vi.fn(),result:vi.fn(),explain:vi.fn(),artifact:vi.fn(),
}));
vi.mock('@orpc/server',async original=>({...await original<typeof import('@orpc/server')>(),createRouterClient:()=>({workbench:service})}));
import { workbenchPages } from '../src/workbench-page.js';
import { changedFiles, isNumericType } from '../src/workbench-visual.js';

const sessionId='f461f1e3-6bcf-4a42-a743-b0dba4b63a32';
const runId='fd1d223f-7d8e-45cd-b84a-a14ecf61b6b8';
const cycleId='f139fa89-8bc9-4601-8b3d-a385725e53e3';
const sql='select 1 as one';
function testApp(admin=true){
 const app=new Hono<{Variables:{context:Context}}>();
 app.use('*',async(c,next)=>{
  c.set('context',{identity:{actor:'fixture',admin,staff:!admin,tenant_id:null,tenant_slug:null},db:{unsafe:vi.fn()}});
  await next();
 });
 return app.route('/workbench',workbenchPages);
}

describe('workbench publication presentation',()=>{
 it('retains the qualifying result and run link after the dry-run response',async()=>{
  service.models.mockResolvedValue({models:[],cycles:[cycleId],sources:[],cycleDetails:[]});
  service.draft.mockResolvedValue({model:'mart_visual_fixture',sql});
  service.history.mockResolvedValue({runs:[{id:runId,status:'succeeded',created_at:'2026-09-18T08:00:00Z',kind:'preview',input:{model:'mart_visual_fixture',sql,cycleId}}]});
  service.saveAsPr.mockResolvedValue({branch:'workbench/fixture',diff:'+select 1 as one',pr_opened:false,url:null,reviewToken:sessionId,message:'Dry run: generated files passed YAML and dbt parse validation.'});
  service.result.mockResolvedValue({columns:[{name:'one',type:'integer',nullable:false,source:null}],rows:[{one:1}],compiledSql:'\n\nselect 1 as one',upstream:[],timingMs:1250,artifactRef:'fixture-result'});
  service.explain.mockResolvedValue({compiledSql:sql,upstream:[],downstream:[],sourceFreshness:[],producingRuns:[]});
  service.artifact.mockResolvedValue({url:'/workbench/artifact?ref=fixture-result',expiresIn:300});
  const app=testApp();
  const response=await app.request('/workbench',{method:'POST',body:new URLSearchParams({action:'save',sessionId,model:'mart_visual_fixture',sql,cycleA:cycleId,cycleB:cycleId,operation:'preview'})});
  const html=await response.text();
  expect(response.status).toBe(200);
  expect(html).toContain('Draft ready');
  expect(html).toContain('Preview results');expect(html).toContain('1 row');
  expect(html).toContain(`/workbench?runId=${runId}`);
  expect(html).toContain('Reopen successful run');expect(html).toContain('Diff expires in 15 min');expect(html).toContain('Open pull request');
  expect(service.saveAsPr).toHaveBeenCalledWith({sessionId,model:'mart_visual_fixture',sql});
  expect(html).not.toContain('Explain this model');expect(html).not.toContain('Freshness:');
  expect(html).toContain('<pre>select 1 as one</pre>');
  expect(service.result).toHaveBeenCalledWith({runId});
 });
  it.each([
    { title: "no GitHub access ends with a patch", prOpened: false },
    { title: "an opened pull request without a URL names its branch", prOpened: true },
  ])(
    "$title",
    async ({ prOpened }) => {
      service.models.mockResolvedValue({
        models: [],
        cycles: [cycleId],
        sources: [],
        cycleDetails: [],
      });
      service.draft.mockResolvedValue({ model: "mart_visual_fixture", sql });
      service.history.mockResolvedValue({ runs: [] });
      const branch = "workbench/fixture-mart_visual_fixture";
      const diff =
        "--- /dev/null\n+++ b/dbt/models/marts/mart_visual_fixture.sql\n@@ -0,0 +1 @@\n+select 1 as one\n";
      const message =
        "No pull request opened: GitHub access is not set. Apply the patch in your clone.";
      service.saveAsPr.mockResolvedValue({
        branch,
        diff,
        pr_opened: prOpened,
        url: null,
        message,
      });
      const response = await testApp().request("/workbench", {
        method: "POST",
        body: new URLSearchParams({
          action: "confirm-save",
          sessionId,
          model: "mart_visual_fixture",
          sql,
          reviewToken: sessionId,
        }),
      });
      const html = await response.text();
      expect(response.status).toBe(200);
      expect(service.saveAsPr).toHaveBeenLastCalledWith({
        sessionId,
        model: "mart_visual_fixture",
        sql,
        reviewToken: sessionId,
      });
      expect(html).not.toContain('href="#"');
      expect(html).not.toContain("PR #undefined");
      expect(html).not.toContain("Open the pull request");
      if (prOpened) {
        expect(html).toContain(
          `Pull request opened from branch ${branch}. Find it on GitHub by that branch.`,
        );
        expect(html).toContain(
          "Check CI and use the workbench label for review.",
        );
        expect(html).not.toContain("Download patch");
      } else {
        expect(html).toContain("Patch ready");
        expect(html).toContain(message);
        expect(html).toContain("Download patch");
        expect(html).toContain('download="mart_visual_fixture.patch"');
        expect(html).toContain(
          `href="data:text/x-diff;charset=utf-8,${encodeURIComponent(diff)}"`,
        );
        expect(html).toContain("Copy patch");
        expect(html).toContain(
          `git fetch origin &amp;&amp; git switch -c ${branch} origin/main`,
        );
        expect(html).toContain("git apply mart_visual_fixture.patch");
        expect(html).toContain("bash ops/ready.sh");
        expect(html.match(/>Copy command<\/button>/g)).toHaveLength(3);
        expect(html).toContain("Send the patch file");
        expect(html).not.toContain("Check CI");
        expect(html).not.toContain("Diff expires");
      }
    },
  );
 it('renders added and removed rows, and keeps the selected history entry current',async()=>{
  service.models.mockResolvedValue({models:[],cycles:[cycleId,runId],sources:[],cycleDetails:[]});
  service.draft.mockResolvedValue({model:'mart_visual_fixture',sql});
  service.history.mockResolvedValue({runs:[{id:runId,status:'succeeded',created_at:'2026-09-18T08:00:00Z',kind:'backtest',input:{model:'mart_visual_fixture',sql,cycleA:cycleId,cycleB:runId}}]});
  service.status.mockResolvedValue({runId,status:'succeeded',progress:100,error:null});
  const build={columns:[{name:'id',type:'integer',nullable:false,source:null}],rows:[{id:2}],compiledSql:sql,upstream:[],timingMs:1000,artifactRef:'fixture'};
  service.result.mockResolvedValue({buildA:{...build,rows:[{id:1}]},buildB:build,keyColumns:['id'],comparedColumns:['id'],added:[{id:2}],removed:[{id:1}],changed:[],summary:{}});
  const app=testApp();
  const html=await (await app.request('/workbench?runId='+runId,{headers:{cookie:'mdp_workbench_session='+sessionId}})).text();
  expect(html).toContain('✓ Added');expect(html).toContain('− Removed');expect(html).toContain('class="added-row"');
  expect(html).not.toContain('&quot;id&quot;');expect(html).not.toContain('<td>All</td>');
  expect(html).not.toContain('No business values changed');expect(html).toContain('aria-current="page"');
  expect(html).toContain('data-land="true"');expect(html).not.toContain('Compared: id');
 });
 it('reopens a query without leaking its cycle into B or a comparison notice',async()=>{
  service.history.mockResolvedValue({runs:[{id:runId,status:'cancelled',created_at:'2026-09-18T08:00:00Z',kind:'preview',input:{operation:'query',model:'mart_visual_fixture',sql,cycleId:runId}}]});
  service.status.mockResolvedValue({runId,status:'cancelled',progress:0,error:null});
  const app=testApp();
  const html=await (await app.request('/workbench?runId='+runId,{headers:{cookie:'mdp_workbench_session='+sessionId}})).text();
  expect(html).not.toContain('Newer cycle available');
  expect(html).toContain('<select name="cycleB"><option value="'+cycleId+'" selected');
  expect(html).toContain('Cancelled · mart_visual_fixture');
  expect(html).toContain('action="/workbench#wb-ticket"');
 });

 it('returns from Explain to the exact result the user was reading',async()=>{
  service.explain.mockResolvedValue({compiledSql:sql,upstream:[],downstream:[],sourceFreshness:[],producingRuns:[]});
  const app=testApp();
  const html=await (await app.request('/workbench',{method:'POST',body:new URLSearchParams({action:'explain',sessionId,model:'mart_visual_fixture',sql,runId})})).text();
  expect(html).toContain(`href="/workbench?runId=${runId}#wb-ticket"`);
  expect(html).toContain('Return to results');
 });

 it('preserves run links and freshness, aligns numeric columns and retires preview hints',async()=>{
  const run={id:runId,status:'succeeded',created_at:'2026-09-18T08:00:00Z',kind:'preview',input:{model:'mart_visual_fixture',sql,cycleId}};
  service.history.mockResolvedValue({runs:[run]});
  service.status.mockResolvedValue({runId,status:'succeeded',progress:100,error:null});
  service.result.mockResolvedValue({columns:[{name:'amount',type:'numeric(12,2)',nullable:true,source:null},{name:'code',type:'text',nullable:false,source:null}],rows:[{amount:null,code:'001'},{amount:'12.50',code:'002'}],compiledSql:sql,upstream:[],timingMs:1000,artifactRef:'fixture',invokeSkipped:true});
  service.explain.mockResolvedValue({compiledSql:sql,upstream:[],downstream:[],sourceFreshness:[{source_key:'fixture_accounts',last_published_at:'2026-09-18T08:00:00Z'}],producingRuns:[{id:runId},{id:cycleId}]});
  const app=testApp();
  const request=()=>app.request('/workbench?runId='+runId,{headers:{cookie:'mdp_workbench_session='+sessionId}});
  const first=await (await request()).text();
  expect(first).toContain('<details class="lineage-runs"><summary>2 runs</summary>');
  expect(first).toContain('lineage-freshness published');
  expect(first).toContain('Preview · functions skipped (fixtures)');expect(first).not.toContain('Select a column to inspect it');
  expect(first).toMatch(/<td class="numeric[^"\n]*">12.50<\/td>/);
  expect(first).toMatch(/<th class="numeric"><button[^>]*>amount/);
  expect(first).not.toContain('Showing up to 100 rows');
  service.history.mockResolvedValue({runs:[run,{...run,id:cycleId}]});
  const repeat=await (await request()).text();
  expect(repeat).not.toContain('Preview · functions skipped (fixtures)');expect(repeat).not.toContain('Select a column to inspect it');
  expect(repeat).toContain('data-overflow-hint="off"');
 });

 it('keeps failure styling with a neutral status badge and recovery',async()=>{
  service.history.mockResolvedValue({runs:[{id:runId,status:'failed',created_at:'2026-09-18T08:00:00Z',kind:'preview',input:{model:'mart_visual_fixture',sql,cycleId}}]});
  service.status.mockResolvedValue({runId,status:'failed',progress:0,error:{error_class:'build_error',message:'Cannot build draft'}});
  const app=testApp();
  const html=await (await app.request('/workbench?runId='+runId,{headers:{cookie:'mdp_workbench_session='+sessionId}})).text();
  expect(html).toContain('class="card terminal-card error"');
  expect(html).not.toContain('class="badge failed"');
  expect(html).toContain('Edit SQL and rerun');expect(html).toContain('Copy error');
 });

 it('explains the current-input boundary only for staff sessions',async()=>{
  for(const admin of [false,true]){
   const response=await testApp(admin).request('/workbench',{headers:{cookie:'mdp_workbench_session='+sessionId}});
   expect(response.status).toBe(200);
   const html=await response.text();
   expect(html.includes('Preview and Backtest use current global inputs')).toBe(!admin);
   if(!admin)expect(html).toContain('/runbooks/forbidden');
  }
 });
 it('recognizes numeric scalar types without treating numeric arrays or identifiers as quantities',()=>{
  for(const type of ['bigint','integer','numeric(12,2)','DOUBLE PRECISION','float8'])expect(isNumericType(type)).toBe(true);
  for(const type of ['text','uuid','integer[]','timestamp'])expect(isNumericType(type)).toBe(false);
  expect(changedFiles('--- a/old.sql\n+++ b/new.sql\n+x\n+++ /dev/null')).toEqual(['new.sql']);
 });

});

it.each([['query','/workbench?intent=query#wb-sql'],['models','/workbench#wb-models'],['','/workbench']])('session POST redirects with 303 for %s',async(intent,location)=>{
 service.createSession.mockResolvedValue({sessionId});
 const response=await testApp().request('/workbench',{method:'POST',body:new URLSearchParams({action:'create',intent})});
 expect(response.status).toBe(303);
 expect(response.headers.get('location')).toBe(location);
 const cookie=response.headers.get('set-cookie');
 expect(cookie).toContain('Secure');expect(cookie).toContain('HttpOnly');expect(cookie).toContain('SameSite=Lax');expect(cookie).toContain('Path=/workbench');
});
it('the starter query POST redirects with 303',async()=>{
 service.createSession.mockResolvedValue({sessionId});service.query.mockResolvedValue({runId});
 const response=await testApp().request('/workbench',{method:'POST',body:new URLSearchParams({action:'create',intent:'charts'})});
 expect(response.status).toBe(303);expect(response.headers.get('location')).toBe(`/workbench?runId=${runId}#wb-ticket`);
});
