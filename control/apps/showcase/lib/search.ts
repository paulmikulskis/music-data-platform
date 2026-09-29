import { z } from "zod";
import { callOffer } from "./calls";

export const searchQuery = z.string().trim().min(2).max(100);
export const searchContext = z.object({
  key: z.string(),
  subtitle: z.string().nullable().optional(),
  art_song: z.string().nullable().optional(),
  platform: z.string().optional(),
  artist_id: z.string().optional(),
  wikidata_qid: z.string().nullable().optional(),
  playlist_id: z.string().optional(),
  account_id: z.string().optional(),
  country: z.string().nullable().optional(),
  city: z.string().nullable().optional(),
});
export const searchRow = z.object({
  object_key: z.string(),
  kind: z.enum(["song", "artist", "playlist", "chart", "account", "source"]),
  display_text: z.string(),
  context: searchContext,
  aliases: z.array(z.string()),
  last_seen: z.string(),
  source_keys: z.array(z.string()),
  learning_eligible: z.boolean(),
  resale_permitted: z.boolean(),
});
export const searchResponse = z.object({
  rows: z.array(searchRow).max(20),
  offers: z.record(z.string(), callOffer),
  sources_unavailable: z.boolean(),
  calls_unavailable: z.boolean(),
});
export type SearchRow = z.infer<typeof searchRow>;
// Only a song has a page; every other result opens its Library sheet (lib/library.ts).
// No Library result sends a viewer to the operator console's Explorer.
export function searchHref(row: SearchRow) {
  return row.kind === "song"
    ? `/s/song/${encodeURIComponent(row.context.key)}`
    : null;
}
