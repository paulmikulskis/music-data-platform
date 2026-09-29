import { probeTargets, probeActivation, probeImport } from "./target-probe.js";
import { parkStaleTargets } from "./stale-targets.js";
import { z } from "zod";
import { targetDto, targetSetDto, jsonRow } from "@mdp/contracts";
import { rows, one, AppError, type DB, type Param } from "./db.js";
import { targetCsv } from "./csv.js";
import { refuseStaleSource, resolveKey, undoMembers } from "./members.js";
import { type Context, impl, patch } from "./router.shared.js";

// The newest succeeded audit row that deactivated target `target` (a bulkActivate or patch whose
// input set active=false, so an edit-only patch is none) and whether the caller or a promoter key
// wrote it: a promoter reactivates only its own deactivations, and a person's stands.
const lastDeactivation = (target: string, actor: string, own: boolean) => `EXISTS (
  SELECT 1 FROM (
    SELECT a.actor FROM control.audit_log a
    WHERE (a.action='targets.parkStale' AND a.subject=${target}::text)
      OR (a.action IN ('targets.bulkActivate','targets.patch') AND a.after->>'state'='succeeded'
      AND a.after->'input'->>'active'='false'
      AND jsonb_path_exists(a.after->'result', '$[*] ? (@.id == $id && @.deactivated_at != null)',
        jsonb_build_object('id', ${target}::text)))
    ORDER BY a.at DESC LIMIT 1) last
  WHERE ${own ? "" : "NOT "}(last.actor=${actor}
    OR last.actor IN (SELECT 'api-key:' || k.id::text FROM control.api_key k WHERE k.role='promoter')))`;

// Whether a promoter key (or the caller) imported target `target`: its provenance, from the
// succeeded targets.importTargets audit row that added it (a result row with no op, or op add; an update
// in place is not an import). A promoter moves, resolves or adopts without a reason only what it
// imported, never a person's import or a seeded row.
const promoterImport = (target: string, actor: string) => `EXISTS (
  SELECT 1 FROM control.audit_log a
  WHERE a.action='targets.importTargets' AND a.after->>'state'='succeeded'
    AND jsonb_path_exists(a.after->'result'->'rows', '$[*] ? (@.id == $id && (!(exists(@.op)) || @.op == "add"))', jsonb_build_object('id', ${target}::text))
    AND (a.actor=${actor} OR a.actor IN (SELECT 'api-key:' || k.id::text FROM control.api_key k WHERE k.role='promoter')))`;

// A promoter key writes global playlist, account and artist_page sets and proposes
// tenant track targets; an operator resolves and activates them.

const PROMOTER_GLOBAL_KINDS = new Set(["playlist", "account", "artist_page"]);

async function promoterSets(context: Context, sql: string, params: Param[], move = false) {
  if (!context.identity.promoter) return;
  const sets = await rows(context.db, z.object({ tenant_id: z.uuid().nullable(), kind: z.string() }), sql, params);
  if (sets.some((set) => (set.tenant_id ? set.kind !== "track" || move : !PROMOTER_GLOBAL_KINDS.has(set.kind))))
    throw new AppError("forbidden", "A promoter key writes the global playlist, account and artist_page sets and proposes into tenant track sets only", 403);
}

async function promoterOwns(context: Context, ids: string[], message: string) {
  if (!context.identity.promoter) return;
  const owned = await rows(context.db, z.object({ id: z.string() }),
    `SELECT t.id::text AS id FROM control.target t WHERE t.id=ANY($1::uuid[]) AND ${promoterImport("t.id", "$2")}`,
    [ids, context.identity.actor]);
  if (owned.length !== new Set(ids).size) throw new AppError("forbidden", message, 403);
}

const setOfTarget = "SELECT s.tenant_id,s.kind FROM control.target t JOIN control.target_set s ON s.id=t.target_set_id WHERE t.id=ANY($1::uuid[])";

export const targetsRouter = {
    probe: impl.targets.probe.handler(async ({ context, input }) => {
      const members = await rows(context.db, z.object({ id: z.uuid() }), "SELECT id FROM control.target WHERE target_set_id=$1", [input.target_set_id]);
      return probeTargets(context.db, members.map((t) => t.id));
    }),
    parkStale: impl.targets.parkStale.handler(({ context }) => parkStaleTargets(context.db)),
    listSets: impl.targets.listSets.handler(({ context }) =>
      rows(
        context.db,
        targetSetDto,
        "SELECT * FROM control.target_set ORDER BY name",
      ),
    ),
    list: impl.targets.list.handler(({ context, input }) =>
      rows(
        context.db,
        targetDto,
        "SELECT * FROM control.target WHERE ($1::uuid IS NULL OR target_set_id=$1) ORDER BY created_at DESC LIMIT 1000",
        [input.target_set_id ?? null],
      ),
    ),
    lookup: impl.targets.lookup.handler(async ({ context, input }) => {
      await promoterSets(context, "SELECT tenant_id,kind FROM control.target_set WHERE id=$1", [input.target_set_id]);
      return rows(
        context.db,
        targetDto.extend({ promotion_reason: z.string().nullable(), has_spec: z.boolean(), promoter_import: z.boolean(), person_deactivated: z.boolean() }),
        `SELECT t.*,s.promotion_reason,s.target_id IS NOT NULL AS has_spec,${promoterImport("t.id", "$4")} AS promoter_import,
           ${lastDeactivation("t.id", "$4", false)} AS person_deactivated
         FROM control.target t LEFT JOIN control.target_spec s ON s.target_id=t.id
         WHERE t.target_set_id=$1 AND t.platform=$2 AND t.platform_account_id=$3 ORDER BY t.created_at,t.id`,
        [input.target_set_id, input.platform, input.platform_account_id, context.identity.actor],
      );
    }),
    createSet: impl.targets.createSet.handler(({ context, input }) => {
      if (context.identity.promoter && (input.tenant_id ? input.kind !== "track" : !PROMOTER_GLOBAL_KINDS.has(input.kind)))
        throw new AppError("forbidden", "A promoter key creates a tenant track set or a global playlist, account or artist_page set only", 403);
      return one(
        context.db,
        targetSetDto,
        "INSERT INTO control.target_set(kind,name,tenant_id) VALUES ($1,$2,$3) RETURNING *",
        [input.kind, input.name, input.tenant_id],
      );
    }),
    importTargets: impl.targets.importTargets.handler(
      async ({ context, input }) => {
        await promoterSets(context, "SELECT tenant_id,kind FROM control.target_set WHERE id=$1", [input.target_set_id]);
        const set = await one(
          context.db,
          targetSetDto,
          "SELECT * FROM control.target_set WHERE id=$1",
          [input.target_set_id],
        );
        await refuseStaleSource(context.db, set.id, input.source_ref);
        const parsed = targetCsv(input.csv);
        if (input.dry_run)
          return {
            dry_run: true,
            count: parsed.length,
            adds: parsed.length, updates: 0, unchanged: 0,
            rows: z.array(jsonRow).parse(JSON.parse(JSON.stringify(parsed.map(row => ({...row,resolution_status:"pending"}))))),
          };
        const inserted = [];
        for (const row of parsed)
          inserted.push(
            await one(
              context.db,
              targetDto,
              `INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,display_name,role,resolution_status) VALUES ($1,$2,$3,$4,$5,$6,'pending') RETURNING *`,
              [
                input.target_set_id,
                row.platform,
                row.platform_account_id || null,
                row.handle || null,
                row.display_name || null,
                row.role || null,
              ],
            ),
          );
        return probeImport(context.db, { dry_run: false, count: inserted.length,
          adds: inserted.length, updates: 0, unchanged: 0, rows: inserted });
      },
    ),
    // Deletes the import's adds and restores the specs it updated (members.ts undoMembers).
    undoImport: impl.targets.undoImport.handler(async ({context,input}) => {
      const imported = await one(context.db,z.object({after:z.object({state:z.literal("succeeded"),result:z.object({dry_run:z.literal(false),rows:z.array(jsonRow)})})}),
        "SELECT after FROM control.audit_log WHERE id=$1 AND action='targets.importTargets' AND actor=$2",[input.audit_id,context.identity.actor]);
      return {count:await undoMembers(context.db,imported.after.result.rows)};
    }),
    resolve: impl.targets.resolve.handler(async ({ context, input }) => {
      await promoterSets(context, setOfTarget, [[input.id]], true);
      await promoterOwns(context, [input.id], "A promoter key resolves only targets it imported");
      const target = await one(
        context.db,
        targetDto,
        "UPDATE control.target SET platform_account_id=$2,resolution_status='resolved' WHERE id=$1 RETURNING *",
        [input.id, input.platform_account_id],
      );
      // A handle-form canonical_key moves to its id form; the audit row records the transition.
      const transition = await resolveKey(context.db, input.id, input.platform_account_id);
      if (transition && context.audit_id)
        await context.db.unsafe("UPDATE control.audit_log SET after=after || $2::text::jsonb WHERE id=$1",
          [context.audit_id, JSON.stringify({ key_transition: transition })]);
      return target;
    }),
    patch: impl.targets.patch.handler(async ({ context, input }) => {
      const current = await one(
        context.db,
        targetDto,
        "SELECT * FROM control.target WHERE id=$1 FOR UPDATE",
        [input.id],
      );
      if (input.active && current.resolution_status !== "resolved")
        throw new AppError(
          "target_unresolved",
          "Resolve the target before activation",
          409,
        );
      const result = await patch(
        context.db,
        targetDto,
        "target",
        "id",
        input.id,
        {
          handle: input.handle,
          display_name: input.display_name,
          role: input.role,
          priority: input.priority,
        },
      );
      if (input.active === undefined) return result;
      if (input.active) await probeActivation(context.db, [input.id]);
      return one(
        context.db,
        targetDto,
        `UPDATE control.target SET activated_at=CASE WHEN $2 THEN now() ELSE activated_at END,deactivated_at=CASE WHEN $2 THEN NULL ELSE now() END WHERE id=$1 RETURNING *`,
        [input.id, input.active],
      );
    }),
    setSpec: impl.targets.setSpec.handler(async ({ context, input }) => {
      await promoterSets(context, setOfTarget, [[input.id]], input.activate === true);
      if (context.identity.promoter && !input.promotion_reason)
        throw new AppError("forbidden", "A promoter key writes a spec only with its promotion_reason", 403);
      const target = await one(context.db, targetDto, "SELECT * FROM control.target WHERE id=$1 FOR UPDATE", [input.id]);
      const held = await rows(context.db, z.object({ promotion_reason: z.string().nullable() }),
        "SELECT promotion_reason FROM control.target_spec WHERE target_id=$1", [input.id]);
      // A promoter writes a spec only on its own import, or where its own reason already holds one.
      if (context.identity.promoter && held[0] && held[0].promotion_reason !== (input.promotion_reason ?? null))
        throw new AppError("target_held", "Another writer holds this target's spec", 409);
      if (!held[0]) await promoterOwns(context, [input.id], "A promoter key writes a first spec only on its own imports");
      const spec = await one(
        context.db,
        z.object({ target_id: z.uuid(), resource_kind: z.string(), canonical_key: z.string(), params_json: jsonRow, promotion_reason: z.string().nullable() }),
        `INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json,promotion_reason)
         VALUES ($1,$2,$3,$4::text::jsonb,$5)
         ON CONFLICT(target_id) DO UPDATE SET params_json=EXCLUDED.params_json || control.target_spec.params_json,
           promotion_reason=coalesce(control.target_spec.promotion_reason, EXCLUDED.promotion_reason)
         RETURNING target_id,resource_kind,canonical_key,params_json,promotion_reason`,
        [input.id, input.resource_kind, input.canonical_key, JSON.stringify(input.params_json), input.promotion_reason ?? null],
      );
      if (!input.activate) return spec;
      if (target.resolution_status !== "resolved")
        throw new AppError("target_unresolved", "Resolve the target before activation", 409);
      if (context.identity.promoter) {
        // It activates with its spec only a target it imported, and never one a person deactivated.
        const refused = await rows(context.db, z.object({ refused: z.boolean() }),
          `SELECT ${lastDeactivation("t.id", "$2", false)} OR NOT ${promoterImport("t.id", "$2")} AS refused FROM control.target t WHERE t.id=$1`,
          [input.id, context.identity.actor]);
        if (refused[0]?.refused)
          throw new AppError("target_held", "A person imported or deactivated this target; the promoter leaves it", 409);
      }
      await probeActivation(context.db, [input.id]);
      await context.db.unsafe("UPDATE control.target SET activated_at=now(),deactivated_at=NULL WHERE id=$1", [input.id]);
      return spec;
    }),
    bulkActivate: impl.targets.bulkActivate.handler(
      async ({ context, input }) => {
        const found = await rows(
          context.db,
          targetDto,
          "SELECT * FROM control.target WHERE id=ANY($1::uuid[]) FOR UPDATE",
          [input.ids],
        );
        if (found.length !== new Set(input.ids).size)
          throw new AppError("not_found", "Some targets do not exist", 404);
        if (context.identity.promoter) {
          await promoterSets(context, setOfTarget, [input.ids], true);
          if (!input.promotion_reason) {
            // Without a reason a promoter moves only its own spec-less imports, and never back
            // over a person's deactivation.
            await promoterOwns(context, input.ids, "A promoter key moves without its promotion_reason only its own imports");
            const held = await rows(context.db, z.object({ n: z.number() }),
              `SELECT count(*)::int AS n FROM control.target t WHERE t.id=ANY($1::uuid[])
                 AND (EXISTS (SELECT 1 FROM control.target_spec s WHERE s.target_id=t.id)
                   OR ($2 AND ${lastDeactivation("t.id", "$3", false)}))`,
              [input.ids, input.active, context.identity.actor]);
            if (held[0]?.n)
              throw new AppError("forbidden", "A promoter key moves a target with a spec, or one a person deactivated, only under its promotion_reason", 403);
          }
        }
        if (
          input.active &&
          found.some((t) => t.resolution_status !== "resolved")
        )
          throw new AppError(
            "target_unresolved",
            "Resolve every target before activation",
            409,
          );
        if (input.active) await probeActivation(context.db, input.ids);
        // With promotion_reason, only targets whose spec carries it change, and a
        // reactivation also needs the target's last deactivation to be this caller's or a
        // promoter key's, so a person's stands.
        return rows(
          context.db,
          targetDto,
          `UPDATE control.target t SET activated_at=CASE WHEN $2 THEN now() ELSE t.activated_at END,deactivated_at=CASE WHEN $2 THEN NULL ELSE now() END
           WHERE t.id=ANY($1::uuid[]) AND ($3::text IS NULL OR (
             EXISTS (SELECT 1 FROM control.target_spec s WHERE s.target_id=t.id AND s.promotion_reason=$3)
             AND (NOT $2 OR ${lastDeactivation("t.id", "$4", true)})))
           RETURNING t.*`,
          [input.ids, input.active, input.promotion_reason ?? null, context.identity.actor],
        );
      },
    ),
  };
