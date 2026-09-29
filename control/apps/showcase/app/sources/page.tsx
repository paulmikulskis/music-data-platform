import { room } from "../../server/room";
import { tending } from "../../server/platform";
import { Shell } from "../../components/shell";
import { SourceList } from "../../components/source-list";
export const dynamic = "force-dynamic";
export const metadata = { title: "Music Data Platform · Sources" };
export default async function Sources({
  searchParams,
}: {
  searchParams: Promise<{ credits?: string }>;
}) {
  const current = await room();
  return (
    <Shell
      handle={current.handle}
      csrf={current.csrf_token}
      tending={await tending(current.person)}
    >
      <SourceList credits={(await searchParams).credits === "1"} />
    </Shell>
  );
}
