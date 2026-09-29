import { room, unavailable } from "../../server/room";
import { holdings, tending } from "../../server/platform";
import { rights, citation } from "../../server/reads";
import { Shell } from "../../components/shell";
import { Holdings, type HoldingsView } from "../../components/holdings";
import { Empty } from "../../components/movers";
import type { Provenance } from "../../components/number";
import { holdingsCommand } from "../../server/citation-sql";
import { inventorySql } from "../../server/inventory";
import { awaitingAccess, liveList, liveSources } from "../../lib/rights";
export const dynamic = "force-dynamic";
const views: HoldingsView[] = ["holds", "collection", "rights", "sources"];
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const requested = (await searchParams).view;
  const view = views.find((item) => item === requested) ?? "holds";
  const current = await room();
  const [result, permission, tended] = await Promise.all([
    holdings(current.person).catch(unavailable),
    rights().catch(unavailable),
    tending(current.person),
  ]);
  const p: Provenance | undefined = result
    ? {
        queried_at: result.value.queried_at,
        scope: "global",
        provenance: "live query",
        window: result.value.since,
        query:
          "appearances collected on the regular schedule since Monday at midnight UTC.",
        sql: holdingsCommand(result.value.since),
        copyKind: "command",
        plain: "appearances collected on schedule this week.",
        sources: tended.sources
          .filter(
            (source) =>
              !awaitingAccess.has(source.source_key) &&
              source.days.some((day) => BigInt(day.entries) > 0n),
          )
          .map((source) => source.source_key),
        next: { label: "Collection by day", href: "/sources?view=collection" },
      }
    : undefined;
  // Sources live counts what Rights lists as Collecting now: switched on and read in two weeks.
  const live: Provenance | undefined = p
    ? {
        ...p,
        query:
          "Sources switched on and read within their schedule. Sources waiting for provider access never count.",
        plain: "Sources switched on and read within their schedule.",
        sources: liveList(tended.sources).map((source) => source.source_key),
        next: { label: "See each source", href: "/sources?view=sources" },
      }
    : undefined;
  // The rights register is the platform's own reviewed list: one permission record per source.
  const rp: Provenance | undefined = permission
    ? {
        ...citation(
          permission.value,
          "Each source carries its own learning and resale permission. The team reviews each one, and an unknown permission is not cleared.",
          permission.value.sql,
        ),
        provenance: "reviewed list",
        plain: "From the platform's rights register: one reviewed record per source.",
        next: { label: "See each source", href: "/sources?view=sources" },
      }
    : undefined;
  return (
    <Shell
      handle={current.handle}
      csrf={current.csrf_token}
      provenances={[p, rp]}
      cached={result?.state}
      tending={tended}
    >
      {result && p ? (
        <Holdings
          queries={{ inventory: inventorySql }}
          data={result.value}
          rights={permission?.value.rows[0]}
          provenance={p}
          liveProvenance={live}
          rightsProvenance={rp}
          sources={liveSources(tended.sources)}
          view={view}
        />
      ) : (
        <Empty
          title="the asset is out of reach."
          detail="Can't reach the platform."
          href="/sources"
          action="Retry"
        />
      )}
    </Shell>
  );
}
