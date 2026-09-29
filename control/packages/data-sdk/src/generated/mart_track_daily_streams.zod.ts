// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_track_daily_streams = z.object({
  "platform": z.string(),
  "platform_track_id": z.string(),
  "day": dateWire,
  "play_count": bigintWire.nullable(),
  "count_changed_at": timestampWire.nullable(),
  "streams_since_last_update": bigintWire.nullable(),
  "previous_count_changed_at": timestampWire.nullable(),
  "count_status": z.string(),
  "count_source": z.string(),
  "observed_at": timestampWire.nullable(),
  "_run_ids": z.string(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_track_daily_streams = z.infer<typeof mart_track_daily_streams>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "count_changed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "previous_count_changed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
  "observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_track_daily_streams.parse(encodeMartRow(row,["day"]));
export const decode = (row: unknown) => mart_track_daily_streams.parse(row);
export const metadata = {"name":"mart_track_daily_streams","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["platform","platform_track_id","day","play_count","count_changed_at","streams_since_last_update","previous_count_changed_at","count_status","count_source","observed_at","_run_ids","learning_eligible","resale_permitted","source_keys"],"grain":["platform","platform_track_id","day"],"grain_types":["text","text","date"],"time_columns":["day","count_changed_at","previous_count_changed_at","observed_at"]};
