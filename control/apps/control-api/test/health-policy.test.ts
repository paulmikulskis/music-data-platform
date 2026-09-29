import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type postgres from "postgres";
import { consoleFailures, runCoverage } from "../src/console-data.js";
import type { DB } from "../src/db.js";
import { isolatedControl } from "./isolated-control.js";

it.each([1, 3, 9, 10])("uses the default floor for %i targets and keeps a recorded override", async total => {
  const records = Array.from({length:total}, (_, i) => ({run_id:"r",id:String(i),completed:[String(i)],status:"succeeded"}));
  const db = {unsafe:async()=>records} as unknown as DB;
  expect((await runCoverage(db,["r"])).r).toMatchObject({total,succeeded:total,floor:total>=10?.9:1});
  const recorded = {unsafe:async()=>records.map(r=>({...r,floor:.75}))} as unknown as DB;
  expect((await runCoverage(recorded,["r"])).r?.floor).toBe(.75);
});

let db: postgres.Sql;
let close: (() => Promise<void>) | undefined;
beforeAll(async () => { if (process.env.MDP_STATUS_TEST_URL) ({ db, close } = await isolatedControl(process.env.MDP_STATUS_TEST_URL)); });
afterAll(async () => close?.());
describe.skipIf(!process.env.MDP_STATUS_TEST_URL)("console overdue boundaries",()=>{
  it.each([["hourly",3600],["daily",86400],["weekly",604800]] as const)("checks open and closed %s cycles", async (cadence,seconds)=>{
    const rollback = new Error("rollback");
    await db.begin(async tx=>{
      for (const status of ["open","closed"]) {
        for (const offset of [-1,0,1]) {
          await tx`DELETE FROM control.cycle`;
          const [cycle] = await tx`INSERT INTO control.cycle(cadence,scope,status,opened_by_dbt_run_id,opened_at,closed_at)
            VALUES (${cadence},'global',${status},'boundary',now()-(${seconds+offset} * interval '1 second'),
              CASE WHEN ${status}='closed' THEN now()-(${seconds+offset} * interval '1 second') END) RETURNING id`;
          const failures = await consoleFailures(tx);
          expect(failures.map(f=>f.cycle_id)).toEqual(offset===1?[cycle!.id]:[]);
        }
      }
      throw rollback;
    }).catch(error=>{if(error!==rollback)throw error;});
  });
});
