import { z } from "zod";
import { AppError, one, type DB } from "./db.js";
import { dbtCloud } from "./service.js";
export async function triggerRegisteredCloudJob(db: DB, cadence: string, scope: string, key: string) {
  const jobs = await db.unsafe("SELECT job_id FROM control.dbt_job WHERE runner='cloud' AND cadence=$1 AND scope=$2 ORDER BY job_id", [cadence, scope]);
  if (jobs.length !== 1) throw new AppError("binding_required", "Register exactly one Cloud job for this cadence and scope", 409);
  const job = z.object({ job_id: z.string() }).parse(jobs[0]);
  const result = z.object({ id: z.union([z.number(), z.string()]) }).parse(
    await dbtCloud(`jobs/${encodeURIComponent(job.job_id)}/run/`, { cause: `Control manual:${key}` }));
  return { path: "cloud" as const, dbt_run_id: String(result.id), job_id: job.job_id, status: "queued" };
}
// Run Now under Core holds the runner lock only for its bind. A Core run of the same cadence and
// scope that starts after the bind supersedes the Run Now cycle, and its export then ends superseded.
export function coreExportRefusal(status: string): AppError | null {
  if (status === "superseded")
    return new AppError(
      "run_now_superseded",
      "A Core run of this cadence and scope started after Run Now bound and superseded its cycle; press Run Now after that run",
      409,
    );
  if (status === "failed" || status === "partial")
    return new AppError("export_failed", "Target export failed; inspect the run");
  return null;
}
