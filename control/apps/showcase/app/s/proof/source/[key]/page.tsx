import { z } from "zod";
import { room } from "../../../../../server/room";
import { cycleProof } from "../../../../../server/proof-summary";
import { viewerPath } from "../../../../../lib/proof-link";
import { signProof } from "../../../../../server/proof-token";
import { Shell } from "../../../../../components/shell";
import { Empty } from "../../../../../components/movers";
import { ProofSummary } from "../../../../../components/proof-summary";
export const dynamic = "force-dynamic";
const sourceKey = z.string().regex(/^[a-z0-9_]{1,64}$/);
// One source's plain summary, for entries that have no saved collection time.
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ key: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const current = await room();
  const query = await searchParams;
  const from = typeof query.from === "string" ? query.from : undefined;
  const back = viewerPath(from) ?? "/";
  const key = sourceKey.safeParse((await params).key);
  const summary = key.success
    ? `/s/proof/source/${key.data}?${new URLSearchParams({ from: back })}`
    : back;
  const view = key.success
    ? await cycleProof(current.person, "", null, [key.data]).catch(() => null)
    : null;
  if (!key.success || !view)
    return (
      <Shell handle={current.handle} csrf={current.csrf_token} retry={summary}>
        <Empty
          title="Proof is unavailable."
          detail="The source could not be found."
          href={back}
          action="Go back"
        />
      </Shell>
    );
  return (
    <Shell handle={current.handle} csrf={current.csrf_token} retry={summary}>
      <ProofSummary
        sourceKey={key.data}
        view={{
          ...view,
          engine: `/functions/${encodeURIComponent(key.data)}?${new URLSearchParams(
            {
              showcase_proof: signProof({
                level: "source",
                song: "",
                handle: current.handle,
                back: summary,
              }),
            },
          )}`,
        }}
        back={back}
      />
    </Shell>
  );
}
