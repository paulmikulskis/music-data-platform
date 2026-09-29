import "server-only";
import { encodeWire } from "@mdp/data-sdk";
import { z } from "zod";
import { warehouse } from "./clients";
import { budget } from "./read-budget";
import { peekQuery } from "./peek-query";
import { lineageCounts } from "./lineage-counts";
import { buildStamp, relationBuildKey } from "./relation-counts";
import type { PeekView } from "../lib/trace";
const cache = budget.cache<Omit<PeekView, "cache_state" | "saved_at">>();
const cell = z.union([z.string(), z.number(), z.boolean(), z.null()]);
export async function peek(
  value: unknown,
  expected?: string,
): Promise<PeekView> {
  const query = peekQuery(value);
  const empty: PeekView = {
    state: "unavailable",
    cache_state: "live",
    saved_at: null,
    columns: [],
    rows: [],
    total: null,
    captured_at: null,
    sql: null,
  };
  if (!query) return empty;
  const counts = await lineageCounts([query.input.relation]).catch(() => []);
  const result = await cache.read(
    `peek:${JSON.stringify(query.input)}:${expected ?? "current"}:${counts[0]?.build_key ?? "unknown"}`,
    "heavy",
    async () =>
      warehouse().begin(
        "isolation level repeatable read read only",
        async (tx) => {
          await tx.unsafe(
            `LOCK TABLE ${query.input.relation} IN ACCESS SHARE MODE`,
          );
          const [stampRow] =
            await tx`SELECT stamp || jsonb_build_object('close_no',stamp->>'close_no') AS stamp FROM (SELECT catalog.snapshot_stamp(${query.input.relation}) AS stamp) s`;
          const stamp = z
            .object({ stamp: buildStamp.nullable() })
            .parse(encodeWire(stampRow)).stamp;
          const key = stamp ? relationBuildKey(stamp) : null;
          if (expected && expected !== key)
            return { ...empty, state: "changed" as const };
          const raw = await tx.unsafe(query.sql, query.values);
          // Project again before caching: unexpected driver fields never reach the browser.
          const rows = z
            .array(z.record(z.string(), z.unknown()))
            .parse(encodeWire(raw))
            .map((row) =>
              Object.fromEntries(
                query.spec.columns.map((column) => [
                  column,
                  cell.parse(row[column]),
                ]),
              ),
            );
          const count = counts.find((item) => item.build_key === key);
          return {
            state: "ready" as const,
            columns: query.spec.columns,
            rows,
            captured_at: count?.captured_at ?? null,
            total:
              Object.keys(query.input.filters).length === 0 &&
              query.spec.policy === "public_metadata"
                ? (count?.row_count ?? null)
                : null,
            sql: query.prefill,
          };
        },
      ),
    10000,
  );
  return {
    ...result.value,
    cache_state: result.state,
    saved_at: result.savedAt,
  };
}
