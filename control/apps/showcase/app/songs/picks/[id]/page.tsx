import { signCallPlace } from "../../../../server/call-proof";
import { notFound } from "next/navigation";
import { z } from "zod";
import { room } from "../../../../server/room";
import { Shell } from "../../../../components/shell";
import { CallCard } from "../../../../components/call-card";
import { budget } from "../../../../server/read-budget";
import { controlStore } from "../../../../server/clients";
import { callsBoard } from "../../../../server/call-reads";
import { sources } from "../../../../server/platform";
export const dynamic = "force-dynamic";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const current = await room();
  const id = z.uuid().safeParse((await params).id);
  if (!id.success) notFound();
  const [stored] = await budget.run(
    "light",
    () =>
      controlStore()`SELECT week_start::text FROM control.showcase_call WHERE id=${id.data}`,
  );
  if (!stored) notFound();
  const [board, catalog] = await Promise.all([
    callsBoard(z.string().parse(stored.week_start)),
    sources(current.person).catch(() => null),
  ]);
  const call = board.calls.find((c) => c.id === id.data);
  if (!call) notFound();
  const observations = board.observations?.value;
  const row = observations?.rows.find((r) => r.id === call.id) ?? null;
  const provenance = observations
    ? {
        queried_at: observations.queried_at,
        scope: "global",
        inputs: observations.builds,
        sources: row?.source_keys,
        query:
          "Places with chart dates after the pick's UTC day, through the next 28 days, excluding places shown on the card.",
        sql: "Open the place for its chart and proof.",
      }
    : undefined;
  const lastRead =
    catalog?.value.sources.find((s) => s.source_key === "sz_chart")
      ?.last_read ?? null;
  const proofs = (row?.places ?? []).map((p) =>
    signCallPlace(call.id, current.handle, p.locator),
  );
  return (
    <Shell
      handle={current.handle}
      csrf={current.csrf_token}
      provenances={[provenance]}
      cached={board.observations?.state}
    >
      <CallCard
        proofs={proofs}
        call={call}
        row={row}
        provenance={provenance}
        csrf={current.csrf_token}
        mine={current.handle === call.author}
        lastRead={lastRead}
        stale={!!board.observations && board.observations.state !== "live"}
      />
    </Shell>
  );
}
