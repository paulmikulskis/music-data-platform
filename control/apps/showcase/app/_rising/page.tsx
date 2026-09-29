import { CallsProvider } from "../../components/call-it";
import {
  callOffers,
  earlyProjection,
  moverProjection,
} from "../../server/call-offers";
import { room, unavailable } from "../../server/room";
import {
  movers,
  recentMovers,
  citation,
  earlySignals,
  earlySignalsSql,
  movementReadiness,
} from "../../server/reads";
import { Shell } from "../../components/shell";
import { Rising } from "../../components/rising";
import { Empty } from "../../components/movers";
import {
  rankingLine,
  rowsWindow,
  artistSizeLine,
  artistSizeMeasured,
} from "../../lib/music-facts";
import { tending } from "../../server/platform";
export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  const days = (await searchParams).days === "28" ? 28 : 7;
  const current = await room();
  const result = await (days === 28 ? recentMovers() : movers(50)).catch(
    unavailable,
  );
  // Keep the early feed alongside a short current mover list.
  const fallback = days === 7 && (result?.value.rows.length ?? 0) < 6;
  const [early, ready] = await Promise.all([
    fallback ? earlySignals().catch(unavailable) : null,
    movementReadiness().catch(unavailable),
  ]);
  const rows = result?.value.rows ?? [];
  const moverKeys = new Set(rows.map((row) => row.song_key));
  const earlyRows = (early?.value.rows ?? []).filter(
    (row) => !moverKeys.has(row.song_key),
  );
  const p = result
    ? citation(
        result.value,
        days === 28
          ? "Songs seen moving in the last twenty-eight days. Each card keeps the reason and window from its latest appearance."
          : `Songs moving ${rowsWindow(result.value.rows)}, across independent sources.`,
        "SELECT movement_list, rank, day, song_key, title_text, artist_text, window_days, momentum_score, score_parts, coverage, reason_rule, evidence, ranking_build, learning_eligible, resale_permitted, source_keys FROM marts.mart_top_movers_current WHERE movement_list = 'new_entries' ORDER BY rank;",
      )
    : undefined;
  const e = early
    ? citation(
        early.value,
        `Songs moving on one source, ${rowsWindow(earlyRows)}, grouped by source.`,
        earlySignalsSql,
      )
    : undefined;
  const measured = artistSizeMeasured(ready?.value.rows, "new_entries");
  const stage =
    days === 7 && ready && measured
      ? {
          ...citation(
            ready.value,
            "Artist size readings among songs in the current list. Unknown artists stay in the count. Open a song to inspect its reading.",
            "SELECT stage_known_songs, list_songs FROM marts.mart_readiness WHERE movement_list = 'new_entries';",
          ),
          plain:
            "Songs with an artist size reading, out of all songs in the current list.",
        }
      : undefined;
  const visible = [...rows, ...earlyRows];
  const states = [result?.state, early?.state];
  const cached = states.includes("busy")
    ? "busy"
    : states.includes("cached")
      ? "cached"
      : "live";
  const tended = await tending(
    current.person,
    visible.map((row) => row.song_key),
  );
  const offers = await callOffers(current, [
    ...(result
      ? rows.map((row) => moverProjection(row, result.value.build))
      : []),
    ...(early
      ? earlyRows.map((row) => earlyProjection(row, early.value.build))
      : []),
  ]).catch(() => ({}));
  return (
    <CallsProvider offers={offers}>
      <Shell
        handle={current.handle}
        songs={visible}
        csrf={current.csrf_token}
        // Artist size joins the footer only when its line is on screen.
        provenances={stage ? [p, e, stage] : [p, e]}
        cached={cached}
        tending={tended}
      >
        {result || early ? (
          <Rising
            playsReady={
              ready?.value.rows.some(
                (row) =>
                  row.family === "streams" && Number(row.history_days) >= 7,
              ) ?? false
            }
            stageCoverage={
              days === 7 && measured
                ? artistSizeLine(ready?.value.rows, "new_entries")
                : null
            }
            stageProvenance={stage}
            days={days}
            movers={rows}
            early={
              early
                ? {
                    rows: earlyRows,
                    ranking: rows.length
                      ? null
                      : rankingLine(ready?.value.rows, !!result),
                    provenance: e,
                  }
                : undefined
            }
          />
        ) : (
          <Empty
            title="the songs are out of reach."
            detail="Can't reach the platform."
            href="/songs?view=rising"
            action="Retry"
          />
        )}
      </Shell>
    </CallsProvider>
  );
}
