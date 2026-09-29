// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_track_identity_audit = z.object({
  "chart": z.string().nullable(),
  "territory": z.string().nullable(),
  "week": z.string().nullable(),
  "canonical_track_id": z.string().nullable(),
  "source_key": z.string().nullable(),
  "value": z.string().nullable(),
  "entity_id": z.string().nullable(),
  "valid_components": z.boolean().nullable(),
  "priority_rank": z.number().int().nullable(),
  "selected": z.boolean().nullable(),
  "selected_source": z.string().nullable(),
  "selected_value": z.string().nullable(),
});
export type mart_track_identity_audit = z.infer<typeof mart_track_identity_audit>;
// Half-open time ranges: from <= column < to.
export const range = z.object({

}).partial().strict();
export const encode = (row: unknown) => mart_track_identity_audit.parse(encodeMartRow(row,[]));
export const decode = (row: unknown) => mart_track_identity_audit.parse(row);
export const metadata = {"name":"mart_track_identity_audit","schema":"marts","tenant_scoped":false,"tenant_readable":true,"columns":["chart","territory","week","canonical_track_id","source_key","value","entity_id","valid_components","priority_rank","selected","selected_source","selected_value"],"grain":[],"grain_types":[],"time_columns":[]};
