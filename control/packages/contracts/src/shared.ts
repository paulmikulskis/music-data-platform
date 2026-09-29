import { oc } from "@orpc/contract";
import { z } from "zod";

export const errorSchema = z.object({
  error_class: z.string(),
  message: z.string(),
  next_step: z.string().optional(),
  runbook: z.string().nullable().optional(),
  run_id: z.uuid().optional(),
});

export const id = z.object({ id: z.uuid() });

export const source = z.object({
  source_key: z.string().regex(/^[a-z][a-z0-9_]*$/),
});

export const empty = z.object({});

export const jsonRow = z.record(z.string(), z.json());

export function get<I extends z.ZodType, O extends z.ZodType>(
  path: `/${string}`,
  input: I,
  output: O,
) {
  return oc
    .errors({ SERVICE: { data: errorSchema } })
    .route({ method: "GET", path })
    .input(input)
    .output(output);
}

export function post<I extends z.ZodType, O extends z.ZodType>(
  path: `/${string}`,
  input: I,
  output: O,
) {
  return oc
    .errors({ SERVICE: { data: errorSchema } })
    .route({ method: "POST", path })
    .input(input)
    .output(output);
}
