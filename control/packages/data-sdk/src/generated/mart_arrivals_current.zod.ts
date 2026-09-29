// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_arrivals_current = z.object({
  "song_key": z.string(),
  "rank": bigintWire,
  "day": dateWire.nullable(),
  "title_text": z.string().nullable(),
  "artist_text": z.string().nullable(),
  "markets": z.string().nullable(),
  "entered_lists": bigintWire.nullable(),
  "discovery_entries": bigintWire,
  "entered_charts": bigintWire.nullable(),
  "reason_rule": z.string().nullable(),
  "evidence": z.string().nullable(),
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
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
  "cluster_key": z.string(),
  "member_song_keys": z.string(),
});
export type mart_arrivals_current = z.infer<typeof mart_arrivals_current>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "day": z.object({ from: dateWire, to: dateWire }).partial().strict(),
  "last_entered_at": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_arrivals_current.parse(encodeMartRow(row,["day"]));
export const decode = (row: unknown) => mart_arrivals_current.parse(row);
export const metadata = {"name":"mart_arrivals_current","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["song_key","rank","day","title_text","artist_text","markets","entered_lists","discovery_entries","entered_charts","reason_rule","evidence","window_days","chart_spread_gain","list_reach_tier","market_count","last_entered_at","movement_list","age_class","age_basis","artist_stage","artist_stage_basis","learning_eligible","resale_permitted","source_keys","cluster_key","member_song_keys"],"grain":["song_key"],"grain_types":["text"],"time_columns":["day","last_entered_at"]};
