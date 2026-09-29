import { linkPreviewSchema } from "./links";
import { z } from "zod";
import { here, viewerPath } from "./proof-link";

export const traceSelection = z.object({
  entry: z.string().min(1).max(160),
  song: z.string().max(240).optional(),
  ranking: z.string().max(120).optional(),
  source: z.string().max(100).optional(),
});
export type TraceSelection = z.infer<typeof traceSelection>;
export function traceLink(selection: TraceSelection, from = here() ?? "/") {
  const url = new URL(viewerPath(from) ?? "/", "http://viewer.invalid");
  url.searchParams.delete("sheet");
  url.searchParams.set("trace", selection.entry);
  for (const key of ["song", "ranking", "source"] as const) {
    url.searchParams.delete(key);
    if (selection[key]) url.searchParams.set(key, selection[key]);
  }
  return url.pathname + url.search;
}
export const traceNode = z.object({
  id: z.string(),
  label: z.string(),
  short: z.string(),
  stage: z.string(),
  relation: z.string().nullable(),
  lit: z.boolean(),
  cadence: z.string().nullable(),
  scheduled_at: z.string().nullable(),
  source: z.string().nullable(),
  brand: z.string().nullable(),
  last_read: z.string().nullable(),
  enabled: z.boolean().nullable().default(null),
  paused: z.boolean().default(false),
  count: z
    .object({
      value: z.string(),
      captured_at: z.string(),
      build_key: z.string(),
      build: z.object({
        relation: z.string(),
        cycle_id: z.string(),
        close_no: z.string().nullable(),
        built_at: z.string(),
        scope: z.literal("global"),
        stamped: z.literal(true),
        tenant_slug: z.null(),
      }),
    })
    .nullable(),
  filters: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
  expected: z.string().nullable(),
  preview: z.boolean(),
  code: z.string().nullable(),
  links: z.array(linkPreviewSchema).optional(),
});
export const traceView = z.object({
  state: z.enum(["evidenced", "unavailable", "source", "unknown"]),
  title: z.string(),
  fact: z.string().nullable(),
  revision: z.string().nullable(),
  nodes: z.array(traceNode),
  edges: z.array(
    z.object({ from: z.string(), to: z.string(), lit: z.boolean() }),
  ),
});
export type TraceView = z.infer<typeof traceView>;
export const peekView = z.object({
  state: z.enum(["ready", "unavailable", "changed"]),
  cache_state: z.enum(["live", "cached", "busy"]),
  saved_at: z.string().nullable(),
  columns: z.array(z.string()),
  rows: z.array(
    z.record(
      z.string(),
      z.union([z.string(), z.number(), z.boolean(), z.null()]),
    ),
  ),
  captured_at: z.string().nullable(),
  total: z.string().nullable(),
  sql: z.string().nullable(),
});
export type PeekView = z.infer<typeof peekView>;
