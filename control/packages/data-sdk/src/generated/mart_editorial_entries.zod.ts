// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_editorial_entries = z.object({
  "platform": z.string(),
  "playlist_id": z.string(),
  "variant": z.string(),
  "occurrence_inferred": z.boolean().nullable(),
  "occurrence_key": z.string(),
  "interval_id": z.string(),
  "event_type": z.string().nullable(),
  "observed_at": timestampWire.nullable(),
  "platform_track_id": z.string().nullable(),
  "position": bigintWire.nullable(),
  "previous_position": bigintWire.nullable(),
  "entered_after": timestampWire.nullable(),
  "first_observed_at": timestampWire.nullable(),
  "removed_after": timestampWire.nullable(),
  "removed_by": timestampWire.nullable(),
  "is_baseline": z.boolean().nullable(),
  "uncertainty_hours": z.number().nullable(),
  "snapshot_id": z.string().nullable(),
  "page_match": z.boolean().nullable(),
  "platform_album_id": z.string().nullable(),
  "isrc": z.string().nullable(),
  "mb_recording_gid": z.string().nullable(),
  "isrc_method": z.string().nullable(),
  "recording_method": z.string().nullable(),
  "confidence": z.number().nullable(),
  "daily_manifest_close_no": bigintWire.nullable(),
  "as_of": timestampWire.nullable(),
  "stream": z.string(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
  "item_type": z.string().nullable(),
  "platform_item_id": z.string().nullable(),
  "featured_track_id": z.string().nullable(),
  "owner_class": z.string().nullable(),
  "first_ever_entry": z.boolean().nullable(),
});
export type mart_editorial_entries = z.infer<typeof mart_editorial_entries>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "entered_after": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "first_observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "removed_after": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "removed_by": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "as_of": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_editorial_entries.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_editorial_entries.parse(row);
export const metadata = {"name":"mart_editorial_entries","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["platform","playlist_id","variant","occurrence_inferred","occurrence_key","interval_id","event_type","observed_at","platform_track_id","position","previous_position","entered_after","first_observed_at","removed_after","removed_by","is_baseline","uncertainty_hours","snapshot_id","page_match","platform_album_id","isrc","mb_recording_gid","isrc_method","recording_method","confidence","daily_manifest_close_no","as_of","stream","learning_eligible","resale_permitted","source_keys","item_type","platform_item_id","featured_track_id","owner_class","first_ever_entry"],"grain":["platform","playlist_id","variant","stream","occurrence_key","interval_id"],"grain_types":["text","text","text","text","text","text"],"time_columns":["observed_at","entered_after","first_observed_at","removed_after","removed_by","as_of"]};
