import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type postgres from "postgres";
import { createRouterClient } from "@orpc/server";
import { isolatedControl } from "./isolated-control.js";
const active = vi.hoisted(()=>({db:undefined as unknown}));
vi.mock("../src/db.js",async()=>({...await vi.importActual("../src/db.js"),database:()=>active.db}));
import { app } from "../src/app.js";
import { router } from "../src/router.js";

let db: postgres.Sql;
let close: (()=>Promise<void>) | undefined;
beforeAll(async()=>{
 if(process.env.MDP_STATUS_TEST_URL) ({db,close}=await isolatedControl(process.env.MDP_STATUS_TEST_URL));
});
afterAll(async()=>{vi.unstubAllEnvs();await close?.();});
describe.skipIf(!process.env.MDP_STATUS_TEST_URL)("/screen/weekly scheduled freshness",()=>{
 it("keeps the last scheduled delivery after legacy canary runs and new probe audits",async()=>{
  vi.stubEnv("MDP_AUTH_MODE","dev");vi.stubEnv("CLERK_SECRET_KEY","");
  const rollback=new Error("rollback");
  await db.begin(async tx=>{
   active.db=tx;
   const [source]=await tx`INSERT INTO control.streamline(source_key,layer,writes,cadence_tag,enabled)
    VALUES ('weekly_fixture','bronze',ARRAY['raw.fixture'],'weekly',true) RETURNING id`;
   const [cycle]=await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at)
    VALUES ('weekly','global','core:scheduled','closed',now()-interval '16 days') RETURNING id`;
   await tx`INSERT INTO control.run(kind,scope,work_key,streamline_id,warehouse_id,cycle_id,status,created_at)
    SELECT 'invoke','global','scheduled',${source!.id},id,${cycle!.id},'succeeded',now()-interval '16 days'
    FROM control.warehouse WHERE is_production`;
   const client=createRouterClient(router,{context:{identity:{actor:"fixture",admin:true,tenant_id:null,tenant_slug:null},db:tx}});
   const before=(await client.screen.weekly({})).freshness_by_cadence;
   expect(before).toMatchObject([{cadence:"weekly",stale:true}]);
   for(const opener of ['manual:canary:probe','manual:operator','backfill:fixture','canary:fixture']) {
    const [probe]=await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at)
     VALUES ('weekly','global',${opener},'closed',now()) RETURNING id`;
    for(const count of [0,1]) await tx`INSERT INTO control.run(kind,scope,work_key,streamline_id,warehouse_id,cycle_id,status,rows_written)
     SELECT 'invoke','global',${opener+count},${source!.id},id,${probe!.id},'succeeded',${count}
     FROM control.warehouse WHERE is_production`;
   }
   await tx`INSERT INTO control.audit_log(actor,action,subject,after) VALUES
    ('deploy','source.canary','weekly_fixture:global','{"status":"passed","records_validated":1}')`;
   expect((await client.screen.weekly({})).freshness_by_cadence).toEqual(before);
   const response=await app.request('/screen/weekly');
   expect(response.status).toBe(200);
   const html=await response.text();
   expect(html).toContain('Cadence health');
   expect(html).toContain('stale');
   expect(html).toContain(before[0]!.last_at!.slice(0,10));
   throw rollback;
  }).catch(error=>{if(error!==rollback)throw error;});
 });
});
