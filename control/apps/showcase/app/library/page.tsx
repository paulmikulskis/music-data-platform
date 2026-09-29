import { room } from "../../server/room";
import { Shell } from "../../components/shell";
export const dynamic = "force-dynamic";
export default async function Page() {
  const current = await room();
  return (
    <Shell handle={current.handle} csrf={current.csrf_token}>
      <h1>Search</h1>
      <p>Find a song, artist or place.</p>
      <a className="primary" href="/songs?view=rising">
        Explore songs
      </a>
    </Shell>
  );
}
