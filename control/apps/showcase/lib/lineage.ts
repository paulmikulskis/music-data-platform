import { z } from "zod";
import generated from "./lineage.generated.json";

const hash = z.string().regex(/^[a-f0-9]{64}$/);
const edge = z
  .object({ from: z.string(), to: z.string(), kind: z.string() })
  .strict();
const selector = z
  .object({
    relation: z.string(),
    row_keys: z.array(z.string()).min(1),
    evidence: z.literal("served_locator"),
    require_input_build: z.literal(true),
  })
  .strict();
export const lineageSchema = z
  .object({
    schema_version: z.literal(1),
    input_hashes: z.record(z.string(), hash),
    meaning: z.literal("Can feed this"),
    nodes: z.array(
      z
        .object({
          id: z.string(),
          kind: z.enum(["relation", "function", "source", "fact"]),
          relation: z.string().nullable(),
          file: z.string().nullable(),
          tier: z.number().int(),
          label: z.string(),
          short_label: z.string().max(18).nullable(),
          stage: z.string(),
          family: z.string().nullable(),
          cadence: z.string().nullable(),
          preview: z.string(),
        })
        .strict(),
    ),
    edges: z.array(edge),
    simple_edges: z.array(
      z
        .object({ from: z.string(), to: z.string(), via: z.array(z.string()) })
        .strict(),
    ),
    entries: z.array(
      z
        .object({
          id: z.string(),
          component: z.string(),
          relation: z.string(),
          code: z.string(),
          selectors: z.array(selector).min(1),
        })
        .strict(),
    ),
    peeks: z.record(
      z.string(),
      z
        .object({
          columns: z.array(z.string()).min(1).max(4),
          order: z.array(z.string()),
          filters: z.array(z.string()),
          public_links: z.array(z.string()).length(0),
          workbench: z.boolean(),
          policy: z.enum(["public_metadata", "global_tracked_accounts"]),
          guards: z.array(
            z
              .object({
                relation: z.string(),
                columns: z.array(z.string()),
                file: z.string(),
              })
              .strict(),
          ),
          provenance: z.record(
            z.string(),
            z.object({ file: z.string(), reason: z.string() }).strict(),
          ),
        })
        .strict(),
    ),
  })
  .strict();
export const lineage = lineageSchema.parse(generated);
export type Lineage = z.infer<typeof lineageSchema>;

const inputBuild = z.object({
  relation: z.string(),
  scope: z.literal("global"),
  cycle_id: z.string().min(1),
  close_no: z.string().min(1),
  built_at: z.string().datetime({ offset: true }),
});
const rowKey = z.record(
  z.string(),
  z.union([z.string(), z.number(), z.boolean(), z.null()]),
);
const locator = z.object({
  component: z.string(),
  relation: z.string(),
  row_key: rowKey,
  input_build: inputBuild,
});
const factEvidence = z.object({
  song_key: z.string(),
  ranking_build: z.string(),
  evidence: z.array(locator),
});
const receiptEvidence = z
  .object({
    song_key: z.string(),
    ranking_build: z.string(),
    locator,
    receipt: z
      .object({
        source_key: z.string(),
        target_table: z.string(),
        run_id: z.string().uuid(),
        dump_id: z.string().uuid(),
      })
      .strict(),
  })
  .strict();
const selectedFact = z.object({
  entry: z.string(),
  song_key: z.string(),
  ranking_build: z.string(),
});
// A caller passes the saved served row, or a current row from the selected ranking build.
// Its source_keys are intentionally ignored. No inferred dependency becomes a lit edge.
export function resolveFactPath(
  selection: unknown,
  servedRow: unknown,
  graph = lineage,
  verifiedReceipts: unknown = [],
) {
  const selected = selectedFact.parse(selection);
  const entry = graph.entries.find((item) => item.id === selected.entry);
  const parsed = factEvidence.safeParse(servedRow);
  const unavailable = {
    state: "unavailable",
    edges: [],
    evidence: [],
    next: "/sources",
  } as const;
  if (!entry || !parsed.success) return unavailable;
  const row = parsed.data;
  if (
    row.song_key !== selected.song_key ||
    row.ranking_build !== selected.ranking_build
  )
    return unavailable;
  const evidence = row.evidence.flatMap((item) => {
    const relation = `marts.${item.relation.replace(/^marts\./, "")}`;
    const rule = entry.selectors.find(
      (candidate) => candidate.relation === relation,
    );
    if (
      !rule ||
      item.component !== entry.component ||
      item.input_build.relation.replace(/^marts\./, "") !==
        relation.replace(/^marts\./, "") ||
      !rule.row_keys.every(
        (key) => item.row_key[key] !== undefined && item.row_key[key] !== null,
      )
    )
      return [];
    // Never pass unreviewed locator fields into client JSON or a public link.
    return [
      {
        ...item,
        row_key: Object.fromEntries(
          rule.row_keys.map((key) => [key, item.row_key[key]]),
        ),
      },
    ];
  });
  const relations = new Set(
    evidence.map(
      (item) => `rel:marts.${item.relation.replace(/^marts\./, "")}`,
    ),
  );
  const edges = graph.edges.filter(
    (item) =>
      item.kind === "evidence" &&
      item.to === `entry:${entry.id}` &&
      relations.has(item.from),
  );
  if (!edges.length) return unavailable;
  // These receipts come from a server read that matched the locator's row and input build.
  // Cycle membership alone is insufficient. No receipt is inferred from a provider or platform.
  const receipts = z.array(receiptEvidence).safeParse(verifiedReceipts);
  for (const contribution of receipts.success ? receipts.data : []) {
    if (
      contribution.song_key !== selected.song_key ||
      contribution.ranking_build !== selected.ranking_build
    )
      continue;
    const supported = evidence.some(
      (item) =>
        item.component === contribution.locator.component &&
        item.relation.replace(/^marts\./, "") ===
          contribution.locator.relation.replace(/^marts\./, "") &&
        item.input_build.cycle_id ===
          contribution.locator.input_build.cycle_id &&
        item.input_build.close_no ===
          contribution.locator.input_build.close_no &&
        item.input_build.built_at ===
          contribution.locator.input_build.built_at &&
        Object.entries(item.row_key).every(
          ([key, value]) => contribution.locator.row_key[key] === value,
        ),
    );
    if (!supported) continue;
    const writer = `fn:${contribution.receipt.source_key}`;
    const write = graph.edges.find(
      (edge) =>
        edge.kind === "write" &&
        edge.from === writer &&
        edge.to === `rel:${contribution.receipt.target_table}`,
    );
    if (!write) continue;
    edges.push(write);
    edges.push(
      ...graph.edges.filter(
        (edge) => edge.kind === "declares" && edge.to === writer,
      ),
    );
  }
  return {
    state: "evidenced",
    edges: [
      ...new Map(
        edges.map((edge) => [`${edge.from}:${edge.to}`, edge]),
      ).values(),
    ],
    evidence,
    next: "/sources",
  } as const;
}
