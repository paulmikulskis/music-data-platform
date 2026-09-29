import { rerunContract } from "./workbench.js";
import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { bigintWire, receiptSchema } from "@mdp/data-sdk";
import { id, source, empty, jsonRow, get, post } from "./shared.js";

export const streamlineDto = s.streamline.pick({
  id: true,
  source_key: true,
  layer: true,
  tenant_bound: true,
  writes: true,
  reads: true,
  external: true,
  cadence_tag: true,
  enabled: true,
  allow_partial: true,
  batch_size: true,
  max_concurrency: true,
  timeout_s: true,
  storage: true,
  acknowledged_fingerprints: true,
}).extend({
  // Inputs a gold streamline parked at its latest read while its inputs_parked alert is open.
  parked_inputs: z.number().int().default(0),
});

export const runDto = s.run
  .pick({
    id: true,
    kind: true,
    scope: true,
    streamline_id: true,
    cycle_id: true,
    parent_run_id: true,
    trace_id: true,
    status: true,
    coverage: true,
    error_class: true,
    error_message: true,
  })
  .extend({
    rows_written: bigintWire,
    rows_rejected: bigintWire,
    cost_cents: bigintWire,
    status: z.string(),
    created_at: z.iso.datetime(),
  });

export const eventDto = s.run_event
  .pick({
    id: true,
    run_id: true,
    level: true,
    event_type: true,
    message: true,
  })
  .extend({ at: z.iso.datetime(), attrs: jsonRow });

export const llmStepDto = s.llm_step.pick({
  id: true,
  source_key: true,
  model: true,
  prompt_version: true,
  step_version: true,
  fallback_model: true,
  enabled: true,
});

export const admission = z.object({ run_id: z.uuid(), status: z.string() });

export const poll = z.object({
  run: runDto,
  receipts: z.array(receiptSchema),
  repairs_pending: z.number().int(),
});

export const functionPage = z.object({
  manifest: jsonRow,
  last_runs: z.array(runDto),
  receipts: z.array(z.array(receiptSchema)),
  output_preview: z.array(
    z.object({ table: z.string(), rows: z.array(jsonRow), columns: z.array(z.object({name:z.string(),type:z.string(),nullable:z.boolean()})), next_cursor:z.string().nullable() }),
  ),
  rejected_sample: z.array(jsonRow),
  row_counts: z.array(z.object({ at: z.string(), rows: bigintWire })),
  fingerprint_history: z.array(jsonRow),
  log_url: z.string(),
});

const scope = z
  .string()
  .regex(/^(global|tenant:[0-9a-f-]{36})$/)
  .default("global");

export const fixtureScenario = z.enum(["normal", "html", "missing_stats", "not_found", "retry", "all_null"]);

export const manualResult = z.union([admission.extend({ path: z.literal("core") }), z.object({ path: z.literal("cloud"), dbt_run_id: z.string(), job_id: z.string(), status: z.string() })]);

const manual = source.extend({
  key: z.string().min(1).max(200),
  scope,
  dbt_run_id: z.string().optional(),
  fixture_scenario: fixtureScenario.optional(),
});

const pr = z.object({
  branch: z.string(),
  title: z.string(),
  body: z.string(),
  diff: z.string(),
  pr_opened: z.boolean(),
  url: z.string().optional(),
});

export const dryProbeResult = z.object({
  status: z.enum(["passed", "failed", "not_due", "skipped", "timed_out"]),
  error_class: z.string().nullable(),
  records_validated: z.number().int().nonnegative(),
  next_step: z.string(),
});

export const streamlinesContract = {
    dryProbe: post("/streamlines/dry-probe", source.extend({ scope }), dryProbeResult),
    canaries: post("/streamlines/canaries", empty, z.object({ results: z.array(z.object({ source_key: z.string(), status: z.enum(["passed", "failed", "not_due", "skipped", "timed_out"]), error_class: z.string().optional(), next_step: z.string() })) })),
    list: get("/streamlines", empty, z.array(streamlineDto)),
    get: get("/streamlines/{source_key}", source, streamlineDto),
    // Releases a streamline's parked inputs: resolves its open inputs_parked alerts, and the next
    // read counts only rejections after that (derived.parked_inputs).
    unpark: post("/streamlines/unpark", source, z.object({ source_key: z.string(), released_alerts: z.number().int() })),
    patchKnobs: post(
      "/streamlines/knobs",
      source.extend({
        enabled: z.boolean().optional(),
        batch_size: z.number().int().min(1).max(1000).optional(),
        max_concurrency: z.number().int().min(1).max(100).optional(),
        allow_partial: z.boolean().optional(),
        storage: z.enum(["heap", "iceberg"]).optional(),
      }),
      streamlineDto,
    ),
    runNow: post("/streamlines/run", manual, manualResult),
    probe: post("/streamlines/probe", manual, manualResult),
    acknowledgeDrift: post(
      "/streamlines/drift",
      source.extend({ fingerprint: z.string().min(1) }),
      streamlineDto,
    ),
    repair: post(
      "/streamlines/repair",
      z.object({
        dump_id: z.uuid(),
        target_table: z.string().regex(/^raw\.[a-z][a-z0-9_]*$/),
      }),
      jsonRow,
    ),
    backfill: post(
      "/streamlines/backfill",
      source.extend({
        cycle_id: z.uuid().optional(),
        window: z.object({ from: z.iso.datetime({ offset: true }), to: z.iso.datetime({ offset: true }) }).optional(),
        target_ids: z.array(z.uuid()).min(1).optional(),
        scope: z.string().optional(),
      }).refine(value => [value.cycle_id, value.window, value.target_ids].filter(x => x !== undefined).length === 1,
        "Choose exactly one cycle, window, or target list"),
      admission,
    ),
    migrate: post(
      "/streamlines/migrate",
      z.object({ warehouse_id: z.uuid(), source_keys: z.array(z.string()).min(1).optional(), since: z.iso.datetime({ offset: true }).optional() }),
      jsonRow,
    ),
    requestCadenceChange: post(
      "/streamlines/cadence",
      source.extend({ cadence: z.enum(["hourly", "daily", "weekly"]) }),
      pr,
    ),
    resetCursor: post(
      "/streamlines/cursor/reset",
      source.extend({
        target_id: z.uuid().nullable(),
        cursor_key: z.string().min(1),
      }),
      z.object({ reset_generation: bigintWire }),
    ),
  };

export const runsContract = {
    list: get(
      "/runs",
      z.object({
        source_key: z.string().optional(),
        status: z.string().optional(),
      }),
      z.array(runDto),
    ),
    get: get("/runs/{id}", id, poll),
    events: get("/runs/{id}/events", id, z.array(eventDto)),
    tree: get("/runs/{id}/tree", id, z.array(runDto)),
    cancel: post("/runs/cancel", id, poll),
  };

export const functionsContract = {
    page: get("/functions/{source_key}", source.extend({metadata_only:z.boolean().optional(),preview_table:z.string().optional(),preview_cursor:z.string().max(4096).optional()}), functionPage),
    register: post(
      "/functions/register",
      empty,
      z.object({ registered: z.number().int() }),
    ),
  };

export const llmStepsContract = { rerun: rerunContract, list: get("/llm-steps", empty, z.array(llmStepDto)) };
