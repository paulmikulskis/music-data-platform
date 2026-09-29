import { z } from "zod";
import {
  holdings,
  inventoryLayer,
  platformSource,
  platformSources,
  sourceWording,
  sourceReaders,
  sourceFallback,
} from "@mdp/contracts";
import { AppError, rows, one, type DB } from "./db.js";
import {
  ingestionSql,
  ingestionEnvelopeSql,
  costSql,
  inventoryHistorySql,
  firstInventorySql,
  productionRun,
  outputTarget,
  genuineSourceRun,
} from "@mdp/contracts/platform-sql";
export {
  ingestionSql,
  ingestionEnvelopeSql,
  costSql,
  inventoryHistorySql,
  firstInventorySql,
} from "@mdp/contracts/platform-sql";
export {
  readRunnerState,
  readNextScheduledRun,
  nextScheduledSql,
} from "@mdp/contracts/runner-state";

// Check production eligibility per candidate run. Keeping it in EXISTS lets the
// ordered run scan stop early instead of sorting all runs after selective joins.
const sourceProductionSql = `EXISTS (
  SELECT 1 FROM control.warehouse w JOIN control.cycle c ON c.id=r.cycle_id
  WHERE ${productionRun}
)`;

// Walk runs in creation order and stop at the first with a qualifying load.
// Only that run's output receipts contribute to its earliest or latest load time.
const sourceLoadSql = (
  direction: "ASC" | "DESC",
  aggregate: "min" | "max",
) => `SELECT loads.loaded_at
  FROM control.run r
  JOIN LATERAL (
    SELECT ${aggregate}(l.loaded_at) AS loaded_at
    FROM control.dump d
    JOIN control.load l ON l.dump_id=d.id AND l.warehouse_id=r.warehouse_id
    WHERE d.run_id=r.id AND d.kind='output' AND d.streamline_id=s.id
      AND l.status='loaded' AND l.loaded_at <= $2::timestamptz AND ${outputTarget}
    HAVING count(*)>0
  ) loads ON true
  WHERE r.streamline_id=s.id AND ${sourceProductionSql} AND ${genuineSourceRun}
  ORDER BY r.created_at ${direction} LIMIT 1`;

export const sourceLoadsSql = `SELECT first_load.loaded_at AS first_collected,last_load.loaded_at AS last_read
  FROM (SELECT 1) anchor
  LEFT JOIN LATERAL (${sourceLoadSql("ASC", "min")}) first_load ON true
  LEFT JOIN LATERAL (${sourceLoadSql("DESC", "max")}) last_load ON true`;

export const sourcesSql = `WITH by_source AS MATERIALIZED (${ingestionSql}),
  candidates AS MATERIALIZED (
    SELECT s.* FROM control.streamline s
    WHERE NOT s.tenant_bound
  )
  SELECT s.source_key,s.layer,p.provider,s.cadence_tag AS cadence,s.enabled,
    loads.first_collected,loads.last_read,
    coalesce((SELECT rows_inserted FROM by_source b WHERE b.source_key=s.source_key
      AND b.day=($2::timestamptz AT TIME ZONE 'UTC')::date::text),'0') AS entries_today,
    targets.member_count AS targets,
    CASE WHEN targets.id IS NOT NULL AND selection.unit IS NOT NULL THEN
      jsonb_build_object('count',tracked.count,'unit',selection.unit,'as_of',to_char(targets.taken_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) END AS tracked,
    jsonb_build_object('declared_at',to_char(s.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'configured_at',to_char(s.updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'checked_at',to_char($2::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'tenant_bound',s.tenant_bound,'last_success',to_char(success.at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'attempt',CASE WHEN attempt.id IS NOT NULL THEN jsonb_build_object(
        'run_id',attempt.id,'attempt_no',attempt.attempt_no,'status',attempt.status,'at',to_char(attempt.at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) END,
      'incident',CASE WHEN incident.id IS NOT NULL THEN jsonb_build_object(
        'alert_id',incident.id,'run_id',incident.run_id,'attempt_no',incident.attempt_no,
        'class',incident.class,'at',to_char(incident.opened_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'remediation',incident.resolution_reason) END) AS evidence,
    (SELECT jsonb_agg(jsonb_build_object('day',calendar.day::date::text,
      'entries',coalesce(b.rows_inserted,'0')) ORDER BY calendar.day)
     FROM generate_series(($1::timestamptz AT TIME ZONE 'UTC')::date::timestamp,
       ($2::timestamptz AT TIME ZONE 'UTC')::date::timestamp,interval '1 day') calendar(day)
     LEFT JOIN by_source b ON b.source_key=s.source_key AND b.day=calendar.day::date::text) AS days
  FROM candidates s
  LEFT JOIN control.rights_source p ON p.source_key=s.source_key
  LEFT JOIN LATERAL (${sourceLoadsSql}) loads ON true
  LEFT JOIN LATERAL (
    SELECT r.revision_id FROM control.run r
    WHERE r.streamline_id=s.id AND r.revision_id IS NOT NULL AND ${sourceProductionSql}
      AND r.created_at <= $2::timestamptz
    ORDER BY r.created_at DESC LIMIT 1
  ) latest_run ON true
  LEFT JOIN control.target_export targets ON targets.id=latest_run.revision_id
  LEFT JOIN LATERAL (
    SELECT unit,platforms,member_cadence,id_prefix FROM jsonb_to_recordset(
      '${JSON.stringify(sourceReaders).replaceAll("'", "''")}'::jsonb
    ) reader(source_key text,unit text,platforms text[],member_cadence text,id_prefix text)
    WHERE reader.source_key=s.source_key
  ) selection ON true
  LEFT JOIN LATERAL (
    SELECT count(*)::int AS count FROM control.target_export_member m
    WHERE m.revision_id=targets.id AND m.target_json->>'platform'=ANY(selection.platforms)
      AND (selection.member_cadence IS NULL OR selection.member_cadence =
        CASE WHEN m.params_json->>'cadence' IN ('daily','weekly') THEN m.params_json->>'cadence' ELSE 'daily' END)
      AND (selection.id_prefix IS NULL OR starts_with(m.target_json->>'platform_account_id',selection.id_prefix))
  ) tracked ON true
  LEFT JOIN LATERAL (
    SELECT r.id,coalesce(a.status,r.status)::text AS status,a.attempt_no,
      coalesce(a.started_at,r.created_at) AS at
    FROM control.run r LEFT JOIN LATERAL (
      SELECT * FROM control.run_attempt a WHERE a.run_id=r.id ORDER BY attempt_no DESC LIMIT 1
    ) a ON true
    WHERE r.streamline_id=s.id AND ${sourceProductionSql} AND ${genuineSourceRun}
      AND r.created_at <= $2::timestamptz
    ORDER BY r.created_at DESC LIMIT 1
  ) attempt ON true
  LEFT JOIN LATERAL (
    SELECT coalesce(r.settled_at,r.updated_at) AS at FROM control.run r
    WHERE r.streamline_id=s.id AND r.status='succeeded' AND r.rows_written>0
      AND ${sourceProductionSql} AND ${genuineSourceRun} AND coalesce(r.settled_at,r.updated_at) <= $2::timestamptz
    ORDER BY r.created_at DESC LIMIT 1
  ) success ON true
  LEFT JOIN LATERAL (
    SELECT a.* FROM control.alert a
    WHERE a.resolved_at IS NULL AND a.opened_at <= $2::timestamptz AND (
      (a.subject_type='streamline' AND a.subject_id=s.id::text AND a.run_id IS NULL)
      OR (a.run_id=attempt.id AND a.attempt_no=attempt.attempt_no))
    ORDER BY a.opened_at DESC LIMIT 1
  ) incident ON true`;

export async function readSources(
  db: DB,
  filter: { source_key?: string } = {},
): Promise<z.infer<typeof platformSources>> {
  const queried_at = new Date().toISOString();
  const since = new Date(
    Date.parse(queried_at.slice(0, 10)) - 13 * 86400000,
  ).toISOString();
  const collected = await rows(
    db,
    platformSource
      .omit({
        display_name: true,
        brand: true,
        family: true,
        description: true,
      })
      .extend({ provider: z.string().nullable(), layer: z.string() }),
    filter.source_key
      ? sourcesSql.replace("WHERE NOT s.tenant_bound", "WHERE s.source_key=$3")
      : sourcesSql,
    filter.source_key
      ? [since, queried_at, filter.source_key]
      : [since, queried_at],
  );
  const sources = collected.map(
    ({ provider, layer, ...source }): z.infer<typeof platformSource> => {
      const wording = Object.hasOwn(sourceWording, source.source_key)
        ? sourceWording[source.source_key]
        : undefined;
      return {
        ...source,
        display_name: wording?.name ?? provider ?? source.source_key,
        brand: wording?.brand ?? null,
        family:
          wording?.family ??
          (["silver", "gold", "universal"].includes(layer)
            ? "derived"
            : "other"),
        description: wording?.plain ?? sourceFallback,
      };
    },
  );
  sources.sort((a, b) => {
    const difference = BigInt(b.entries_today) - BigInt(a.entries_today);
    return difference === 0n
      ? a.display_name.localeCompare(b.display_name)
      : difference > 0n
        ? 1
        : -1;
  });
  return {
    queried_at,
    sources,
    next_step: "Open /functions to see each source's runs.",
  };
}

export async function readHoldings(
  db: DB,
  since: string,
): Promise<z.infer<typeof holdings>> {
  const queried_at = new Date().toISOString();
  if (
    Date.parse(since) < Date.parse(queried_at) - 90 * 86400000 ||
    Date.parse(since) > Date.parse(queried_at)
  )
    throw new AppError(
      "holdings_window_refused",
      "Choose a start time within the last 90 days. Open /ops/platform to read the past week.",
    );
  const production = await one(
    db,
    z.object({ id: z.uuid(), database: z.string() }),
    "SELECT id,database FROM control.warehouse WHERE is_production",
  );
  const [ingestion, costs, history, first] = await Promise.all([
    one(
      db,
      z.object({ payload: holdings.shape.ingestion }),
      ingestionEnvelopeSql,
      [since, queried_at],
    ),
    rows(
      db,
      holdings.shape.vendor_cost.shape.days.element.extend({
        day: z.iso.date().nullable(),
        current_rows: z.string(),
      }),
      costSql,
      [since, queried_at],
    ),
    rows(
      db,
      inventoryLayer.extend({
        day: z.iso.date(),
        captured_at: z.iso.datetime(),
      }),
      inventoryHistorySql,
      [production.id, since, queried_at],
    ),
    one(db, z.object({ day: z.iso.date().nullable() }), firstInventorySql, [
      production.id,
    ]),
  ]);
  const costTotal = costs.find((row) => row.day === null)!;
  const costDays = costs.flatMap((row) =>
    row.day === null ? [] : [{ ...row, day: row.day }],
  );
  const days: z.infer<typeof holdings>["inventory_history"]["days"] = [];
  for (
    let day = since.slice(0, 10);
    day <= queried_at.slice(0, 10);
    day = new Date(Date.parse(day) + 86400000).toISOString().slice(0, 10)
  ) {
    const layers = history
      .filter((row) => row.day === day)
      .map(({ day: _day, ...layer }) => layer);
    days.push({
      day,
      state: layers.length ? "available" : "unavailable",
      layers,
    });
  }
  return {
    queried_at,
    since,
    warehouse: production.id,
    ingestion: ingestion.payload,
    inventory_history: {
      first_snapshot_day: first.day,
      unavailable_before: first.day,
      days,
      next_step:
        "Open /runbooks/showcase-inventory-failed to check missing snapshots.",
    },
    vendor_cost: {
      cost_cents: costTotal.cost_cents,
      has_current_rows: BigInt(costTotal.current_rows) > 0n,
      label: costTotal.label,
      days: costDays,
      infrastructure_included: false,
      next_step:
        "Infrastructure cost is excluded. Open /ops to inspect vendor usage.",
    },
  };
}

export { readNight, readRelationCounts } from "./platform-night.js";
