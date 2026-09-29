import { z } from "zod";
export const bigintWire = z.string().regex(/^-?\d+$/);
export const decimalWire = z.string().regex(/^-?\d+(\.\d+)?$/);
export const timestampWire = z.iso.datetime();
export const dateWire = z.iso.date();
export function encodeWire(value: unknown): unknown {
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(encodeWire);
  if (typeof value === "object" && value !== null)
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, encodeWire(v)]),
    );
  return value;
}
export const receiptSchema = z.object({
  row_coverage: z.enum(["full", "partial", "empty"]).nullable().optional(),
  row_acceptance: z.number().nullable().optional(),
  row_rejection_share: z.number().nullable().optional(),
  rows_excluded: z.string().nullable().optional(),
  row_exclusions: z.record(z.string(), z.number().int().nonnegative()).nullable().optional(),
  min_row_coverage: z.number().nullable().optional(),
  row_coverage_met: z.boolean().nullable().optional(),
  min_target_coverage: z.number().nullable().optional(),
  target_coverage: z.number().nullable().optional(),
  targets_succeeded: z.number().int().nullable().optional(),
  targets_total: z.number().int().nullable().optional(),
  target_coverage_met: z.boolean().nullable().optional(),
  allow_partial: z.boolean(),
  run_id: z.uuid(),
  status: z.string(),
  coverage: z.string().nullable(),
  rows_written: bigintWire,
  rows_rejected: bigintWire,
  dump_id: z.uuid().nullable(),
  landed_seq: bigintWire.nullable(),
  trace_url: z.string(),
  message: z.string(),
  error_class: z.string().nullable().optional(),
  loads: z.array(
    z.object({
      dump_id: z.uuid(),
      status: z.string(),
      generation: z.number().int(),
      rows_inserted: bigintWire.nullable().optional(),
    }),
  ),
});
export type Receipt = z.infer<typeof receiptSchema>;

export function encodeMartRow(value: unknown, dateColumns: string[]): unknown {
  const row = z.record(z.string(), z.unknown()).parse(encodeWire(value));
  for (const key of dateColumns)
    if (typeof row[key] === "string") row[key] = row[key].slice(0, 10);
  return row;
}
