import { z } from "zod";
import { get } from "./shared.js";
import { sourceBrand, sourceFamily } from "./source-wording.js";

const count = z.string().regex(/^\d+$/);
export const inventoryLayer = z.object({
  layer: z.enum([
    "raw",
    "staging",
    "intermediate",
    "marts",
    "reference",
    "tenant",
  ]),
  relations: z.number().int(),
  rows_est: count.nullable(),
  bytes: count,
  complete: z.boolean(),
});
export const platformEvent = z.object({
  key: z.string(),
  kind: z.enum([
    "cycle_opened",
    "cycle_closed",
    "run_admitted",
    "run_settled",
    "alert_opened",
    "alert_resolved",
  ]),
  subject_id: z.uuid(),
  occurred_at: z.iso.datetime(),
  cycle_id: z.uuid().nullable(),
  scope: z.string().nullable(),
  status: z.string().nullable(),
  scheduled: z.boolean().nullable(),
});
export const runnerState = z.object({
  state: z.enum(["idle", "busy", "unknown"]),
  busy: z.boolean(),
  sessions: z.array(
    z.object({
      lock: z.string(),
      kind: z.enum(["scheduled", "other"]),
      started_at: z.iso.datetime(),
    }),
  ),
  next_scheduled_at: z.iso
    .datetime()
    .nullable()
    .describe("Earliest eligible global Core run. Host ticks may start later."),
  next_step: z.string(),
});
export const holdings = z.object({
  queried_at: z.iso.datetime(),
  since: z.iso.datetime(),
  warehouse: z.string(),
  ingestion: z.object({
    summary: z.object({
      rows_inserted: count,
      sources_live: count,
      today_rows: count,
      today_sources: count,
      today_measured: z.boolean(),
      days: z.array(z.object({ day: z.iso.date(), rows_inserted: count })),
    }),
    rows: z.array(
      z.object({
        day: z.iso.date(),
        source_key: z.string(),
        rows_inserted: count,
      }),
    ),
    next_step: z.string(),
  }),
  inventory_history: z.object({
    first_snapshot_day: z.iso.date().nullable(),
    unavailable_before: z.iso.date().nullable(),
    days: z.array(
      z.object({
        day: z.iso.date(),
        state: z.enum(["available", "unavailable"]),
        layers: z.array(
          inventoryLayer.extend({ captured_at: z.iso.datetime() }),
        ),
      }),
    ),
    next_step: z.string(),
  }),
  vendor_cost: z.object({
    cost_cents: count,
    has_current_rows: z.boolean(),
    label: z.enum(["estimate", "reconciled", "partly reconciled"]),
    days: z.array(
      z.object({
        day: z.iso.date(),
        cost_cents: count,
        label: z.enum(["estimate", "reconciled", "partly reconciled"]),
      }),
    ),
    infrastructure_included: z.literal(false),
    next_step: z.string(),
  }),
});
// One row per source: plain wording from source-wording.ts, then what production collected.
export const platformSource = z.object({
  source_key: z.string(),
  display_name: z.string(),
  brand: sourceBrand.nullable(),
  family: sourceFamily,
  description: z.string(),
  cadence: z.string().nullable(),
  enabled: z.boolean(),
  first_collected: z.iso
    .datetime()
    .nullable()
    .describe("First production load of this source; null before any."),
  last_read: z.iso
    .datetime()
    .nullable()
    .describe("Latest production load of this source."),
  entries_today: count.describe(
    "Rows loaded since 00:00 UTC by production scheduled runs.",
  ),
  targets: z
    .number()
    .int()
    .nullable()
    .describe(
      "watch_list_total: shared frozen set size, not the number selected by this reader.",
    ),
  tracked: z
    .object({
      count: z.number().int().nonnegative(),
      unit: z.enum(["playlists", "charts", "accounts", "songs", "curators"]),
      as_of: z.iso.datetime(),
    })
    .nullable()
    .default(null)
    .describe(
      "Reader membership at the latest frozen revision, across all weekday buckets; not successful reads.",
    ),
  evidence: z
    .object({
      declared_at: z.iso.datetime(),
      configured_at: z.iso.datetime(),
      checked_at: z.iso.datetime(),
      tenant_bound: z.boolean(),
      last_success: z.iso.datetime().nullable(),
      attempt: z
        .object({
          run_id: z.uuid(),
          attempt_no: z.number().int().nullable(),
          status: z.string(),
          at: z.iso.datetime(),
        })
        .nullable(),
      incident: z
        .object({
          alert_id: z.uuid(),
          run_id: z.uuid().nullable(),
          attempt_no: z.number().int().nullable(),
          class: z.string(),
          at: z.iso.datetime(),
          remediation: z.string().nullable(),
        })
        .nullable(),
    })
    .nullable()
    .default(null)
    .describe(
      "Independently dated registry, deployed configuration, successful collection and latest attempt evidence.",
    ),
  days: z
    .array(z.object({ day: z.iso.date(), entries: count }))
    .describe("Rows loaded per UTC day, last 14 days, oldest first."),
});
export const platformSources = z.object({
  queried_at: z.iso.datetime(),
  sources: z.array(platformSource),
  next_step: z.string(),
});
export const nightWindow = z
  .object({ since: z.iso.datetime(), until: z.iso.datetime() })
  .refine(
    ({ since, until }) =>
      Date.parse(until) > Date.parse(since) &&
      Date.parse(until) - Date.parse(since) <= 36 * 3600000,
    {
      message:
        "Choose a window of at most 36 hours. Open the night view again.",
    },
  );
const nightStatus = z.enum([
  "queued",
  "running",
  "succeeded",
  "partial",
  "failed",
  "superseded",
]);
export const nightTargets = z.object({
  frozen_membership: z.number().int().nonnegative().nullable(),
  eligible: z.number().int().nonnegative().nullable(),
  succeeded: z.number().int().nonnegative().nullable(),
  skipped: z.number().int().nonnegative().nullable(),
  unit: z.string().nullable(),
  evidence_at: z.iso.datetime().nullable(),
});
export const nightAttempt = z.object({
  attempt_no: z.number().int(),
  started_at: z.iso.datetime(),
  ended_at: z.iso.datetime().nullable(),
  status: nightStatus,
  trigger: z.enum(["scheduled", "manual", "retry/restore", "unknown"]),
  trigger_evidence: z.object({
    dbt_run_id: z.string().nullable(),
    reason_category: z.string().nullable(),
    runner: z.string().nullable(),
  }),
  targets: nightTargets.nullable(),
});
export const platformNight = z.object({
  window: nightWindow.safeExtend({ queried_at: z.iso.datetime() }),
  warehouse_id: z.uuid(),
  runs: z.array(
    z.object({
      run_id: z.uuid(),
      admitted_at: z.iso.datetime(),
      source_key: z.string().nullable(),
      kind: z.string(),
      cycle_id: z.uuid().nullable(),
      scope: z.literal("global"),
      warehouse_id: z.uuid(),
      status: nightStatus,
      attempts: z.array(nightAttempt),
      outputs: z.object({
        dump_count: count.nullable(),
        rows_landed: count.nullable(),
      }),
      targets: nightTargets,
    }),
  ),
  coverage: z.array(
    z.object({
      source_key: z.string(),
      succeeded: z.number().int().nullable(),
      unit: z.string().nullable(),
      evidence_at: z.iso.datetime().nullable(),
    }),
  ),
  closes: z.array(
    z.object({
      cycle_id: z.uuid(),
      cadence: z.string(),
      close_no: count.nullable(),
      closed_at: z.iso.datetime(),
      status: z.string(),
    }),
  ),
  alerts: z.array(
    z.object({
      run_id: z.uuid().nullable(),
      attempt_no: z.number().int().nullable(),
      opened_at: z.iso.datetime(),
      class: z.string(),
      summary: z.string(),
      next_step: z.string(),
      resolved_at: z.iso.datetime().nullable(),
    }),
  ),
  runner: runnerState,
  next_due: z.iso.datetime().nullable(),
  next_step: z.string(),
});
export const relationCountKey = z.object({
  relation: z.string().regex(/^(marts|intermediate|staging)\.[a-z][a-z0-9_]*$/),
  build_key: z.string().min(1).max(2048).nullable(),
  input_hash: z.string().regex(/^[a-f0-9]{64}$/),
});
export const relationCount = z.object({
  warehouse_id: z.uuid(),
  relation: z.string(),
  build_key: z.string(),
  captured_at: z.iso.datetime(),
  row_count: count,
  latest_at: z.iso.datetime().nullable(),
  basis: z.literal("exact"),
  input_hash: z.string(),
});
export const relationCountsInput = z.object({
  keys: z.array(relationCountKey).max(100),
});
export const relationCounts = z.object({
  queried_at: z.iso.datetime(),
  counts: z.array(
    z.object({
      key: relationCountKey,
      capture: relationCount.nullable(),
      last_good: relationCount.nullable(),
      denominator: count.nullable(),
      suppression: z.enum([
        "none",
        "unstamped",
        "not_captured",
        "different_input",
      ]),
    }),
  ),
  next_step: z.string(),
});
export const platformContract = {
  night: get("/platform/night", nightWindow, platformNight),
  relationCounts: get(
    "/platform/relation-counts",
    relationCountsInput,
    relationCounts,
  ),
  holdings: get(
    "/platform/holdings",
    z.object({
      since: z.iso.datetime().describe("A UTC time within the last 90 days."),
    }),
    holdings,
  ),
  sources: get("/platform/sources", z.object({}), platformSources),
  events: get(
    "/platform/events",
    z.object({
      after: z.string().min(1).max(2048).optional(),
      limit: z.number().int().min(1).max(500).default(100),
    }),
    z.object({
      events: z.array(platformEvent),
      next_cursor: z.string(),
      overlap_start: z.iso.datetime().nullable(),
      has_more: z.boolean(),
      runner: runnerState,
      next_step: z.string(),
    }),
  ),
};
