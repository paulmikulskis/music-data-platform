import { z } from "zod";
import { room } from "../../../../../server/room";
import { cycleProof } from "../../../../../server/proof-summary";
import { verifyProof } from "../../../../../server/proof-token";
import { viewerPath } from "../../../../../lib/proof-link";
import { Shell } from "../../../../../components/shell";
import { Empty } from "../../../../../components/movers";
import { ProofSummary } from "../../../../../components/proof-summary";
export const dynamic = "force-dynamic";
type Query = Record<string, string | string[] | undefined>;
const one = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;
const relationName = z.string().regex(/^(marts\.)?mart_[a-z0-9_]+$/);
const sourceKey = z.string().regex(/^[a-z0-9_]{1,64}$/);
// The plain proof behind a number: the song's own entries when a song is named, else its sources.
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ cycle: string }>;
  searchParams: Promise<Query>;
}) {
  const current = await room();
  const query = await searchParams;
  const cycle = z.uuid().safeParse((await params).cycle);
  const relation = relationName.safeParse(one(query.relation));
  const song = z.string().min(1).max(200).safeParse(one(query.song));
  const keys = (one(query.sources) ?? "")
    .split(",")
    .filter((key) => sourceKey.safeParse(key).success)
    .slice(0, 6);
  const back =
    viewerPath(one(query.from)) ??
    (song.success ? `/s/song/${encodeURIComponent(song.data)}` : "/");
  const facts = new URLSearchParams();
  if (relation.success) facts.set("relation", relation.data);
  if (song.success) facts.set("song", song.data);
  if (keys.length) facts.set("sources", keys.join(","));
  const summary = cycle.success
    ? `/s/proof/cycle/${cycle.data}?${new URLSearchParams([...facts, ["from", back]])}`
    : back;
  // A read that fails is not a missing record: it says so and offers Retry.
  const { view, failed } = cycle.success
    ? await cycleProof(
        current.person,
        relation.success ? relation.data.replace(/^marts\./, "") : "",
        song.success ? song.data : null,
        keys,
      ).then(
        (found) => ({ view: found, failed: false }),
        () => ({ view: null, failed: true }),
      )
    : { view: null, failed: false };
  if (!cycle.success || !view)
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
            action="Go back"
          />
        )}
      </Shell>
    );
  const engine = `/s/proof/cycle/${cycle.data}/engine?${new URLSearchParams([...facts, ["back", summary]])}`;
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
