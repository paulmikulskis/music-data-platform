import { canaries, dryProbe } from "./canaries.js";
import { rerunProcedure } from "./workbench-router.js";
import { z } from "zod";
import { streamlineDto, runDto, eventDto, llmStepDto, jsonRow, admission, poll, functionPage } from "@mdp/contracts";
import { rows, one, AppError, type DB } from "./db.js";
import { service } from "./service.js";
import { coreExportRefusal, triggerRegisteredCloudJob } from "./manual.js";
import { cadencePayload } from "./cadence.js";
import { type Context, impl, patch } from "./router.shared.js";

// The inputs a gold streamline parked at its latest read (derived.parked_inputs) while an
// inputs_parked alert for it is open; resolving that alert (streamlines.unpark) releases them. The
// event read runs only for a streamline with an open alert, over events since it opened (the
// opening read's input_snapshot shares the alert's transaction time).
const withParked = `SELECT s.*,coalesce((SELECT (SELECT (e.attrs->>'parked')::int FROM control.run r JOIN control.run_event e ON e.run_id=r.id
      WHERE r.streamline_id=s.id AND e.at >= p.opened_at AND e.event_type='input_snapshot'
      ORDER BY e.at DESC LIMIT 1)
    FROM (SELECT min(a.opened_at) AS opened_at FROM control.alert a JOIN control.run ar ON ar.id=a.run_id
      WHERE ar.streamline_id=s.id AND a.class='inputs_parked' AND a.resolved_at IS NULL) p
    WHERE p.opened_at IS NOT NULL),0) AS parked_inputs
  FROM control.streamline s`;

const getStream = (db: DB, key: string) =>
  one(
    db,
    streamlineDto,
    `${withParked} WHERE s.source_key=$1`,
    [key],
  );

async function manualRun(
  input: {
    source_key: string;
    key: string;
    scope: string;
    dbt_run_id?: string | undefined;
    fixture_scenario?: string | undefined;
  },
  context: Context,
) {
  if (input.fixture_scenario) {
    if (process.env.MDP_AUTH_MODE !== "dev")
      throw new AppError("forbidden", "Fixture scenarios require development authentication", 403);
    await service("/v1/_fixture/state", z.unknown());
  }
  const s = await getStream(context.db, input.source_key);
  if (s.tenant_bound !== input.scope.startsWith("tenant:"))
    throw new AppError(
      "scope_mismatch",
      "Use a scope matching the function declaration",
    );
  let binding = input.dbt_run_id;
  if (!binding) {
    const mode = await one(
      context.db,
      z.object({ runner: z.enum(["core", "cloud"]) }),
      "SELECT runner FROM control.runner_mode WHERE id",
    );
    if (mode.runner === "cloud") {
      if (input.fixture_scenario) throw new AppError("fixture_unavailable", "Fixture scenarios require Core mode", 409);
      return triggerRegisteredCloudJob(context.db, s.cadence_tag ?? "daily", input.scope, input.key);
    }
    const job = await one(
      context.db,
      z.object({ job_id: z.string() }),
      "SELECT job_id FROM control.dbt_job WHERE runner='core' AND cadence=$1 AND scope=$2 ORDER BY job_id LIMIT 1",
      [s.cadence_tag ?? "daily", input.scope],
    );
    binding = `manual:${input.key}`;
    // The bind refuses while a Core run of the pair holds its runner lock (core_run_in_progress).
    // It holds the lock only for the bind: a Core run that starts later supersedes this cycle.
    await service("/v1/bind_cycle", jsonRow, {
      runner: "core",
      cadence: s.cadence_tag,
      scope: input.scope,
      dbt_run_id: binding,
      reason_category: "scheduled",
      job_id: job.job_id,
      lock_runner: true,
    });
    const exportRun = await service(
      "/v1/invoke",
      admission,
      {
        source_key: "targets_export",
        cadence: s.cadence_tag,
        dbt_run_id: binding,
        manual: true,
      },
      `manual:export:${input.key}`,
    );
    for (let i = 0; i < 100; i++) {
      const state = await service(`/v1/runs/${exportRun.run_id}`, poll);
      if (state.run.status === "succeeded") break;
      const refusal = coreExportRefusal(state.run.status);
      if (refusal) throw refusal;
      if (i === 99)
        throw new AppError(
          "invoke_timeout",
          "Target export did not finish",
          503,
        );
      await new Promise((r) => setTimeout(r, 100));
    }
  }
  const result = await service(
    `/v1/functions/${input.source_key}/run?dbt_run_id=${encodeURIComponent(binding)}${input.fixture_scenario ? `&fixture_scenario=${encodeURIComponent(input.fixture_scenario)}` : ""}`,
    admission,
    {},
    `manual:${input.key}`,
  );
  return { ...result, path: "core" as const };
}

export const streamlinesRouter = {
    canaries: impl.streamlines.canaries.handler(({ context }) => canaries(context)),
    list: impl.streamlines.list.handler(({ context }) =>
      rows(
        context.db,
        streamlineDto,
        `${withParked} ORDER BY s.source_key`,
      ),
    ),
    get: impl.streamlines.get.handler(({ context, input }) =>
      getStream(context.db, input.source_key),
    ),
    unpark: impl.streamlines.unpark.handler(async ({ context, input }) => {
      const stream = await getStream(context.db, input.source_key);
      const released = await rows(context.db, z.object({ id: z.uuid() }),
        `UPDATE control.alert a SET resolved_at=now() FROM control.run r
         WHERE r.id=a.run_id AND r.streamline_id=$1 AND a.class='inputs_parked' AND a.resolved_at IS NULL RETURNING a.id`,
        [stream.id]);
      return { source_key: input.source_key, released_alerts: released.length };
    }),
    patchKnobs: impl.streamlines.patchKnobs.handler(({ context, input }) => {
      const { source_key, ...knobs } = input;
      if (knobs.storage === "iceberg")
        throw new AppError(
          "storage_unavailable",
          "Iceberg awaits the restore acceptance gate",
          409,
        );
      return patch(
        context.db,
        streamlineDto,
        "streamline",
        "source_key",
        source_key,
        knobs,
      );
    }),
    runNow: impl.streamlines.runNow.handler(({ context, input }) =>
      manualRun(input, context),
    ),
    dryProbe: impl.streamlines.dryProbe.handler(({ context, input }) => {
      if (!context.identity.admin)
        throw new AppError("forbidden", "Probe needs the admin role. Ask an operator to run it.", 403);
      return dryProbe(input.source_key, input.scope);
    }),
    probe: impl.streamlines.probe.handler(({ context, input }) =>
      manualRun(input, context),
    ),
    acknowledgeDrift: impl.streamlines.acknowledgeDrift.handler(
      async ({ context, input }) => {
        const page = await service(
          `/v1/functions/${input.source_key}`,
          functionPage,
        );
        if (
          !page.fingerprint_history.some(
            (h) => h.fingerprint === input.fingerprint,
          )
        )
          throw new AppError(
            "unknown_fingerprint",
            "Fingerprint is not in this function history",
          );
        await context.db.unsafe(
          "UPDATE control.alert SET acknowledged_by=$2 WHERE class='schema_drift' AND run_id IN (SELECT r.id FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id WHERE s.source_key=$1)",
          [input.source_key, context.identity.actor],
        );
        return one(
          context.db,
          streamlineDto,
          "UPDATE control.streamline SET acknowledged_fingerprints=array(SELECT DISTINCT unnest(acknowledged_fingerprints || $2::text[])) WHERE source_key=$1 RETURNING *",
          [input.source_key, page.fingerprint_history.filter(h=>h.fingerprint===input.fingerprint).flatMap(h=>
            (Array.isArray(h.tables) ? h.tables : [h.table]).filter((t):t is string=>typeof t==="string").map(t=>`${input.source_key}:${t}:${input.fingerprint}`))],
        );
      },
    ),
    repair: impl.streamlines.repair.handler(({ input }) =>
      service("/v1/repair", jsonRow, input),
    ),
    backfill: impl.streamlines.backfill.handler(({ input }) =>
      service("/v1/backfill", admission, input),
    ),
    migrate: impl.streamlines.migrate.handler(({ input }) =>
      service("/v1/migrate", jsonRow, input),
    ),
    requestCadenceChange: impl.streamlines.requestCadenceChange.handler(
      async ({ context, input }) => {
        await getStream(context.db, input.source_key);
        return cadencePayload(input.source_key, input.cadence);
      },
    ),
    resetCursor: impl.streamlines.resetCursor.handler(({ context, input }) =>
      one(
        context.db,
        z.object({ reset_generation: z.string() }),
        "UPDATE control.cursor SET reset_generation=reset_generation+1 WHERE streamline_id=(SELECT id FROM control.streamline WHERE source_key=$1) AND target_id IS NOT DISTINCT FROM $2::uuid AND cursor_key=$3 RETURNING reset_generation::text",
        [input.source_key, input.target_id, input.cursor_key],
      ),
    ),
  };

export const runsRouter = {
    list: impl.runs.list.handler(({ context, input }) =>
      rows(
        context.db,
        runDto,
        "SELECT r.* FROM control.run r LEFT JOIN control.streamline s ON s.id=r.streamline_id WHERE ($1::text IS NULL OR s.source_key=$1) AND ($2::text IS NULL OR r.status::text=$2) ORDER BY r.created_at DESC LIMIT 100",
        [input.source_key ?? null, input.status ?? null],
      ),
    ),
    get: impl.runs.get.handler(async ({ context, input }) => {
      const run = await one(
        context.db,
        runDto,
        "SELECT * FROM control.run WHERE id=$1",
        [input.id],
      );
      return run.kind === "dbt"
        ? { run, receipts: [], repairs_pending: 0 }
        : service(`/v1/runs/${input.id}`, poll);
    }),
    events: impl.runs.events.handler(({ context, input }) =>
      rows(
        context.db,
        eventDto,
        "SELECT * FROM control.run_event WHERE run_id=$1 ORDER BY at LIMIT 1000",
        [input.id],
      ),
    ),
    tree: impl.runs.tree.handler(({ context, input }) =>
      rows(
        context.db,
        runDto,
        "WITH RECURSIVE tree AS (SELECT * FROM control.run WHERE id=$1 UNION ALL SELECT r.* FROM control.run r JOIN tree t ON r.parent_run_id=t.id) SELECT * FROM tree LIMIT 1000",
        [input.id],
      ),
    ),
    cancel: impl.runs.cancel.handler(({ input }) =>
      service(`/v1/runs/${input.id}/cancel`, poll, {}),
    ),
  };

export const functionsRouter = {
    page: impl.functions.page.handler(async ({ input, context }) => {
      if (!context.identity.admin && input.preview_table)
        throw new AppError("forbidden", "Output previews need the admin role. Ask an operator, or query global staging in Workbench.", 403);
      const metadataOnly = !context.identity.admin || input.metadata_only;
      // Built-in control functions do not expose warehouse preview relations.
      if (["targets_export", "cycle_close"].includes(input.source_key)) {
        const manifest = await getStream(context.db, input.source_key);
        const last_runs = await rows(context.db, runDto, "SELECT * FROM control.run WHERE streamline_id=$1 ORDER BY created_at DESC LIMIT 20", [manifest.id]);
        const receipts = metadataOnly ? [] : await Promise.all(last_runs.map(r => service(`/v1/runs/${r.id}`,poll).then(v=>v.receipts)));
        return functionPage.parse({manifest,last_runs,receipts,output_preview:[],rejected_sample:[],fingerprint_history:[],row_counts:[...last_runs].reverse().map(r=>({at:r.created_at,rows:r.rows_written})),log_url:`/functions/${input.source_key}/logs`});
      }
      const query = new URLSearchParams();
      if (metadataOnly) {
        query.set("metadata_only", "true");
      } else {
        if (input.preview_table) query.set("preview_table", input.preview_table);
        if (input.preview_cursor) query.set("preview_cursor", input.preview_cursor);
      }
      const page = await service(`/v1/functions/${input.source_key}?${query}`, functionPage);
      return metadataOnly ? {...page, receipts:[], output_preview:[], rejected_sample:[]} : page;
    }),
    register: impl.functions.register.handler(() =>
      service(
        "/v1/registry/sync",
        z.object({ registered: z.number().int() }),
        {},
      ),
    ),
  };

export const llmStepsRouter = {
    rerun: rerunProcedure,
    list: impl.llmSteps.list.handler(({ context }) =>
      rows(
        context.db,
        llmStepDto,
        "SELECT * FROM control.llm_step ORDER BY source_key,step_version DESC",
      ),
    ),
  };
