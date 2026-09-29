// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_chart_history = z.object({
  "chart_name": z.string(),
  "chart_week": dateWire,
  "chart_position": z.number().int(),
  "track_title": z.string().nullable(),
  "artist_name": z.string().nullable(),
  "song_key": z.string().nullable(),
  "billboard_match_method": z.string().nullable(),
  "confidence": z.number().nullable(),
  "matched_by_group": z.boolean().nullable(),
  "weeks_on_chart": z.number().int().nullable(),
  "is_debut": z.boolean().nullable(),
  "source_key": z.string().nullable(),
  "learning_eligible": z.boolean(),
  "_cycle_id": z.string().nullable(),
  "_built_by": z.string().nullable(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_chart_history = z.infer<typeof mart_chart_history>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "chart_week": z.object({ from: dateWire, to: dateWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_chart_history.parse(encodeMartRow(row,["chart_week"]));
export const decode = (row: unknown) => mart_chart_history.parse(row);
export const metadata = {"name":"mart_chart_history","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["chart_name","chart_week","chart_position","track_title","artist_name","song_key","billboard_match_method","confidence","matched_by_group","weeks_on_chart","is_debut","source_key","learning_eligible","_cycle_id","_built_by","resale_permitted","source_keys"],"grain":["chart_name","chart_week","chart_position"],"grain_types":["varchar","date","integer"],"time_columns":["chart_week"]};
