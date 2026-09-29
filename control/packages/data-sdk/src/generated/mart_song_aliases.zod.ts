// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_song_aliases = z.object({
  "alias_key": z.string(),
  "song_key": z.string().nullable(),
  "platform": z.string().nullable(),
  "platform_track_id": z.string().nullable(),
  "resolved": z.boolean().nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_song_aliases = z.infer<typeof mart_song_aliases>;
// Half-open time ranges: from <= column < to.
export const range = z.object({

}).partial().strict();
export const encode = (row: unknown) => mart_song_aliases.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_song_aliases.parse(row);
export const metadata = {"name":"mart_song_aliases","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["alias_key","song_key","platform","platform_track_id","resolved","learning_eligible","resale_permitted","source_keys"],"grain":["alias_key"],"grain_types":["text"],"time_columns":[]};
