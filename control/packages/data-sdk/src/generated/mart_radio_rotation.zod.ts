// Generated from the catalog and mart contracts. Do not edit.
import { z } from 'zod';
import { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';
export const mart_radio_rotation = z.object({
  "station": z.string(),
  "recording_mbid": z.string(),
  "week_start": dateWire,
  "plays": bigintWire.nullable(),
  "top_rotation": z.string().nullable(),
  "first_played": z.boolean().nullable(),
  "rotation_added": z.boolean().nullable(),
  "rotation_upgraded": z.boolean().nullable(),
  "artist_mbids": z.string().nullable(),
  "release_group_mbid": z.string().nullable(),
  "requests": bigintWire.nullable(),
  "local": z.boolean().nullable(),
  "learning_eligible": z.boolean(),
  "resale_permitted": z.boolean(),
  "source_keys": z.string(),
});
export type mart_radio_rotation = z.infer<typeof mart_radio_rotation>;
// Half-open time ranges: from <= column < to.
export const range = z.object({
  "week_start": z.object({ from: dateWire, to: dateWire }).partial().strict(),
}).partial().strict();
export const encode = (row: unknown) => mart_radio_rotation.parse(encodeMartRow(row,["week_start"]));
export const decode = (row: unknown) => mart_radio_rotation.parse(row);
export const metadata = {"name":"mart_radio_rotation","schema":"marts","tenant_scoped":false,"tenant_readable":false,"columns":["station","recording_mbid","week_start","plays","top_rotation","first_played","rotation_added","rotation_upgraded","artist_mbids","release_group_mbid","requests","local","learning_eligible","resale_permitted","source_keys"],"grain":[],"grain_types":[],"time_columns":["week_start"]};
