import { room, unavailable } from "../../server/room";
import {
  citation,
  signalArrivals,
  signalArrivalsSql,
} from "../../server/reads";
import { Shell } from "../../components/shell";
import { Places } from "../../components/places";
import { Empty } from "../../components/movers";
import { rowsWindow } from "../../lib/music-facts";
import { tending } from "../../server/platform";
export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const view = (await searchParams).view === "waking" ? "waking" : "places";
  const current = await room();
  const result = await signalArrivals().catch(unavailable);
  const tended = await tending(
    current.person,
    result?.value.rows.map((row) => row.song_key) ?? [],
  );
  const p = result
    ? citation(
        result.value,
        `Catalog songs and new songs by established artists that entered tracked lists or charts, ${rowsWindow(result.value.rows)}. New markets rank first.`,
        signalArrivalsSql,
      )
    : undefined;
  return (
    <Shell
      handle={current.handle}
      csrf={current.csrf_token}
      songs={result?.value.rows}
      provenances={[p]}
      cached={result?.state}
      tending={tended}
    >
      {result ? (
        <Places view={view} rows={result.value.rows} provenance={p} />
      ) : (
        <Empty
          title="the spread is out of reach."
          detail="Can't reach the platform."
          href="/songs?view=places"
          action="Retry"
        />
      )}
    </Shell>
  );
}
