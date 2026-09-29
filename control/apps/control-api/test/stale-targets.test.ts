import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type postgres from "postgres";
import { isolatedControl } from "./isolated-control.js";
import { pendingProbeTargets, probeActivation } from "../src/target-probe.js";
import { targetHealth } from "../src/console-data.js";
import { TargetHealth } from "../src/primitives.js";
import { parkStaleTargets } from "../src/stale-targets.js";

let db: postgres.Sql;
let close: (() => Promise<void>) | undefined;
beforeAll(async () => { if (process.env.MDP_STATUS_TEST_URL) ({ db, close } = await isolatedControl(process.env.MDP_STATUS_TEST_URL)); });
afterAll(async () => close?.());
describe.skipIf(!process.env.MDP_STATUS_TEST_URL)("stale target lifecycle",()=>{
  it("queues an inactive import without a spec for an advisory probe", async()=>{
    const rollback = new Error("rollback");
    await db.begin(async tx=>{
      const [set] = await tx`INSERT INTO control.target_set(kind,name) VALUES ('chart','Pending charts') RETURNING id`;
      const [target] = await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status)
        VALUES (${set!.id},'fixture','pending','resolved') RETURNING id`;
      await probeActivation(tx,[target!.id]);
      expect(await pendingProbeTargets(tx)).toEqual([{id:target!.id,seed:false}]);
      throw rollback;
    }).catch(error=>{if(error!==rollback)throw error;});
  });
  for (const scenario of ["local", "outage", "other-host", "cap", "weekly", "small-1", "small-3", "small-9", "manual:", "backfill:", "canary:"]) {
    it(`parks only isolated stale targets: ${scenario}`,async()=>{
      const rollback = new Error("rollback");
      await db.begin(async tx=>{
        const [set] = await tx`INSERT INTO control.target_set(kind,name) VALUES ('chart','Fixture charts') RETURNING id`;
        const size = scenario.startsWith("small-") ? Number(scenario.slice(6)) : 10;
        const targets = await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at)
          SELECT ${set!.id},'fixture',n::text,'resolved','2026-01-01' FROM generate_series(0,${size-1}) n RETURNING id`;
        // A one-member set still needs evidence that the provider works. A peer in
        // another set supplies it without adding to this set's parking capacity.
        if (size === 1) {
          const [peerSet] = await tx`INSERT INTO control.target_set(kind,name) VALUES ('account','Peer accounts') RETURNING id`;
          const [peer] = await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at)
            VALUES (${peerSet!.id},'fixture','peer','resolved','2026-01-01') RETURNING id`;
          targets.push(peer!);
        }
        const [stream] = await tx`INSERT INTO control.streamline(source_key,layer) VALUES ('fixture_stale','bronze') RETURNING id`;
        const [warehouse] = await tx`SELECT id FROM control.warehouse WHERE is_production`;
        const dead = scenario === "outage" ? 10 : scenario === "cap" ? 2 : 1;
        async function cycle(day:number,skip=false,cid?:string) {
          const cycleId=cid ?? (await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at)
            VALUES ('daily','global',${`fixture:${day}`},${`2026-01-${String(day).padStart(2,"0")}T00:00:00Z`}) RETURNING id`)[0]!.id;
          const [run] = await tx`INSERT INTO control.run(kind,work_key,cycle_id,scope,streamline_id,warehouse_id,status,resolved_config)
            VALUES ('invoke',gen_random_uuid()::text,${cycleId},'global',${stream!.id},${warehouse!.id},'partial',
            '{"target_coverage":{"stale_target_cycles":2}}') RETURNING id`;
          const completed = targets.flatMap((t,i)=>[t.id, ...(skip ? [`skipped:${t.id}`] : i<dead ? [`stale_target:${t.id}`] : [])]);
          await tx`INSERT INTO control.batch(run_id,index,target_ids,status,cursor_checkpoint)
            VALUES (${run!.id},0,${targets.map(t=>t.id)}::uuid[],'partial',${JSON.stringify({completed_targets:completed})}::text::jsonb)`;
          if (!skip) for (const [i,target] of targets.entries()) {
            await tx`INSERT INTO control.call_ledger(run_id,target_id,vendor,endpoint,request_id,http_status,duration_ms,attempt,cost_cents)
              VALUES (${run!.id},${target.id},${scenario==="other-host" && i>=dead ? "other.invalid":"fixture.invalid"},'/chart',gen_random_uuid(),${i<dead?404:200},1,1,0)`;
            if (i<dead) await tx`INSERT INTO control.dead_letter(run_id,streamline_id,target_id,reason)
              VALUES (${run!.id},${stream!.id},${target.id},'stale_target: HTTP 404')`;
          }
          return cycleId as string;
        }
        const first=await cycle(2);
        expect((await parkStaleTargets(tx)).parked).toEqual([]);
        await cycle(2,false,first);
        expect((await parkStaleTargets(tx)).parked).toEqual([]);
        if (scenario==="weekly") await cycle(3,true);
        const second = await cycle(9);
        if (scenario.endsWith(":")) {
          await tx`UPDATE control.cycle SET opened_by_dbt_run_id=${scenario + 'fixture'} WHERE id=${second}`;
          expect((await parkStaleTargets(tx)).parked).toEqual([]);
          await cycle(10);
        }
        await tx`SET LOCAL ROLE control_rt`;
        const result=await parkStaleTargets(tx);
        expect(result.parked).toHaveLength(["outage","other-host"].includes(scenario) ? 0 : 1);
        if (scenario==="outage") expect(result.warnings).toHaveLength(1);
        expect((await parkStaleTargets(tx)).parked).toEqual([]);
        if (result.parked.length) {
          const id = result.parked[0]!;
          expect(targets.slice(0,dead).map(t=>t.id)).toContain(id);
          const health = (await targetHealth(tx,[id]))[id]!;
          expect(health).toMatchObject({active:false,parked:true,resolved:true});
          const html = String(await TargetHealth({health}));
          expect(html).toContain('parked');
          await tx`RESET ROLE`;
          await tx`INSERT INTO control.runbook(slug,title,body_md) VALUES ('target-zero-yield','No output','Inspect the producing run.') ON CONFLICT DO NOTHING`;
          await tx`INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug)
            SELECT 'target_zero_yield','warning','target',${id},id,'target-zero-yield' FROM control.run ORDER BY created_at DESC LIMIT 1`;
          await tx`SET LOCAL ROLE control_rt`;
          const warned = (await targetHealth(tx,[id]))[id]!;
          const warningHtml = String(await TargetHealth({health:warned}));
          expect(warningHtml).toContain('No output');
          expect(warningHtml).toContain(`/runs/${warned.zero_yield_run}`);
          expect(warningHtml).toContain('/runbooks/target-zero-yield');
          expect(html).toContain('Reactivate');
          expect(html).toContain('/actions/activate');
          expect(html).toContain(`name="id" value="${id}"`);
          expect(html).toContain('name="active" value="true"');
          expect((await tx`SELECT count(*)::int AS n FROM control.audit_log WHERE action='targets.parkStale'`)[0]!.n).toBe(1);
          await tx`UPDATE control.target SET deactivated_at=NULL,activated_at='2026-01-10T12:00:00Z' WHERE id=${result.parked[0]!}`;
          await tx`RESET ROLE`;
          await cycle(11);
          expect((await parkStaleTargets(tx)).parked).not.toContain(result.parked[0]);
        }
        throw rollback;
      }).catch(error=>{if(error!==rollback)throw error;});
    });
  }
});
