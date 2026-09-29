// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_track_playlist_followers = z.object({
  "day": dateWire,
  "platform": z.string(),
  "owner_class": z.string(),
  "mb_recording_gid": z.string(),
  "playlist_followers": bigintWire.nullable(),
  "list_count": bigintWire,
  "best_position": bigintWire.nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_track_playlist_followers = z.infer<typeof mart_track_playlist_followers>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_track_playlist_followers.parse(encodeMartRow(row,["day"]));
export const decode = (row: unknown) => mart_track_playlist_followers.parse(row);
export const metadata = {"name":"mart_track_playlist_followers","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["day","platform","owner_class","mb_recording_gid","playlist_followers","list_count","best_position","learning_eligible","resale_permitted","source_keys"],"grain":[],"grain_types":[],"time_columns":["day"]};
