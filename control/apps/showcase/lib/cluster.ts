import type { Locator, SongDay } from "../server/models";
import { sourceKeys } from "./source-keys";

const methodNames: Record<string, string> = {
  isrc_crosswalk: "same ISRC",
  apple_variant: "Apple versions",
  title_artist_duration: "same title and artist",
};

// One sentence for a matched song, the same on its card and on its page.
export function matchedLine(copies: number, methods: string[]) {
  const named = [...new Set(methods)]
    .map((method) => methodNames[method] ?? "reviewed match")
    .join(", ");
  return `${copies} copies matched${named ? `: ${named}` : ""}.`;
}

export function clusterLine(evidence: Locator[]) {
  const matched = evidence.find(
    (entry) => (entry.member_song_keys?.length ?? 0) > 1,
  );
  if (!matched?.member_song_keys) return null;
  return matchedLine(
    matched.member_song_keys.length,
    matched.cluster_methods ?? [],
  );
}

const sum = (values: (number | null)[]) =>
  values.some((value) => value !== null)
    ? values.reduce<number>((total, value) => total + (value ?? 0), 0)
    : null;
const bigSum = (values: (string | null)[]) =>
  values.some((value) => value !== null)
    ? values.reduce((total, value) => total + BigInt(value ?? 0), 0n).toString()
    : null;
const bigMax = (values: (string | null)[]) =>
  values
    .filter((value) => value !== null)
    .map(BigInt)
    .reduce<bigint | null>((most, value) => (most === null || value > most ? value : most), null)
    ?.toString() ?? null;

// A matched song's days, one row per day under the page's key. Adds, followers and plays add up
// across its copies; cities are the most any one copy reached, so one city never counts twice.
// A copy with no row that day adds nothing; a lane with no copy read that day stays empty.
export function groupDays(key: string, lists: SongDay[][]): SongDay[] {
  const byDay = new Map<string, SongDay[]>();
  for (const list of lists)
    for (const row of list) byDay.set(row.day, [...(byDay.get(row.day) ?? []), row]);
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, rows]) => ({
      song_key: key,
      day,
      title_text: rows.find((row) => row.title_text)?.title_text ?? null,
      artist_text: rows.find((row) => row.artist_text)?.artist_text ?? null,
      learning_eligible: rows.every((row) => row.learning_eligible),
      resale_permitted: rows.every((row) => row.resale_permitted),
      source_keys: JSON.stringify(
        [...new Set(rows.flatMap((row) => sourceKeys(row.source_keys)))].sort(),
      ),
      editorial_adds: sum(rows.map((row) => row.editorial_adds)),
      algorithmic_adds: sum(rows.map((row) => row.algorithmic_adds)),
      playlist_followers: bigSum(rows.map((row) => row.playlist_followers)),
      shazam_cities: bigMax(rows.map((row) => row.shazam_cities)),
      stream_rate: sum(rows.map((row) => row.stream_rate)),
    }));
}
