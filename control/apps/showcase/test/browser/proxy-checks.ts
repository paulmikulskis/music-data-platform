import assert from 'node:assert/strict';
import type { Page, BrowserContext } from '@playwright/test';
import type postgres from 'postgres';
export async function proxyChecks(page: Page, context: BrowserContext, db: postgres.Sql, origin: string, actor: string, requests: { method: string; path: string }[]) {
  page.on('pageerror', error => console.log('Console page error:', error.message));
  // Pace navigation as a person would; the public host enforces ten requests per second.
  await page.waitForTimeout(1100); await page.goto(origin + '/tenants');
  const slug = 'browser-' + Date.now();
  await page.locator('form[action="/actions/create-tenant"] input[name=slug]').fill(slug);
  await page.locator('form[action="/actions/create-tenant"] input[name=name]').fill('Test collection');
  await page.getByRole('button', { name: 'Create tenant', exact: true }).click();
  if (!new URL(page.url()).pathname.startsWith('/tenants')) throw new Error(`Form response ${page.url()}: ${(await page.locator('body').innerText()).slice(0,300)}`);
  const [created] = await db`SELECT id FROM control.tenant WHERE slug=${slug}`; assert(created, 'Native form creates the local fixture tenant.');
  const audit = await db`SELECT actor FROM control.audit_log WHERE action='tenants.create' ORDER BY at DESC LIMIT 1`; assert.equal(audit[0]?.actor, actor);
  const [set] = await db`SELECT id FROM control.target_set WHERE tenant_id=${created.id} AND kind='artist_page'`;
  const headers = { origin, 'sec-fetch-site': 'same-origin', cookie: (await context.cookies()).map(c => `${c.name}=${c.value}`).join('; ') };
  await page.waitForTimeout(1100);
  const imported = await page.request.post(origin + '/actions/import', { headers, maxRedirects: 0, multipart: { target_set_id: set.id, dry_run: 'true', back: '/ops', file: { name: 'subject.csv', mimeType: 'text/csv', buffer: Buffer.from('artist,platform,platform_account_id,role\nTest recording,spotify,0000000000000000000001,watchlist\n') } } });
  assert.equal(imported.status(), 303); assert(imported.headers().location?.startsWith(origin));
  const imports = await db`SELECT actor FROM control.audit_log WHERE action='targets.importTargets' ORDER BY at DESC LIMIT 1`; assert.equal(imports[0]?.actor, actor);
  await page.waitForTimeout(1100); await page.goto(origin + '/functions/browser_fixture'); await page.locator('#recent-runs[data-live-run]').waitFor();
  // The first fetch has a different Accept header from document navigation. Cache that request too.
  await page.waitForResponse(response=>new URL(response.url()).pathname==='/functions/browser_fixture'&&response.request().resourceType()==='fetch');
  const polls = requests.filter(r => r.path === '/functions/browser_fixture').length; let browserPolls=0;page.on('request',request=>{if(new URL(request.url()).pathname==='/functions/browser_fixture')browserPolls++;});await page.waitForTimeout(4500);assert(browserPolls>0,'Console polling reaches the public host.');assert.equal(requests.filter(r=>r.path==='/functions/browser_fixture').length,polls,'Safe reads reuse the identity cache.');
  await page.waitForTimeout(1100);
  const previews=requests.filter(r=>r.path==='/rpc/functions/page').length;
  const read=()=>page.request.post(origin+'/rpc/functions/page',{headers,data:{json:{source_key:'browser_fixture'}}});
  const reads=await Promise.all([read(),read(),read()]);assert(reads.every(response=>response.status()===200));
  assert.equal(requests.filter(r=>r.path==='/rpc/functions/page').length,previews+1,'POST reads share one request.');
  await page.waitForTimeout(1100);assert.equal((await read()).status(),200);
  assert.equal(requests.filter(r=>r.path==='/rpc/functions/page').length,previews+1,'POST reads reuse their identity and input cache.');
  await page.waitForTimeout(1100); await page.goto(origin + '/workbench'); await page.getByRole('button',{name:'Start session',exact:true}).click(); await page.waitForURL(/\/workbench/);
  assert(!(await context.cookies()).some(c=>c.name==='mdp_workbench_session'), 'Workbench cookies remain server-side.');
  const wbCookies = { ...headers, cookie: (await context.cookies()).map(c=>`${c.name}=${c.value}`).join('; ') };
  for (const action of ['query','preview']) {
    await page.waitForTimeout(1100);
    const result = await page.request.post(origin+'/workbench',{headers:wbCookies,form:{action,sessionId:'00000000-0000-4000-8000-000000000201',model:'mart_chart_history',sql:'select 1 as fixture',cycleA:'00000000-0000-4000-8000-000000000041'}});
    assert.equal(result.status(),200); assert((await result.text()).includes('Back to showcase'));
  }
  const operations = await db`SELECT action,actor FROM control.audit_log WHERE action IN ('workbench.query','workbench.previewModel')`; assert(operations.some(r=>r.action==='workbench.query'&&r.actor===actor)); assert(operations.some(r=>r.action==='workbench.previewModel'&&r.actor===actor));
  await page.waitForTimeout(1100);
  const csv = await page.request.get(origin+'/api/browser-download',{headers:wbCookies}); assert.equal(csv.status(),200); assert.equal(csv.headers()['content-type'],'text/csv'); assert.equal(await csv.text(),'fixture\n1\n');
}
