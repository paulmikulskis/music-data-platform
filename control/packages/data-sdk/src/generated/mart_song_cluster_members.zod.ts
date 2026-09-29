// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_song_cluster_members = z.object({
  "song_key": z.string(),
  "cluster_key": z.string(),
  "representative_song_key": z.string(),
  "cluster_methods": z.string(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_song_cluster_members = z.infer<typeof mart_song_cluster_members>;
// Half-open time ranges: from <= column < to.
export const range = z.object({

}).partial().strict();
export const encode = (row: unknown) => mart_song_cluster_members.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_song_cluster_members.parse(row);
export const metadata = {"name":"mart_song_cluster_members","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["song_key","cluster_key","representative_song_key","cluster_methods","learning_eligible","resale_permitted","source_keys"],"grain":["song_key"],"grain_types":["text"],"time_columns":[]};
