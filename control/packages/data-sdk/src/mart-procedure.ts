import { RelationLabel } from './relation-labels.js';
import { oc } from "@orpc/contract";
import { z } from "zod";
// Every data-API refusal carries a stable error_class, such as cursor_stale or unauthorized.
export const dataError = z.object({ error_class: z.string(), message: z.string() });
export const martBuild = z.object({
  relation: z.string(),
  scope: z.enum(["global", "tenant"]),
  tenant_slug: z.string().nullable(),
  stamped: z.boolean(),
  cycle_id: z.string().nullable(),
  close_no: z.string().regex(/^\d+$/).nullable(),
  built_at: z.iso.datetime().nullable(),
});
export type MartBuild = z.infer<typeof martBuild>;
export function martProcedure<T extends z.ZodRawShape, R extends z.ZodType>(
  name: string,
  row: z.ZodObject<T>,
  range: R,
) {
  const filters = row
    .partial()
    .extend({ tenant_id: z.never().optional() })
    .strict();
  return oc
    .errors({ DATA: { data: dataError } })
    .route({ method: "GET", path: `/marts/${name}` })
    .input(
      z
        .object({
          filters: filters.optional(),
          range: range.optional(),
          cursor: z.string().max(16384).optional(),
          limit: z.coerce.number().int().min(1).max(100).default(50),
        })
        .strict(),
    )
    .output(
      z.object({ rows: z.array(row), next_cursor: z.string().nullable(), labels: RelationLabel, build: martBuild }),
    );
}
