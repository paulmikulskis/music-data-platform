// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_search_index = z.object({
  "object_key": z.string(),
  "kind": z.string(),
  "display_text": z.string(),
  "context": z.string(),
  "aliases": z.string(),
  "last_seen": timestampWire,
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_search_index = z.infer<typeof mart_search_index>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "last_seen": z.object({ from: timestampWire, to: timestampWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_search_index.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_search_index.parse(row);
export const metadata = {"name":"mart_search_index","schema":"marts","tenant_scoped":false,"tenant_readable":false,"columns":["object_key","kind","display_text","context","aliases","last_seen","learning_eligible","resale_permitted","source_keys"],"grain":["object_key"],"grain_types":["text"],"time_columns":["last_seen"]};
