import { CallsView } from "../../components/calls-view";
import { z } from "zod";
import { room } from "../../server/room";
import { Shell } from "../../components/shell";
import { CallsBoard } from "../../components/calls-board";
import { callsBoard } from "../../server/call-reads";
import { callWeek } from "../../lib/calls";
import { movementReadiness, citation } from "../../server/reads";
import { homeCalls } from "../../server/call-offers";
export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ week?: string; new?: string }>;
}) {
  const current = await room();
  const query = await searchParams;
  const parsed = z.iso.date().safeParse(query.week);
  const week =
    parsed.success &&
    callWeek(new Date(`${parsed.data}T12:00:00Z`)) === parsed.data
      ? parsed.data
      : callWeek();
  const [board, history] = await Promise.all([
    callsBoard(week),
    movementReadiness().catch(() => null),
  ]);
  const observations = board.observations?.value ?? null;
  // An empty week says whether a Home song can be called now, and why not.
  const empty =
    week === callWeek() &&
    !board.calls.some((call) => !call.undone_at && !call.hidden_at);
  const callable = empty ? await homeCalls(current) : "unknown";
  const provenance = observations
    ? {
        queried_at: observations.queried_at,
        scope: "global",
        inputs: observations.builds,
        query: "Shazam places seen since the call, absent from its card.",
        sql: "Open a pick to inspect its places.",
      }
    : undefined;
  const historyProvenance = history
    ? citation(
        history.value,
        "Days with observations for each family.",
        "SELECT family,history_days FROM marts.mart_readiness",
      )
    : undefined;
  const calls =
    query.new === "1"
      ? board.calls.filter(
          (c) =>
            c.author === current.handle &&
            observations?.rows.some(
              (r) => r.id === c.id && BigInt(r.total) > 0n,
            ),
        )
      : board.calls;
  return (
    <Shell
      handle={current.handle}
      csrf={current.csrf_token}
      provenances={[provenance]}
      cached={board.observations?.state}
    >
      <CallsView csrf={current.csrf_token} week={week} />
      <CallsBoard
        calls={calls}
        observations={observations}
        handle={current.handle}
        week={week}
        current={week === callWeek()}
        callable={callable}
        history={history?.value.rows ?? []}
        provenance={provenance}
        historyProvenance={historyProvenance}
      />
    </Shell>
  );
}
