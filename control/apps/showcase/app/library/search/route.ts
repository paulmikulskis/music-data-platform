import { session } from "../../../server/session";
import { librarySearch } from "../../../server/reads";
import { sources } from "../../../server/platform";
import { callOffers, searchProjection } from "../../../server/call-offers";
import { searchQuery, type SearchRow } from "../../../lib/search";

export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "private, no-store" };
export async function GET(request: Request) {
  const current = await session();
  if (!current)
    return Response.json(
      { next_step: "Open /sign-in." },
      { status: 401, headers },
    );
  const query = searchQuery.safeParse(
    new URL(request.url).searchParams.get("q"),
  );
  if (!query.success)
    return Response.json(
      { next_step: "Type 2 to 100 characters." },
      { status: 400, headers },
    );
  try {
    const [read, collected] = await Promise.all([
      librarySearch(query.data, request.signal),
      sources(current.person).catch(() => null),
    ]);
    request.signal.throwIfAborted();
    const matches: SearchRow[] = (collected?.value.sources ?? [])
      .filter((source) =>
        `${source.display_name} ${source.source_key}`
          .toLowerCase()
          .includes(query.data.toLowerCase()),
      )
      .map((source) => ({
        object_key: `source:${source.source_key}`,
        kind: "source",
        display_text: source.display_name,
        context: { key: source.source_key },
        aliases: [],
        last_seen: "",
        source_keys: [],
        learning_eligible: false,
        resale_permitted: false,
      }));
    const rows = [
      ...read.rows.slice(0, 20 - Math.min(matches.length, 3)),
      ...matches.slice(0, 3),
    ];
    const offers = await callOffers(
      current,
      rows
        .filter((row) => row.kind === "song")
        .map((row) => searchProjection(row, read.build)),
    ).catch(() => null);
    return Response.json(
      {
        rows,
        offers: offers ?? {},
        sources_unavailable: !collected,
        calls_unavailable: offers === null,
      },
      { headers },
    );
  } catch {
    return Response.json(
      { next_step: "Retry, or open /ops." },
      { status: 503, headers },
    );
  }
}
