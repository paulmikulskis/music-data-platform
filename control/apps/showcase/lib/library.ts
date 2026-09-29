import { z } from "zod";
import { platformSource } from "@mdp/contracts/platform";
import type { SearchRow } from "./search";
import { placeName } from "./places";

// What a Library result opens inside the showcase: a playlist, a chart, an artist or a source.
export const libraryKind = z.enum(["playlist", "chart", "artist", "source"]);
export const libraryItemQuery = z.object({
  kind: libraryKind,
  key: z
    .string()
    .min(3)
    .max(200)
    .regex(/^[^:\s]+:\S+$/),
});
const place = z.object({ name: z.string(), lat: z.number(), lon: z.number() });
export const libraryItem = z.object({
  kind: libraryKind,
  title: z.string(),
  // One plain line under the title: who runs the list, or where the chart is.
  subtitle: z.string().nullable(),
  // At most two headline numbers, already formatted.
  facts: z.array(z.object({ value: z.string(), label: z.string() })).max(2),
  songs_label: z.string().nullable(),
  songs: z
    .array(
      z.object({
        key: z.string().nullable(),
        title: z.string(),
        note: z.string().nullable(),
      }),
    )
    .max(5),
  // An empty list says why, in one line.
  empty: z.string().nullable(),
  places: z.array(place).max(1),
  // Where the facts came from, in plain words.
  read: z.string(),
  source: platformSource.optional(),
  link: z.object({ label: z.string(), href: z.url() }).nullable(),
  // Only a source card offers the operator console, and only as a secondary link.
  engine: z.string().nullable(),
  // An artist's first seen day, for the artist card.
  first_seen: z.string().nullable(),
});
export type LibraryItem = z.infer<typeof libraryItem>;

// The Library sheet for a playlist, chart or source result; songs open their page.
export function libraryItemPath(row: SearchRow) {
  const kind = row.kind === "account" ? null : row.kind;
  if (kind === null || kind === "song") return null;
  const key =
    kind === "artist"
      ? row.context.platform && row.context.artist_id
        ? `${row.context.platform}:${row.context.artist_id}`
        : null
      : kind === "source"
        ? `source:${row.context.key}`
        : row.context.key;
  return key
    ? `/library/item?kind=${kind}&key=${encodeURIComponent(key)}`
    : null;
}

// Search keeps chart names as the platform writes them (tokyo Shazam top-50); viewers read
// Tokyo · Shazam top 50.
export function chartTitle(place: string, type: string | null) {
  const kind = (type ?? "chart").replaceAll("-", " ");
  return `${place} · Shazam ${kind}`;
}
// A chart result's plain name from its key: shazam:<type>:<country>[:<city>], or Billboard's.
export function chartLabel(row: SearchRow) {
  const [family, type = null, country = "", city = null] =
    row.context.key.split(":");
  if (family !== "shazam") return row.display_text;
  return chartTitle(placeName({ country, city }), type);
}
