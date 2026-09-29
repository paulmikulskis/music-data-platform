import "server-only";
import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
import { lineage, resolveFactPath } from "../lib/lineage";
import { controlStore, warehouse } from "./clients";
import { budget } from "./read-budget";
import { buildStamp } from "./relation-counts";
import type { TraceSelection } from "../lib/trace";
const receipt = z.object({
  source_key: z.string(),
  target_table: z.string(),
  run_id: z.string().uuid(),
  dump_id: z.string().uuid(),
});
const cache = budget.cache<z.infer<typeof receipt>[]>();
const verified = budget.cache<boolean>();
// Proof reads project lineage ids only, never source payloads or personal columns.
export async function traceReceipts(selection: TraceSelection, row: unknown) {
  const selected = {
    entry: selection.entry,
    song_key: selection.song ?? "",
    ranking_build: selection.ranking ?? "",
  };
  const resolved = resolveFactPath(selected, row);
  const contributions = [];
  // A bounded sample can establish contributors, never a total or an exhaustive path.
  for (const locator of resolved.evidence.slice(0, 5)) {
    const relation = `marts.${locator.relation.replace(/^marts\./, "")}`;
    const rule = lineage.entries
      .find((entry) => entry.id === selection.entry)
      ?.selectors.find((rule) => rule.relation === relation);
    if (!rule) continue;
    const join =
      relation === "marts.mart_playlist_events" ||
      relation === "marts.mart_playlist_profile"
        ? {
            table: "explore_staging.stg_playlist__snapshots",
            raw: "raw.playlist_snapshots",
            on: "s.snapshot_id=a.snapshot_id AND s.platform=a.platform AND s.playlist_id=a.playlist_id AND s.variant=a.variant",
          }
        : relation === "marts.mart_shazam_chart_daily"
          ? {
              table: "explore_staging.stg_shazam__chart_entries",
              raw: "raw.shazam_chart_entries",
              on: "s.chart=a.chart AND s.chart_date=a.chart_date AND s.position=a.position AND s.apple_song_id=a.apple_song_id",
            }
          : null;
    if (!join) continue;
    const result = await cache
      .read(
        `trace-receipts:${JSON.stringify(locator)}`,
        "heavy",
        () =>
          warehouse().begin(
            "isolation level repeatable read read only",
            async (tx) => {
              await tx.unsafe(
                `LOCK TABLE ${relation}, ${join.table} IN ACCESS SHARE MODE`,
              );
              const [value] =
                await tx`SELECT stamp || jsonb_build_object('close_no',stamp->>'close_no') AS stamp FROM (SELECT catalog.snapshot_stamp(${relation}) AS stamp) s`;
              const stamp = z
                .object({ stamp: buildStamp.nullable() })
                .parse(encodeWire(value)).stamp;
              if (
                !stamp ||
                stamp.cycle_id !== locator.input_build.cycle_id ||
                stamp.close_no !== locator.input_build.close_no ||
                stamp.built_at !==
                  buildStamp.parse(locator.input_build).built_at
              )
                return [];
              const result = await tx.unsafe(
                `SELECT DISTINCT s._source_key AS source_key, '${join.raw}' AS target_table,
        s._run_id::text AS run_id,s._dump_id::text AS dump_id FROM ${relation} a JOIN ${join.table} s ON ${join.on}
        WHERE ${rule.row_keys.map((key, i) => `a."${key}"=$${i + 1}`).join(" AND ")} LIMIT 5`,
                rule.row_keys.map((key) => locator.row_key[key]),
              );
              return receipt.array().parse(encodeWire(result));
            },
          ),
        10000,
      )
      .catch(() => null);
    for (const receipt of result?.value ?? []) {
      const loaded = await verified
        .read(
          `trace-load:${JSON.stringify([locator.input_build, receipt])}`,
          "light",
          async () => {
            const rows = await controlStore()`SELECT 1 FROM control.dump d
          JOIN control.run r ON r.id=d.run_id AND r.scope='global'
          JOIN control.streamline s ON s.id=r.streamline_id AND s.source_key=${receipt.source_key}
          JOIN control.load l ON l.dump_id=d.id AND l.status='loaded' AND l.target_table=${receipt.target_table}
          JOIN control.warehouse w ON w.id=l.warehouse_id AND w.is_production
          JOIN control.cycle c ON c.id=${locator.input_build.cycle_id}::uuid AND c.scope='global' AND c.status='closed'
          WHERE d.id=${receipt.dump_id}::uuid AND d.run_id=${receipt.run_id}::uuid
          AND d.scope='global' AND d.quarantined_at IS NULL AND d.rejected_at IS NULL
          AND ((c.manifest_mode='stamp' AND c.close_no=${locator.input_build.close_no}::bigint AND d.close_no<=c.close_no)
            OR (c.manifest_mode='list' AND EXISTS (SELECT 1 FROM control.cycle_input i WHERE i.cycle_id=c.id AND i.dump_id=d.id)))
          LIMIT 1`;
            return rows.length === 1;
          },
          10000,
        )
        .catch(() => null);
      if (loaded?.value)
        contributions.push({
          song_key: selected.song_key,
          ranking_build: selected.ranking_build,
          locator,
          receipt,
        });
    }
  }
  return contributions;
}
