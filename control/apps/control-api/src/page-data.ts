import { z } from "zod";
import { alertDto, jsonRow } from "@mdp/contracts";
import { rows, type DB } from "./db.js";

// Attention and quiet acknowledged history use the same unpaginated query.
export async function attention(
  db: DB,
  resolved = false,
  acknowledged = false,
) {
  return rows(
    db,
    alertDto.extend({ source_key: z.string().nullable(), message: z.string() }),
    `SELECT a.*,s.source_key,coalesce(r.error_message,replace(a.class,'_',' ')) AS message FROM control.alert a
     LEFT JOIN control.run r ON r.id=a.run_id OR (a.subject_type='run' AND r.id::text=a.subject_id AND a.run_id IS NULL)
     LEFT JOIN control.streamline s ON s.id=r.streamline_id OR (a.subject_type='streamline' AND s.id::text=a.subject_id AND r.streamline_id IS NULL)
     WHERE (a.resolved_at IS NOT NULL)=$1 AND ($1 OR (a.acknowledged_by IS NOT NULL)=$2)
     ORDER BY a.opened_at DESC,a.id`,
    [resolved, acknowledged],
  );
}
export async function pausedSince(db: DB) {
  return rows(
    db,
    jsonRow,
    `SELECT DISTINCT ON (after->'input'->>'source_key') after->'input'->>'source_key' AS source_key,at
    FROM control.audit_log WHERE action='streamlines.patchKnobs' AND after->>'state'='succeeded'
    AND after->'input'->>'enabled'='false' ORDER BY after->'input'->>'source_key',at DESC`,
  );
}
export async function budgetSpend(db: DB) {
  return rows(
    db,
    z.object({
      id: z.string(),
      spent: z.string(),
      scope_name: z.string().nullable(),
      window_start: z.string(),
      window_end: z.string().nullable(),
    }),
    `SELECT b.id,coalesce(sum(c.cost_cents),0)::text AS spent,
    CASE b.scope WHEN 'global' THEN 'Global' WHEN 'streamline' THEN (SELECT source_key FROM control.streamline WHERE id=b.scope_id)
      WHEN 'llm_step' THEN (SELECT source_key FROM control.llm_step WHERE id=b.scope_id) WHEN 'tenant' THEN (SELECT slug FROM control.tenant WHERE id=b.scope_id) END AS scope_name,
    to_char((CASE b.period WHEN 'daily' THEN date_trunc('day',now()) WHEN 'weekly' THEN date_trunc('week',now()) WHEN 'monthly' THEN date_trunc('month',now()) ELSE b.created_at END) AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI') AS window_start,
    to_char((CASE b.period WHEN 'daily' THEN date_trunc('day',now())+interval '1 day' WHEN 'weekly' THEN date_trunc('week',now())+interval '1 week' WHEN 'monthly' THEN date_trunc('month',now())+interval '1 month' END) AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI') AS window_end
    FROM control.budget b LEFT JOIN control.cost_ledger c ON c.is_current
    AND (b.scope='global' OR b.scope='tenant' AND c.tenant_id=b.scope_id OR b.scope='streamline' AND c.streamline_id=b.scope_id OR b.scope='llm_step' AND c.llm_step_id=b.scope_id)
    AND c.occurred_at >= CASE b.period WHEN 'daily' THEN date_trunc('day',now()) WHEN 'weekly' THEN date_trunc('week',now()) WHEN 'monthly' THEN date_trunc('month',now()) ELSE b.created_at END
    GROUP BY b.id`,
  );
}

// Bounded, read-only recovery choices. No credentials or storage URIs reach the page.
export async function recoveryChoices(db: DB) {
  const [cycles, dumps, warehouses] = await Promise.all([
    rows(
      db,
      z.object({
        id: z.string(),
        cadence: z.string(),
        scope: z.string(),
        closed_at: z.string(),
      }),
      "SELECT id,cadence,scope,closed_at FROM control.cycle WHERE closed_at IS NOT NULL AND status='closed' ORDER BY closed_at DESC LIMIT 50",
    ),
    rows(
      db,
      z.object({
        id: z.string(),
        source_key: z.string().nullable(),
        created_at: z.string(),
        run_id: z.string(),
      }),
      `SELECT d.id,s.source_key,d.created_at,d.run_id FROM control.dump d
       LEFT JOIN control.streamline s ON s.id=d.streamline_id
       WHERE d.kind='output' AND d.quarantined_at IS NULL ORDER BY d.created_at DESC LIMIT 50`,
    ),
    rows(
      db,
      z.object({
        id: z.string(),
        database: z.string(),
        adapter: z.string(),
        is_production: z.boolean(),
      }),
      "SELECT id,database,adapter,is_production FROM control.warehouse ORDER BY database,id",
    ),
  ]);
  return { cycles, dumps, warehouses };
}

export function leadColumns(
  columns: { name: string; type: string }[],
): string[] {
  const safe = columns.filter(
    (c) =>
      !/^_|(?:_id|_hash)$/.test(c.name) &&
      c.name !== "id" &&
      !/json|array|\[\]/i.test(c.type),
  );
  const name = safe.find((c) => /(^|_)(name|title|handle)(_|$)/.test(c.name));
  const platform = safe.find((c) => c.name === "platform");
  const numbers = safe
    .filter((c) => /int|numeric|decimal|float|double|real/i.test(c.type))
    .slice(0, 2);
  const age = safe.find(
    (c) =>
      /timestamp|date/i.test(c.type) ||
      /(?:observed|updated|created)_at$/.test(c.name),
  );
  return [
    ...new Set(
      [name, platform, ...numbers, age].flatMap((c) => (c ? [c.name] : [])),
    ),
  ];
}
