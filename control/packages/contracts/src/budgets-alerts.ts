import { costsyncContract } from "./workbench.js";
import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { bigintWire } from "@mdp/data-sdk";
import { id, empty, get, post } from "./shared.js";

export const alertDto = s.alert
  .pick({
    id: true,
    class: true,
    severity: true,
    subject_type: true,
    subject_id: true,
    run_id: true,
    acknowledged_by: true,
    resolved_by: true,
    resolution_reason: true,
    runbook_slug: true,
  })
  .extend({
    opened_at: z.iso.datetime(),
    resolved_at: z.iso.datetime().nullable(),
  });

export const budgetDto = s.budget
  .pick({
    id: true,
    scope: true,
    scope_id: true,
    period: true,
    soft_pct: true,
    hard_action: true,
  })
  .extend({ cap_cents: bigintWire, ceiling_cents: bigintWire.nullable(), cap_requests: bigintWire.nullable() });

export const weekly = z.object({
  runs_by_status: z.array(z.object({ status: z.string(), count: bigintWire })),
  rows_landed: bigintWire,
  rows_by_day_source: z.array(z.object({ day: z.string(), source_key: z.string(), rows: bigintWire })),
  cost_by_scope: z.array(
    z.object({ scope: z.string(), scope_kind: z.string(), scope_id: z.string().nullable(), scope_name: z.string().nullable(), cost_cents: bigintWire }),
  ),
  open_alerts: bigintWire,
  acknowledged_alerts: bigintWire,
  stale_targets: bigintWire,
  drift_pending: bigintWire,
  freshness_by_cadence: z.array(
    z.object({
      cadence: z.string(),
      last_at: z.iso.datetime().nullable(),
      stale: z.boolean(),
    }),
  ),
});

export const budgetsContract = {
    create: post("/budgets/create", z.object({
      scope: z.enum(["global", "tenant", "streamline", "llm_step", "provider"]),
      scope_id: z.uuid().nullable().default(null), period: z.string().min(1).max(80),
      cap_cents: bigintWire, soft_pct: z.number().int().min(0).max(100),
      hard_action: z.enum(["warn", "pause", "degrade"]), ceiling_cents: bigintWire,
      // A provider's vendor requests in the period. A provider budget needs one and pauses at it: the
      // runtime treats a provider row without a request cap, or one that warns or degrades, as no row.
      cap_requests: bigintWire.nullable().default(null),
    }).refine(v => (v.scope === "global") === (v.scope_id === null), "Non-global budgets require a scope identity")
      .refine(v => v.cap_requests === null || (v.scope === "provider" && BigInt(v.cap_requests) >= 0n), "Only a provider budget carries a request cap, and it is nonnegative")
      .refine(v => v.scope !== "provider" || (v.cap_requests !== null && v.hard_action === "pause"), "A provider budget needs a request cap and pauses at it")
      .refine(v => BigInt(v.cap_cents) >= 0n && BigInt(v.ceiling_cents) >= BigInt(v.cap_cents), "Cap must be nonnegative and within ceiling"), budgetDto),
    list: get("/budgets", empty, z.array(budgetDto)),
    raise: post(
      "/budgets/raise",
      // cap_requests, when given, replaces a provider budget's request cap; a vendor provider's cap is never
      // removed, and changing it alone needs no cents ceiling.
      id.extend({ cap_cents: bigintWire, cap_requests: bigintWire.nullable().optional() }),
      budgetDto,
    ),
  };

export const costsContract = {
    sync: costsyncContract,
    summary: get(
      "/costs",
      empty,
      z.array(z.object({ scope: z.string(), cost_cents: bigintWire })),
    ),
  };

export const alertsContract = {
    list: get("/alerts", z.object({
      resolved: z.boolean().default(false), acknowledged: z.boolean().default(false), offset: z.number().int().min(0).default(0),
      subject_id: z.string().optional(), class: z.string().optional(),
    }), z.array(alertDto)),
    acknowledge: post("/alerts/acknowledge", id, alertDto),
    resolve: post("/alerts/resolve", id, alertDto),
  };

export const screenContract = { weekly: get("/screen/weekly", empty, weekly) };
