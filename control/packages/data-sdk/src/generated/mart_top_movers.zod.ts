// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_top_movers = z.object({
  "rank": bigintWire,
  "day": dateWire,
  "song_key": z.string().nullable(),
  "title_text": z.string().nullable(),
  "artist_text": z.string().nullable(),
  "momentum_score": z.number().nullable(),
  "score_parts": z.string().nullable(),
  "coverage": z.string().nullable(),
  "reason_rule": z.string().nullable(),
  "evidence": z.string().nullable(),
  "ranking_build": z.string().nullable(),
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
export type mart_top_movers = z.infer<typeof mart_top_movers>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "last_entered_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_top_movers.parse(encodeMartRow(row,["day"]));
export const decode = (row: unknown) => mart_top_movers.parse(row);
export const metadata = {"name":"mart_top_movers","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["rank","day","song_key","title_text","artist_text","momentum_score","score_parts","coverage","reason_rule","evidence","ranking_build","learning_eligible","resale_permitted","source_keys","window_days","chart_spread_gain","list_reach_tier","market_count","last_entered_at","movement_list","age_class","age_basis","artist_stage","artist_stage_basis","cluster_key","member_song_keys"],"grain":["day","movement_list","rank"],"grain_types":["date","text","bigint"],"time_columns":["day","last_entered_at"]};
