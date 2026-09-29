import "server-only";
import { z } from "zod";
import type { ProofContext } from "./proof-token";
import type { Person } from "@mdp/showcase-auth";
import { budget } from "./read-budget";
import { controlClient } from "./clients";
import { mart, playlistReceipt } from "./reads";
import type { Locator } from "./models";
export type ProofResult = { level: ProofContext["level"]; href: string };
const proofCache = budget.cache<Awaited<ReturnType<typeof mart>> | null>();
export async function resolveProof(
  person: Person,
  mark: Locator,
): Promise<ProofResult> {
  const captured = mark.input_build;
  if (!captured.cycle_id || !captured.built_at)
    return { level: "unavailable", href: "/ops" };
  const relation = mark.relation.replace(/^marts\./, "");
  // These SDK calls read the exact row and its build in one transaction.
  let row: Record<string, unknown> | undefined;
  let matches = false;
  try {
    const result = await proofCache.read(
      `proof:${relation}:${JSON.stringify(mark.row_key)}:${captured.built_at}`,
      "heavy",
      async () => {
        switch (relation) {
          case "mart_song_day":
            return mart("mart_song_day", { limit: 1, filters: mark.row_key });
          case "mart_playlist_events":
            return mart("mart_playlist_events", {
              limit: 1,
              filters: mark.row_key,
            });
          case "mart_shazam_chart_daily":
            return mart("mart_shazam_chart_daily", {
              limit: 1,
              filters: mark.row_key,
            });
          case "mart_track_daily_streams":
            return mart("mart_track_daily_streams", {
              limit: 1,
              filters: mark.row_key,
            });
          default:
            return null;
        }
      },
      0,
    );
    const page = result.value;
    matches =
      result.state === "live" &&
      !!page?.build.stamped &&
      page.build.cycle_id === captured.cycle_id &&
      page.build.close_no === captured.close_no &&
      Date.parse(page.build.built_at!) === Date.parse(captured.built_at);
    row = matches ? page?.rows[0] : undefined;
  } catch {
    /* Captured cycle proof remains available when the current row cannot be read. */
  }
  const runIds =
    typeof row?._run_ids === "string"
      ? z.array(z.string()).parse(JSON.parse(row._run_ids))
      : [];
  const exactRun = runIds.find((id) => /^[0-9a-f-]{36}$/i.test(id));
  if (exactRun) return { level: "row", href: `/runs/${exactRun}` };
  if (
    typeof row?.snapshot_id === "string" &&
    typeof row.platform === "string" &&
    typeof row.playlist_id === "string"
  ) {
    const receipt = await playlistReceipt(
      row.snapshot_id,
      row.platform,
      row.playlist_id,
    ).catch(() => null);
    const run = receipt?.value.rows[0]?.run_id;
    if (run && /^[0-9a-f-]{36}$/i.test(run))
      return { level: "row", href: `/runs/${run}` };
  }
  return resolveCycleProof(
    person,
    captured.cycle_id,
    relation,
    matches ? "cycle" : "partial",
    mark.component,
  );
}

export async function resolveCycleProof(
  person: Person,
  cycle: string,
  relation = "",
  level: "cycle" | "partial" = "cycle",
  component = "",
): Promise<ProofResult> {
  // A relation identifies its source. Song-day combines sources, so its lane narrows the trail.
  const source = relation === "mart_song_day" ? component : relation;
  const contributing = /shazam/.test(source)
    ? /shazam/
    : /stream/.test(source)
      ? /playlist|track.*stream|stream.*track/
      : /playlist/.test(source)
        ? /playlist/
        : null;
  const dumps: Record<string, unknown>[] = [];
  const receipts: Record<string, unknown>[] = [];
  let after:
    | {
        dumps?: string | null;
        receipts?: string | null;
        requests?: string | null;
      }
    | undefined;
  for (let page = 0; page < 20; page++) {
    const input = { cycle_id: cycle, limit: 100, after };
    const chain = await budget.run("light", () =>
      controlClient(person).lineage.chain(input),
    );
    dumps.push(...chain.dumps);
    receipts.push(...chain.receipts);
    const receipt = receipts.find(
      (r) =>
        (!contributing || contributing.test(String(r.target_table))) &&
        dumps.some((d) => d.id === r.dump_id),
    );
    const dump = receipt
      ? dumps.find((d) => d.id === receipt.dump_id)
      : undefined;
    if (typeof dump?.run_id === "string")
      return {
        level,
        href: `/runs/${encodeURIComponent(dump.run_id)}`,
      };
    after = chain.next;
    if (!after || !Object.values(after).some(Boolean)) break;
  }
  return { level: "unavailable", href: "/ops" };
}
