import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { empty, jsonRow, get, post } from "./shared.js";
import { admission } from "./functions-runs.js";

// A one-off core-runner machine the Retry or Replay action started.
export const coreLaunchDto = z.object({
  launcher: z.enum(["fly", "record"]),
  machine_id: z.string().nullable(),
  cadence: z.enum(["hourly", "daily", "weekly"]),
  scope: z.string(),
  command: z.array(z.string()),
  env: z.record(z.string(), z.string()),
});

export const jobDto = s.dbt_job.pick({
  job_id: true,
  runner: true,
  cadence: true,
  scope: true,
});

export const dbtContract = {
    jobs: {
      list: get("/dbt/jobs", empty, z.array(jsonRow)),
      upsert: post(
        "/dbt/jobs/upsert",
        jobDto.extend({ config: jsonRow.optional() }),
        jsonRow,
      ),
      trigger: post(
        "/dbt/jobs/trigger",
        z.object({ job_id: z.string().min(1) }),
        jsonRow,
      ),
    },
    runs: { list: get("/dbt/runs", empty, z.array(jsonRow)) },
    // Under Core, Retry reruns a cycle's (cadence, scope) and binds to its newest scheduled cycle;
    // Replay rebuilds a closed global cycle. Tenant Replay is not implemented.
    retry: post("/dbt/retry", z.object({ cycle_id: z.uuid() }), coreLaunchDto),
    replay: post("/dbt/replay", z.object({ cycle_id: z.uuid() }), coreLaunchDto),
    webhook: post(
      "/dbt/webhook",
      z.object({
        event_id: z.string(),
        run_id: z.string(),
        job_id: z.string(),
        status: z.enum(["running", "succeeded", "failed"]),
        message: z.string().default(""),
      }),
      admission,
    ),
    runnerMode: {
      get: get(
        "/dbt/runner",
        empty,
        z.object({ runner: z.enum(["core", "cloud"]) }),
      ),
      set: post(
        "/dbt/runner",
        z.object({ runner: z.enum(["core", "cloud"]) }),
        z.object({ runner: z.enum(["core", "cloud"]) }),
      ),
    },
  };
