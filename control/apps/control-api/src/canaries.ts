import { canaryTimeoutMs, canaryHttpTimeoutMs } from "./health-policy.generated.js";
import { z } from "zod";
import { functionPage, errorHint, dryProbeResult } from "@mdp/contracts";
import { rows, database, type DB } from "./db.js";
import { service, serviceDeadline } from "./service.js";
import type { Context } from "./router.shared.js";

type Outcome = "passed" | "failed" | "not_due" | "skipped" | "timed_out";
type Result = {source_key: string; status: Outcome; error_class?: string | undefined; next_step: string};

function errorClass(error: unknown) {
  const parsed = z.object({error_class:z.string()}).safeParse(error);
  return parsed.success && /^[a-z][a-z0-9_]*$/.test(parsed.data.error_class) ? parsed.data.error_class : "canary_check_failed";
}
function warn(error: unknown) {
  const code = errorClass(error);
  // Exception messages may contain response bodies, SQL values or credentials. Log catalog copy only.
  console.warn(`Canary check unavailable (${code}): ${errorHint(code).summary}; see /runbooks/partial-coverage`);
}

export async function canaries(context: Context) {
  const results: Result[] = [];
  const deadline = Date.now() + 120000;
  await serviceDeadline.run(deadline, async () => {
    try {
      context = {...context, db: database()};
      const sources = await rows(context.db, z.object({source_key:z.string(),tenant_bound:z.boolean()}),
        "SELECT source_key,tenant_bound FROM control.streamline WHERE enabled ORDER BY source_key");
      for (const source of sources) {
        let hash: string | undefined;
        try {
          if (Date.now() + canaryHttpTimeoutMs > deadline) {
            const skipped: Result = {source_key:source.source_key,status:"skipped",next_step:`Check /functions/${source.source_key}; rerun canaries for sources beyond the check deadline`};
            results.push(skipped);
            await record(context.db, source.source_key, source.source_key, "global", undefined, skipped);
            continue;
          }
          const page = await service(`/v1/functions/${source.source_key}?metadata_only=true`, functionPage);
          if (page.manifest.kind !== "invoke") continue;
          hash = String(page.manifest.code_fingerprint);
          const scopes = source.tenant_bound ? await rows(context.db, z.object({scope:z.string()}),
            "SELECT 'tenant:'||id::text AS scope FROM control.tenant WHERE status='active' ORDER BY id LIMIT 1") : [{scope:"global"}];
          if (!scopes.length) {
            const skipped: Result = {source_key:source.source_key,status:"skipped",next_step:"Add an active tenant at /tenants"};
            results.push(skipped);
            await record(context.db, source.source_key, source.source_key, "global", hash, skipped);
          }
          for (const {scope} of scopes) {
            await checkSource(context, source.source_key, scope, hash, deadline, results);
          }
        } catch (error) {
          const code = errorClass(error);
          const result: Result = {source_key:source.source_key,status:["invoke_timeout", "deadline_expired"].includes(code) ? "timed_out" : "failed",error_class:code,next_step:`Open /functions/${source.source_key}`};
          results.push(result);
          await record(context.db, source.source_key, source.source_key, "global", hash, result);
        }
      }
    } catch (error) { warn(error); }
  });
  return {results};
}

// Shared by the deploy sweep and the caller-audited console command.
export async function dryProbe(source: string, scope: string, deadline = Date.now() + canaryHttpTimeoutMs): Promise<z.infer<typeof dryProbeResult>> {
  const probeDeadline = Date.now() + canaryTimeoutMs;
  try {
    return await serviceDeadline.run(deadline, () => service(
      `/v1/functions/${source}/probe?scope=${encodeURIComponent(scope)}`, dryProbeResult, {}, undefined, {timeoutMs:canaryHttpTimeoutMs},
    ));
  } catch (error) {
    const code = errorClass(error);
    return { status: ["invoke_timeout", "deadline_expired"].includes(code) || Date.now() >= probeDeadline ? "timed_out" : "failed",
      error_class: code, records_validated: 0, next_step: `Open /functions/${source}` };
  }
}

async function checkSource(context: Context, source: string, scope: string, hash: string, deadline: number, results: Result[]) {
  const subject = `${source}:${scope}`;
  let validated = 0;
  let result: Result = {source_key:subject,status:"failed",error_class:"canary_check_failed",next_step:`Open /functions/${source}`};
  const started = Date.now();
  if (started + canaryHttpTimeoutMs > deadline) {
    result = {source_key:subject,status:"skipped",next_step:`Check /functions/${source}; rerun canaries for sources beyond the check deadline`};
    results.push(result);
    await record(context.db, subject, source, scope, hash, result);
    return;
  }
  const probe = await dryProbe(source, scope, Math.min(deadline, started + canaryHttpTimeoutMs));
  validated = probe.records_validated;
  result = {source_key:subject,status:probe.status,error_class:probe.error_class ?? undefined,next_step:probe.next_step};
  results.push(result);
  await record(context.db, subject, source, scope, hash, result, validated);
}

async function record(db: DB, subject: string, source: string, scope: string, hash: string | undefined, result: Result, validated = 0) {
  if (result.status === "failed" || result.status === "timed_out") {
    try { await serviceDeadline.run(Date.now()+2000,()=>service("/v1/alerts/canary_failed",z.unknown(),{source_key:source,scope,run_id:null})); }
    catch (error) { warn(error); }
  }
  try {
    await db.unsafe("INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('deploy','source.canary',$1,$2::text::jsonb)",
      [subject,JSON.stringify({code_fingerprint:hash,...result,records_validated:validated})]);
  } catch (error) { warn(error); }
}
