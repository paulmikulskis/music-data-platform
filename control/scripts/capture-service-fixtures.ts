import { mkdirSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { database, one } from "../apps/control-api/src/db.js";
const dir = new URL("../packages/contracts/test/fixtures/",import.meta.url);
mkdirSync(dir,{recursive:true});
async function record(path: string, url: string, method="get", request?: unknown) {
  const response=await fetch(new URL(url,process.env.MDP_SERVICE_URL),{method:method.toUpperCase(),headers:{authorization:`Bearer ${process.env.MDP_SERVICE_TOKEN}`,"content-type":"application/json","idempotency-key":`manual:conformance-${randomUUID()}`},...(request === undefined ? {} : {body:JSON.stringify(request)})});
  const value: unknown=await response.json();
  const name=path.replaceAll(/[^a-zA-Z0-9]+/g,"_")+"_"+method+".json";
  writeFileSync(new URL(name,dir),JSON.stringify({path,url,method,status:response.status,...(request === undefined?{}:{request}),response:value},null,2)+"\n");
  return value;
}
await record("/v1/functions/{source_key}","/v1/functions/fixture_accounts");
const runs=z.array(z.object({id:z.uuid(),kind:z.string()})).parse(await record("/v1/runs","/v1/runs?source_key=fixture_accounts&limit=3"));
const run=runs.find(r=>r.kind === "invoke");if(!run)throw new Error("Capture requires a completed synthetic Fixture accounts invocation");
await record("/v1/runs/{run_id}",`/v1/runs/${run.id}`);
await record("/v1/health/detail","/v1/health/detail");
await record("/v1/backfill","/v1/backfill","post",{source_key:"fixture_accounts"});
const db=database();
try {
  const binding=await one(db,z.object({dbt_run_id:z.string(),job_id:z.string(),cadence:z.string(),scope:z.string(),runner:z.string()}),"SELECT a.dbt_run_id,a.job_id,c.cadence,c.scope,a.runner FROM control.cycle_attempt a JOIN control.cycle c ON c.id=a.cycle_id WHERE c.cadence='hourly' AND c.scope='global' AND a.runner='core' ORDER BY c.opened_at DESC LIMIT 1");
  await record("/v1/bind_cycle","/v1/bind_cycle","post",{...binding,reason_category:"scheduled"});
  const admitted=z.object({run_id:z.uuid()}).parse(await record("/v1/functions/{source_key}/run",`/v1/functions/fixture_accounts/run?dbt_run_id=${encodeURIComponent(binding.dbt_run_id)}`,"post",{}));
  for(let i=0;i<100;i++) {
    const response=await fetch(new URL(`/v1/runs/${admitted.run_id}`,process.env.MDP_SERVICE_URL),{headers:{authorization:`Bearer ${process.env.MDP_SERVICE_TOKEN}`}});
    const state=z.object({run:z.object({status:z.string()})}).parse(await response.json());
    if(["succeeded","partial","failed","superseded"].includes(state.run.status))break;
    await new Promise(r=>setTimeout(r,200));
  }
  await record("/v1/runs/{run_id}/cancel",`/v1/runs/${admitted.run_id}/cancel`,"post",{});
  await record("/v1/invoke","/v1/invoke","post",{source_key:"targets_export",cadence:binding.cadence,dbt_run_id:binding.dbt_run_id,manual:true});
  const dump=await one(db,z.object({id:z.uuid(),target_table:z.string()}),"SELECT d.id,l.target_table FROM control.dump d JOIN control.load l ON l.dump_id=d.id WHERE d.kind='output' AND l.status='loaded' AND l.target_table='raw.account_snapshots' ORDER BY d.created_at DESC LIMIT 1");
  await record("/v1/repair","/v1/repair","post",{dump_id:dump.id,target_table:dump.target_table});
  await record("/v1/registry/sync","/v1/registry/sync","post",{});
  await record("/v1/dbt/webhook","/v1/dbt/webhook","post",{event_id:randomUUID(),run_id:`conformance-${randomUUID()}`,job_id:binding.job_id,status:"succeeded",message:"Synthetic conformance capture"});
} finally {await db.end();}
console.log("Captured service responses for all consumed routes; no authentication material written.");
