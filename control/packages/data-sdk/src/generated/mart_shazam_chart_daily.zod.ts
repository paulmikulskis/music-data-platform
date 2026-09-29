// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_shazam_chart_daily = z.object({
  "chart": z.string(),
  "chart_type": z.string().nullable(),
  "country": z.string().nullable(),
  "city": z.string().nullable(),
  "chart_date": dateWire,
  "position": z.number().int(),
  "apple_song_id": z.string(),
  "apple_primary_artist_id": z.string().nullable(),
  "artist_text": z.string().nullable(),
  "title_text": z.string().nullable(),
  "multi_artist_credit": z.boolean().nullable(),
  "isrc": z.string().nullable(),
  "isrc_source": z.string().nullable(),
  "mb_recording_gid": z.string().nullable(),
  "recording_method": z.string().nullable(),
  "confidence": z.number().nullable(),
  "observed_at": timestampWire,
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_shazam_chart_daily = z.infer<typeof mart_shazam_chart_daily>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "chart_date": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "observed_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_shazam_chart_daily.parse(encodeMartRow(row,["chart_date"]));
export const decode = (row: unknown) => mart_shazam_chart_daily.parse(row);
export const metadata = {"name":"mart_shazam_chart_daily","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["chart","chart_type","country","city","chart_date","position","apple_song_id","apple_primary_artist_id","artist_text","title_text","multi_artist_credit","isrc","isrc_source","mb_recording_gid","recording_method","confidence","observed_at","learning_eligible","resale_permitted","source_keys"],"grain":["chart","chart_date","position"],"grain_types":["text","date","integer"],"time_columns":["chart_date","observed_at"]};
