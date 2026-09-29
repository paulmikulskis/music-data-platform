import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type postgres from "postgres";
import { isolatedControl } from "./isolated-control.js";
import { consoleFailures, runCoverage } from "../src/console-data.js";
const url = process.env.MDP_STATUS_TEST_URL;
let db: postgres.Sql;
let close: (() => Promise<void>) | undefined;
beforeAll(async () => { if (url) ({ db, close } = await isolatedControl(url)); });
afterAll(async () => close?.());
describe.skipIf(!url)("coverage in the console", () => {
  it("shows 58 of 59 above the floor without a failed-cycle banner", async () => {
    const rollback = new Error("rollback");
    await db.begin(async tx => {
      const [stream] = await tx`INSERT INTO control.streamline(source_key,layer,allow_partial) VALUES ('fixture_coverage','bronze',false) RETURNING id`;
      const [cycle] = await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id) VALUES ('daily','global','fixture:coverage') RETURNING id`;
      const [set] = await tx`INSERT INTO control.target_set(kind,name) VALUES ('chart','Coverage fixture') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id`;
      const targets = await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status) SELECT ${set!.id},'coverage',n::text,'resolved' FROM generate_series(1,59) n RETURNING id`;
      const ids = targets.map(t => String(t.id));
      const [run] = await tx`INSERT INTO control.run(kind,work_key,cycle_id,scope,streamline_id,warehouse_id,status,coverage,error_class,resolved_config)
        SELECT 'invoke','fixture:coverage',${cycle!.id},'global',${stream!.id},id,'partial','partial','stale_target','{"target_coverage":{"min_target_coverage":0.9}}'
        FROM control.warehouse WHERE is_production RETURNING id`;
      await tx.unsafe("INSERT INTO control.batch(run_id,index,target_ids,status,cursor_checkpoint) VALUES ($1,0,$2::uuid[],'partial',$3::text::jsonb)",
        [run!.id, ids, JSON.stringify({ completed_targets: [...ids, `stale_target:${ids[0]}`] })]);
      const coverage = (await runCoverage(tx, [run!.id]))[run!.id];
      expect(coverage).toMatchObject({ succeeded: 58, total: 59, floor: 0.9 });
      expect((await consoleFailures(tx)).some(f => f.cycle_id === cycle!.id)).toBe(false);
      await tx`UPDATE control.run SET status='failed',error_class='partial_coverage' WHERE id=${run!.id}`;
      expect((await consoleFailures(tx)).some(f => f.cycle_id === cycle!.id)).toBe(true);
      throw rollback;
    }).catch(error => { if (error !== rollback) throw error; });
  });
  it.each([0,10])("counts a completed target with %s expected exclusions",async excluded=>{
    const rollback=new Error("rollback");
    await db.begin(async tx=>{
      const [stream]=await tx`INSERT INTO control.streamline(source_key,layer) VALUES ('fixture_exclusions','bronze') RETURNING id`;
      const [set]=await tx`INSERT INTO control.target_set(kind,name) VALUES ('chart','Exclusion fixture') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id`;
      const [target]=await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status) VALUES (${set!.id},'fixture','excluded','resolved') RETURNING id`;
      const [run]=await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status) SELECT 'invoke','fixture:exclusions','global',${stream!.id},id,'succeeded' FROM control.warehouse WHERE is_production RETURNING id`;
      const [batch]=await tx.unsafe("INSERT INTO control.batch(run_id,index,target_ids,status,cursor_checkpoint) VALUES ($1,0,$2::uuid[],'succeeded',$3::text::jsonb) RETURNING id",[run!.id,[target!.id],JSON.stringify({completed_targets:[target!.id]})]);
      await tx.unsafe("INSERT INTO control.run_event(run_id,event_type,level,message,attrs) VALUES ($1,'page_published','info','Fixture',$2::text::jsonb)",[run!.id,JSON.stringify({batch_id:batch!.id,observed:10,yielded:0,rejected:10,exclusions:excluded ? {'fixture:outside_scope':excluded} : {}})]);
      const coverage=(await runCoverage(tx,[run!.id]))[run!.id];
      expect(coverage).toMatchObject({succeeded:excluded ? 1 : 0,total:1});
      throw rollback;
    }).catch(error=>{if(error!==rollback)throw error;});
  });

});
