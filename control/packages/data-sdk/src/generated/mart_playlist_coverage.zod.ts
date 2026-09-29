// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_playlist_coverage = z.object({
  "platform": z.string(),
  "playlist_id": z.string(),
  "variant": z.string(),
  "snapshot_id": z.string(),
  "observed_at": timestampWire,
  "cycle_id": z.string().nullable(),
  "source_key": z.string().nullable(),
  "coverage": z.string().nullable(),
  "observation": z.string().nullable(),
  "items_observed": bigintWire.nullable(),
  "items_visible": bigintWire.nullable(),
  "track_count_reported": bigintWire.nullable(),
  "observed_share": z.number().nullable(),
  "last_full_at": timestampWire.nullable(),
  "as_of": timestampWire.nullable(),
  "days_since_full": z.number().nullable(),
  "stream": z.string(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_playlist_coverage = z.infer<typeof mart_playlist_coverage>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "last_full_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "as_of": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_playlist_coverage.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_playlist_coverage.parse(row);
export const metadata = {"name":"mart_playlist_coverage","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["platform","playlist_id","variant","snapshot_id","observed_at","cycle_id","source_key","coverage","observation","items_observed","items_visible","track_count_reported","observed_share","last_full_at","as_of","days_since_full","stream","learning_eligible","resale_permitted","source_keys"],"grain":["platform","playlist_id","variant","stream","observed_at","snapshot_id"],"grain_types":["text","text","text","text","timestamp","text"],"time_columns":["observed_at","last_full_at","as_of"]};
