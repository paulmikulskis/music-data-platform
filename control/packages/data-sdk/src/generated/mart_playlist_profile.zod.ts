// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_playlist_profile = z.object({
  "platform": z.string(),
  "playlist_id": z.string(),
  "variant": z.string(),
  "observed_at": timestampWire,
  "snapshot_id": z.string(),
  "title": z.string().nullable(),
  "description": z.string().nullable(),
  "owner_id": z.string().nullable(),
  "owner_name": z.string().nullable(),
  "followers": bigintWire.nullable(),
  "follower_change": bigintWire.nullable(),
  "track_count_reported": bigintWire.nullable(),
  "stream": z.string(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
  "owner_class": z.string().nullable(),
});
export type mart_playlist_profile = z.infer<typeof mart_playlist_profile>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_playlist_profile.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_playlist_profile.parse(row);
export const metadata = {"name":"mart_playlist_profile","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["platform","playlist_id","variant","observed_at","snapshot_id","title","description","owner_id","owner_name","followers","follower_change","track_count_reported","stream","learning_eligible","resale_permitted","source_keys","owner_class"],"grain":["platform","playlist_id","variant","stream","observed_at","snapshot_id"],"grain_types":["text","text","text","text","timestamp","text"],"time_columns":["observed_at"]};
