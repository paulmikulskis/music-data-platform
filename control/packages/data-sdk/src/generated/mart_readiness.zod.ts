// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_readiness = z.object({
  "family": z.string(),
  "day": dateWire.nullable(),
  "history_days": bigintWire,
  "history_needed": z.string().nullable(),
  "first_rank_day": dateWire.nullable(),
  "movement_list": z.string().nullable(),
  "stage_known_songs": bigintWire.nullable(),
  "list_songs": bigintWire.nullable(),
  "chart_week": dateWire.nullable(),
  "keyed_entries": bigintWire.nullable(),
  "chart_entries": bigintWire.nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_readiness = z.infer<typeof mart_readiness>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "first_rank_day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "chart_week": z.object({ from: dateWire, to: dateWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_readiness.parse(encodeMartRow(row,["day","first_rank_day","chart_week"]));
export const decode = (row: unknown) => mart_readiness.parse(row);
export const metadata = {"name":"mart_readiness","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["family","day","history_days","history_needed","first_rank_day","movement_list","stage_known_songs","list_songs","chart_week","keyed_entries","chart_entries","learning_eligible","resale_permitted","source_keys"],"grain":["family"],"grain_types":["text"],"time_columns":["day","first_rank_day","chart_week"]};
