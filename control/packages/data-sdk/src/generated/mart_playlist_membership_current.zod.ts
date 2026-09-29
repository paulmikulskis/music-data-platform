// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_playlist_membership_current = z.object({
  "platform": z.string(),
  "playlist_id": z.string(),
  "variant": z.string(),
  "stream": z.string(),
  "occurrence_key": z.string(),
  "occurrence_inferred": z.boolean().nullable(),
  "interval_id": z.string().nullable(),
  "position": bigintWire.nullable(),
  "platform_track_id": z.string().nullable(),
  "item_type": z.string().nullable(),
  "platform_item_id": z.string().nullable(),
  "featured_track_id": z.string().nullable(),
  "observed_at": timestampWire.nullable(),
  "coverage": z.string().nullable(),
  "extent": z.string().nullable(),
  "snapshot_id": z.string().nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_playlist_membership_current = z.infer<typeof mart_playlist_membership_current>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_playlist_membership_current.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_playlist_membership_current.parse(row);
export const metadata = {"name":"mart_playlist_membership_current","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["platform","playlist_id","variant","stream","occurrence_key","occurrence_inferred","interval_id","position","platform_track_id","item_type","platform_item_id","featured_track_id","observed_at","coverage","extent","snapshot_id","learning_eligible","resale_permitted","source_keys"],"grain":["platform","playlist_id","variant","occurrence_key"],"grain_types":["text","text","text","text"],"time_columns":["observed_at"]};
