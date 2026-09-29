import { z } from "zod";
import { jsonRow, get } from "./shared.js";

export const lineageContract = {
    chain: get(
      "/lineage",
      z
        .object({
          dump_id: z.uuid().optional(),
          request_id: z.string().optional(),
          cycle_id: z.uuid().optional(),
          run_id: z.uuid().optional(),
          limit: z.number().int().min(1).max(100).default(100),
          after: z.object({ dumps: z.uuid().nullable().optional(), receipts: z.uuid().nullable().optional(), requests: z.uuid().nullable().optional() }).optional(),
        })
        .refine(
          (v) => !!(v.dump_id || v.request_id || v.cycle_id || v.run_id),
          "Supply a dump, request, cycle or run identity. Open a run to find its ID.",
        ),
      z.object({
        dumps: z.array(jsonRow),
        receipts: z.array(jsonRow),
        requests: z.array(jsonRow),
        next: z.object({ dumps: z.uuid().nullable(), receipts: z.uuid().nullable(), requests: z.uuid().nullable() }),
        next_step: z.string(),
      }),
    ),
  };
