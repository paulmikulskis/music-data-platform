import { z } from "zod";
import { jsonRow, admission } from "@mdp/contracts";
import { one, rows, AppError, type DB } from "./db.js";
import { scheduledCycleSql } from "./health-policy.generated.js";
import { service, dbtCloud } from "./service.js";
import { launchCore } from "./core-launcher.js";
import { impl } from "./router.shared.js";

// The cycle a Retry or Replay acts on, with its tenant slug; both run only under the Core runner.
async function coreCycle(db: DB, cycleId: string) {
  const mode = await one(db, z.object({ runner: z.enum(["core", "cloud"]) }), "SELECT runner FROM control.runner_mode WHERE id");
  if (mode.runner !== "core") throw new AppError("runner_inactive", "Retry and Replay machines run under the Core runner", 409);
  return one(
    db,
    z.object({ cadence: z.enum(["hourly", "daily", "weekly"]), scope: z.string(), status: z.string(), slug: z.string().nullable() }),
    "SELECT c.cadence,c.scope,c.status::text AS status,t.slug FROM control.cycle c LEFT JOIN control.tenant t ON c.scope='tenant:'||t.id::text WHERE c.id=$1",
    [cycleId],
  );
}

async function supersededRefusal(db: DB, cycleId: string, cycle: { cadence: string; scope: string }) {
  const [current] = await rows(
    db,
    z.object({ id: z.string(), status: z.string() }),
    `SELECT id::text AS id,status::text AS status FROM control.cycle WHERE cadence=$1 AND scope=$2 AND status<>'superseded'
      AND ${scheduledCycleSql()} ORDER BY opened_at DESC LIMIT 1`,
    [cycle.cadence, cycle.scope],
  );
  const next = !current
    ? "No current cycle exists yet; the next scheduled build opens one."
    : current.status === "open"
      ? `Retry the current cycle ${current.id} instead.`
      : `The current cycle ${current.id} is closed; its next successful build resolves the alert.`;
  return new AppError("cycle_superseded", `Cycle ${cycleId} was superseded. ${next}`, 409);
}

export const dbtRouter = {
    jobs: {
      list: impl.dbt.jobs.list.handler(async () =>
        z.array(jsonRow).parse(await dbtCloud("jobs/")),
      ),
      upsert: impl.dbt.jobs.upsert.handler(async ({ context, input }) => {
        const result = jsonRow.parse(
          await dbtCloud(
            `jobs/${encodeURIComponent(input.job_id)}/`,
            input.config ?? {},
          ),
        );
        await context.db.unsafe(
          "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ($1,$2,$3,$4) ON CONFLICT(job_id) DO UPDATE SET runner=EXCLUDED.runner,cadence=EXCLUDED.cadence,scope=EXCLUDED.scope",
          [input.job_id, input.runner, input.cadence, input.scope],
        );
        return result;
      }),
      trigger: impl.dbt.jobs.trigger.handler(async ({ input }) =>
        jsonRow.parse(
          await dbtCloud(`jobs/${encodeURIComponent(input.job_id)}/run/`, {
            cause: "Manual control-plane run",
          }),
        ),
      ),
    },
    runs: {
      list: impl.dbt.runs.list.handler(async () =>
        z.array(jsonRow).parse(await dbtCloud("runs/")),
      ),
    },
    retry: impl.dbt.retry.handler(async ({ context, input }) => {
      const cycle = await coreCycle(context.db, input.cycle_id);
      // Retry rebuilds the newest non-superseded scheduled cycle, never the one asked for, so a
      // superseded input would rebuild a different cycle without saying so.
      if (cycle.status === "superseded") throw await supersededRefusal(context.db, input.cycle_id, cycle);
      return launchCore(cycle.cadence, cycle.scope, cycle.slug, ["--reason-category", "other"]);
    }),
    replay: impl.dbt.replay.handler(async ({ context, input }) => {
      const cycle = await coreCycle(context.db, input.cycle_id);
      // Tenant Replay is not implemented; tenant builds use Retry.
      if (cycle.scope !== "global")
        throw new AppError("replay_unavailable", "Tenant Replay is unavailable; use Retry for the current build", 409);
      if (cycle.status !== "closed") throw new AppError("replay_refused", "Replay requires a closed cycle", 409);
      return launchCore(cycle.cadence, cycle.scope, null, ["--cycle-id", input.cycle_id]);
    }),
    webhook: impl.dbt.webhook.handler(({ input }) =>
      service("/v1/dbt/webhook", admission, input),
    ),
    runnerMode: {
      get: impl.dbt.runnerMode.get.handler(({ context }) =>
        one(
          context.db,
          z.object({ runner: z.enum(["core", "cloud"]) }),
          "SELECT runner FROM control.runner_mode WHERE id",
        ),
      ),
      set: impl.dbt.runnerMode.set.handler(({ context, input }) =>
        one(
          context.db,
          z.object({ runner: z.enum(["core", "cloud"]) }),
          "UPDATE control.runner_mode SET runner=$1 WHERE id RETURNING runner",
          [input.runner],
        ),
      ),
    },
  };
