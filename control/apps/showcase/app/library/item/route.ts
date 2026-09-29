import { session } from "../../../server/session";
import {
  artistFacts,
  chartFacts,
  playlistFacts,
  sourceFacts,
} from "../../../server/library";
import { libraryItemQuery } from "../../../lib/library";

export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "private, no-store" };
// One Library result's viewer view: a playlist, chart, artist or source, never the operator console.
export async function GET(request: Request) {
  const current = await session();
  if (!current)
    return Response.json(
      { next_step: "Open /sign-in." },
      { status: 401, headers },
    );
  const url = new URL(request.url);
  const query = libraryItemQuery.safeParse({
    kind: url.searchParams.get("kind"),
    key: url.searchParams.get("key"),
  });
  if (!query.success)
    return Response.json(
      { next_step: "Search the Library again." },
      { status: 400, headers },
    );
  const { kind, key } = query.data;
  try {
    const item =
      kind === "playlist"
        ? await playlistFacts(key)
        : kind === "chart"
          ? await chartFacts(key)
          : kind === "artist"
            ? await artistFacts(key)
            : await sourceFacts(current.person, key);
    if (!item)
      return Response.json(
        { next_step: "Search the Library again." },
        { status: 404, headers },
      );
    return Response.json(item, { headers });
  } catch {
    return Response.json(
      { next_step: "Retry, or open /ops." },
      { status: 503, headers },
    );
  }
}
