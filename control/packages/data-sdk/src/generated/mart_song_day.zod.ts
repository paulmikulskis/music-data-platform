// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_song_day = z.object({
  "song_key": z.string(),
  "title_text": z.string().nullable(),
  "artist_text": z.string().nullable(),
  "day": dateWire,
  "editorial_adds": z.number().nullable(),
  "algorithmic_adds": z.number().nullable(),
  "removes": bigintWire.nullable(),
  "playlist_followers": bigintWire.nullable(),
  "list_count": bigintWire.nullable(),
  "shazam_cities": bigintWire.nullable(),
  "shazam_countries": bigintWire.nullable(),
  "shazam_charts": bigintWire.nullable(),
  "shazam_new_entries": bigintWire.nullable(),
  "best_position": z.number().int().nullable(),
  "shazam_best_position": z.number().int().nullable(),
  "stream_rate": z.number().nullable(),
  "playlists_observed": z.boolean(),
  "shazam_observed": z.boolean(),
  "streams_observed": z.boolean(),
  "stream_interval_hours": z.number().nullable(),
  "billboard_position": z.number().int().nullable(),
  "billboard_weeks_on_chart": z.number().int().nullable(),
  "billboard_debut": z.boolean().nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_song_day = z.infer<typeof mart_song_day>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_song_day.parse(encodeMartRow(row,["day"]));
export const decode = (row: unknown) => mart_song_day.parse(row);
export const metadata = {"name":"mart_song_day","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["song_key","title_text","artist_text","day","editorial_adds","algorithmic_adds","removes","playlist_followers","list_count","shazam_cities","shazam_countries","shazam_charts","shazam_new_entries","best_position","shazam_best_position","stream_rate","playlists_observed","shazam_observed","streams_observed","stream_interval_hours","billboard_position","billboard_weeks_on_chart","billboard_debut","learning_eligible","resale_permitted","source_keys"],"grain":["song_key","day"],"grain_types":["text","date"],"time_columns":["day"]};
