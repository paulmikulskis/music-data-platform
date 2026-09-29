import "server-only";
import { readLinks } from "./links";
import type { ContractRouterClient } from "@orpc/contract";
import type { contract } from "@mdp/contracts";
import type { Person } from "@mdp/showcase-auth";
import { budget } from "./read-budget";
import { controlClient, warehouse } from "./clients";
import { readInventory } from "./inventory";
import { songArtists, trackedSongs } from "./reads";
import { artMissing } from "./art";
type Platform = ContractRouterClient<typeof contract>["platform"];
export type Events = Awaited<ReturnType<Platform["events"]>>;
export type Event = Events["events"][number];
export type Holdings = Awaited<ReturnType<Platform["holdings"]>> & {
  inventory_now: {
    queried_at: string;
    layers: Awaited<ReturnType<typeof readInventory>>;
  };
};
const summaryCache = budget.cache<Awaited<ReturnType<Platform["holdings"]>>>();
const inventoryCache = budget.cache<Holdings["inventory_now"]>();
export function holdingsSummary(person: Person) {
  const since = new Date();
  since.setUTCHours(0, 0, 0, 0);
  since.setUTCDate(since.getUTCDate() - ((since.getUTCDay() + 6) % 7));
  return summaryCache.read(
    `person:${person.api_key_id}:holdings:${since.toISOString()}`,
    "heavy",
    () =>
      controlClient(person).platform.holdings({ since: since.toISOString() }),
  );
}
export async function holdings(person: Person) {
  const [summary, inventory] = await Promise.all([
    holdingsSummary(person),
    inventoryCache.read("global:inventory", "heavy", async () => ({
      layers: await readInventory(warehouse()),
      queried_at: new Date().toISOString(),
    })),
  ]);
  return {
    ...summary,
    state:
      summary.state === "busy" || inventory.state === "busy"
        ? ("busy" as const)
        : summary.state === "cached" || inventory.state === "cached"
          ? ("cached" as const)
          : ("live" as const),
    value: { ...summary.value, inventory_now: inventory.value },
  };
}
export function events(person: Person, after?: string) {
  return budget.run("essential", () =>
    controlClient(person).platform.events({ after, limit: 500 }),
  );
}
const sourcesCache = budget.cache<Awaited<ReturnType<Platform["sources"]>>>();
// Each source's counts, read through the person's admin key. Control-only, so a light read.
export async function sources(person: Person) {
  return sourcesCache.read(
    `person:${person.api_key_id}:sources`,
    "light",
    () => controlClient(person).platform.sources({}),
    60000,
  );
}
// What every hover and reveal on a page counts from, plus the lead artist of each song shown.
// Missing parts stay unmeasured.
export async function tending(person: Person, songs: string[] = []) {
  const [read, tracked, artists] = await Promise.all([
    sources(person).catch(() => null),
    trackedSongs().catch(() => null),
    songs.length ? songArtists(songs).catch(() => null) : null,
  ]);
  const links = readLinks();
  return {
    sources: read?.value.sources ?? [],
    links: [
      links.get("sources-catalog"),
      ...(read?.value.sources ?? []).flatMap((source) => [
        links.get("sources-reader-code", source.source_key),
        links.get("proof-open-console", source.source_key),
      ]),
    ].filter((link) => link !== null),
    songs: tracked?.value.rows[0]?.songs ?? null,
    artists: Object.fromEntries(
      (artists?.value.rows ?? []).map((row) => [row.song_key, row]),
    ),
    // Songs whose cover the server already knows is missing render the placeholder at once.
    missingArt: songs.filter((song) => artMissing(song)),
  };
}
