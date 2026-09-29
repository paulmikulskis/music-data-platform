import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { id, empty, jsonRow, get, post } from "./shared.js";
import { tenantSlug } from "./tenants.js";

// A batch import’s source identity: `<repo>:<path>@<commit time>`.
export const sourceRef = z
  .string()
  .max(500)
  .regex(/^[A-Za-z0-9._/-]+:\S+@\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/);

// What an import through the members update path returns: its plan counts and a row per member, with its op.
const importResult = z.object({
  dry_run: z.boolean(),
  count: z.number().int(),
  adds: z.number().int(),
  updates: z.number().int(),
  unchanged: z.number().int(),
  rows: z.array(jsonRow),
});

export const targetSetDto = s.target_set.pick({
  id: true,
  kind: true,
  tenant_id: true,
  name: true,
});

export const targetDto = s.target
  .pick({
    id: true,
    target_set_id: true,
    platform: true,
    platform_account_id: true,
    handle: true,
    display_name: true,
    role: true,
    resolution_status: true,
    priority: true,
  })
  .extend({
    activated_at: z.iso.datetime().nullable(),
    deactivated_at: z.iso.datetime().nullable(),
  });

export const targetsContract = {
    probe: post("/targets/probe", z.object({ target_set_id: z.uuid() }), z.object({ results: z.array(jsonRow) })),
    parkStale: post("/targets/park-stale", empty, z.object({ parked: z.array(z.uuid()), warnings: z.array(z.object({run_id:z.string(),source:z.string(),cycle_id:z.string(),reason:z.string()})) })),
    listSets: get("/targets/sets", empty, z.array(targetSetDto)),
    list: get(
      "/targets",
      z.object({ target_set_id: z.uuid().optional() }),
      z.array(targetDto),
    ),
    createSet: post(
      "/targets/sets/create",
      z.object({
        kind: z.string().min(1),
        name: z.string().min(1),
        tenant_id: z.uuid().nullable().default(null),
      }),
      targetSetDto,
    ),
    // unchanged, and every result row carries its op. source_ref is recorded in the audit row, and one
    // older than the set's last is refused with stale_export.
    importTargets: post(
      "/targets/import",
      z.object({
        target_set_id: z.uuid(),
        csv: z.string().min(1).max(1_000_000),
        dry_run: z.boolean().default(true),
        source_ref: sourceRef.optional(),
      }),
      importResult,
    ),
    undoImport: post("/targets/import/undo", z.object({ audit_id: z.uuid() }), z.object({ count: z.number().int() })),
    resolve: post(
      "/targets/resolve",
      id.extend({ platform_account_id: z.string().min(1) }),
      targetDto,
    ),
    patch: post(
      "/targets/patch",
      id.extend({
        handle: z.string().optional(),
        display_name: z.string().optional(),
        role: z.string().optional(),
        priority: z.number().int().optional(),
        active: z.boolean().optional(),
      }),
      targetDto,
    ),
    // A promoter's typed identity and frozen params; keys a spec already holds win.
    // With activate, the spec and the target's activation commit together: a promoter's new
    // target is never active without its reason.
    setSpec: post(
      "/targets/spec",
      id.extend({
        resource_kind: z.string().min(1),
        canonical_key: z.string().min(1).max(500),
        params_json: jsonRow.default({}),
        promotion_reason: z.string().max(200).optional(),
        activate: z.boolean().optional(),
      }),
      z.object({
        target_id: z.uuid(),
        resource_kind: z.string(),
        canonical_key: z.string(),
        params_json: jsonRow,
        promotion_reason: z.string().nullable(),
      }),
    ),
    // The keyed lookup promoters use: one identity's targets in a set, in any state, with
    // the spec's reason. No paged listing decides what a set holds.
    lookup: get(
      "/targets/lookup",
      z.object({
        target_set_id: z.uuid(),
        platform: z.string().min(1),
        platform_account_id: z.string().min(1).max(500),
      }),
      z.array(targetDto.extend({ promotion_reason: z.string().nullable(), has_spec: z.boolean(), promoter_import: z.boolean(), person_deactivated: z.boolean() })),
    ),
    // With promotion_reason, only targets whose spec carries it change, and a reactivation only
    // when the target's last deactivation was the caller's or a promoter key's: a promoter moves
    // only what it promoted and never undoes a person's deactivation. Returns the targets changed.
    bulkActivate: post(
      "/targets/activate",
      z.object({
        ids: z.array(z.uuid()).min(1).max(1000),
        active: z.boolean(),
        promotion_reason: z.string().min(1).max(200).optional(),
      }),
      z.array(targetDto),
    ),
  };
