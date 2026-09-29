// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_early_signals_current = z.object({
  "family": z.string(),
  "rank": bigintWire,
  "day": dateWire.nullable(),
  "song_key": z.string().nullable(),
  "title_text": z.string().nullable(),
  "artist_text": z.string().nullable(),
  "component": z.string().nullable(),
  "value": z.number().nullable(),
  "reason_rule": z.string().nullable(),
  "evidence": z.string().nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
  "window_days": z.number().int().nullable(),
  "chart_spread_gain": bigintWire.nullable(),
  "list_reach_tier": z.number().int().nullable(),
  "market_count": bigintWire.nullable(),
  "last_entered_at": timestampWire.nullable(),
  "movement_list": z.string(),
  "age_class": z.string().nullable(),
  "age_basis": z.string().nullable(),
  "artist_stage": z.string().nullable(),
  "artist_stage_basis": z.string().nullable(),
  "cluster_key": z.string(),
  "member_song_keys": z.string(),
});
export type mart_early_signals_current = z.infer<typeof mart_early_signals_current>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "last_entered_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_early_signals_current.parse(encodeMartRow(row,["day"]));
export const decode = (row: unknown) => mart_early_signals_current.parse(row);
export const metadata = {"name":"mart_early_signals_current","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["family","rank","day","song_key","title_text","artist_text","component","value","reason_rule","evidence","learning_eligible","resale_permitted","source_keys","window_days","chart_spread_gain","list_reach_tier","market_count","last_entered_at","movement_list","age_class","age_basis","artist_stage","artist_stage_basis","cluster_key","member_song_keys"],"grain":["movement_list","family","rank"],"grain_types":["text","text","bigint"],"time_columns":["day","last_entered_at"]};
