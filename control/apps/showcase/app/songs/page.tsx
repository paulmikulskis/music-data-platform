import Rising from "../_rising/page";
import Places from "../_places/page";
import Picks from "../_picks/page";
import Friday from "../draft/page";
export const dynamic = "force-dynamic";
export const metadata = { title: "Music Data Platform · Songs" };
export default async function Songs({
  searchParams,
}: {
  searchParams: Promise<{
    view?: string;
    days?: string;
    week?: string;
    new?: string;
    board?: string;
    tray?: string;
  }>;
}) {
  const query = await searchParams;
  if (query.view === "picks") return <Picks searchParams={searchParams} />;
  if (query.view === "friday") return <Friday searchParams={searchParams} />;
  if (query.view === "places" || query.view === "waking")
    return <Places searchParams={searchParams} />;
  return <Rising searchParams={searchParams} />;
}
