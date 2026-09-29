import { notFound } from "next/navigation";
import { room } from "../../../../../server/room";
import { routeKey } from "../../../../../lib/route-key";
import { markProof } from "../../../../../server/proof-summary";
import { verifyProof } from "../../../../../server/proof-token";
import { viewerPath } from "../../../../../lib/proof-link";
import { Shell } from "../../../../../components/shell";
import { Empty } from "../../../../../components/movers";
import { ProofSummary } from "../../../../../components/proof-summary";
export const dynamic = "force-dynamic";
type Query = Record<string, string | string[] | undefined>;
const one = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;
// The plain proof behind one fact on a song: a movement fact, or one lane on one day.
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ key: string; mark: string }>;
  searchParams: Promise<Query>;
}) {
  const { key: raw, mark } = await params;
  const key = routeKey(raw);
  if (!key) notFound();
  const current = await room();
  const query = await searchParams;
  const song = `/s/song/${encodeURIComponent(key)}`;
  const back = viewerPath(one(query.from)) ?? song;
  const facts = new URLSearchParams();
  for (const name of ["ranking", "history", "day"]) {
    const value = one(query[name]);
    if (value !== undefined) facts.set(name, value);
  }
  const summary = `/s/proof/${encodeURIComponent(key)}/${encodeURIComponent(mark)}?${new URLSearchParams([...facts, ...(back === song ? [] : [["from", back]])])}`;
  // A read that fails is not a missing record: it says so and offers Retry.
  const { view, failed } = await markProof(key, mark, {
    ranking: one(query.ranking),
    history: one(query.history),
    day: one(query.day),
  }).then(
    (found) => ({ view: found, failed: false }),
    () => ({ view: null, failed: true }),
  );
  if (!view)
    return (
      <Shell handle={current.handle} csrf={current.csrf_token} retry={summary}>
        {failed ? (
          <Empty
            title="Proof is out of reach."
            detail="Can't read the source records right now."
            href={summary}
            action="Retry"
          />
        ) : (
          <Empty
            title="Proof is unavailable."
            detail="The saved source records could not be found."
            href={back}
            action={back === song ? "Open the song" : "Go back"}
          />
        )}
      </Shell>
    );
  facts.set("back", summary);
  const engine = `/s/proof/${encodeURIComponent(key)}/${encodeURIComponent(mark)}/engine?${facts}`;
  const refused =
    verifyProof(one(query.showcase_proof) ?? null, current.handle)?.level ===
    "unavailable";
  return (
    <Shell handle={current.handle} csrf={current.csrf_token} retry={summary}>
      <ProofSummary
        view={{
          ...view,
          engine,
          lines: refused
            ? [
                ...view.lines,
                "The Console has no run for these appearances yet.",
              ]
            : view.lines,
        }}
        back={back}
      />
    </Shell>
  );
}
