import { warehouseLabels, writerScope, type Labels } from "./console-semantics.js";
// Data explorer catalog assembly.
// Reads three sources and folds them into one browsable set of entities:
//   1. the warehouse (physical Postgres relations, via the reader_wh role — never an admin role)
//   2. the dbt project (logical models, from dbt/target/manifest.json + catalog.json)
//   3. the control plane (source functions, i.e. streamlines)
// Everything here is read-only. No warehouse writes, no credentials on any surface.
import { readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import postgres from "postgres";
import { z } from "zod";
import { streamlineDto } from "@mdp/contracts";
import { rows, type DB } from "./db.js";

// Only the control-DB handle is needed here; typed structurally so this module
// never imports the router (avoids a cycle). The full Context satisfies it.
type CatalogContext = { db: DB; identity?: { admin: boolean } };

// display_name is a NEW field. Precedence: dbt meta.display_name (authored in a model/source's
// meta, flows through the manifest) → this curated seed → null (the caller then shows the raw
// slug as the title, gracefully). These are real, honest names — never fabricated stats.
export const SEED_DISPLAY_NAMES: Record<string, string> = {
  "raw.billboard_chart_entries": "Billboard chart entries",
  chart_entries: "Chart entries",
  "raw.chart_entries": "Chart entries",
  targets: "Targets",
  "raw.targets": "Targets",
  mart_chart_history: "Chart history",
  "marts.mart_chart_history": "Chart history",
  billboard_hot100: "Billboard Hot 100",
};

export type EntityKind = "table" | "view" | "model" | "source";
export type EntityGroup = "warehouse" | "dbt" | "functions";

export type Column = {
  name: string;
  type: string | null;
  nullable: boolean | null;
  description: string | null;
};
export type LineageRef = { name: string; id: string | null; kind: EntityKind };
export type LastActivity = {
  at: string;
  runId: string | null;
  verb: string; // "landed" | "run" — the temporal verb shown before the relative time
} | null;
export type EntityLink = { label: string; href: string };

export type Entity = {
  qualified: string;
  labels?: Labels;
  availability?: "built" | "not_built" | "inline" | "unknown";
  buildCommand?: string;
  id: string; // e.g. "table~raw.account_snapshots" — url-safe, one path segment
  kind: EntityKind;
  group: EntityGroup;
  slug: string; // machine identifier (qualified for warehouse, model name, or source_key)
  displayName: string | null; // resolved human name, or null → slug is the title
  schema: string | null;
  format: string; // "Postgres" | "dbt" | "Fetch" — small format tag for the popover
  location: string; // one terse line: where it physically lives / what layer it is
  description: string | null;
  author: string | null; // "human" / "agent" only when derivable from meta; else null
  columns: Column[];
  stats: { rows: number | null; bytes: number | null; size: string | null };
  tags: string[];
  cadence: string | null;
  layer: string | null;
  enabled: boolean | null;
  reads: string[];
  writes: string[];
  upstream: LineageRef[];
  downstream: LineageRef[];
  last: LastActivity;
  links: EntityLink[];
  materialization: string | null;
};

export type Section = { key: string; label: string; entities: Entity[] };
export type Group = {
  key: EntityGroup;
  label: string;
  sublabel: string;
  sections: Section[];
};
export type Preview = {
  id: string;
  kind: EntityKind;
  display: string; // display_name or slug
  slug: string;
  qualified: string;
  group: EntityGroup;
  location: string;
  format: string;
  description: string | null;
  rows: number | null;
  size: string | null;
  columnCount: number;
  columns: string[]; // first several column names
  last: string | null; // "landed 8m ago" style, plain text
  href: string;
};
export type SearchIndex = {
  entities: Preview[];
  columns: { column: string; entity: string; id: string; kind: EntityKind }[];
};
export type Catalog = {
  groups: Group[];
  entities: Entity[];
  index: SearchIndex;
  warehouseReachable: boolean;
  manifestReachable: boolean;
};

// ---- pure helpers -------------------------------------------------------

const KIND_CODE: Record<EntityKind, string> = {
  table: "table",
  view: "view",
  model: "model",
  source: "fn",
};
const CODE_KIND: Record<string, EntityKind> = {
  table: "table",
  view: "view",
  model: "model",
  fn: "source",
};
export function entityId(kind: EntityKind, identifier: string): string {
  return `${KIND_CODE[kind]}~${identifier}`;
}
export function decodeEntityId(
  id: string,
): { kind: EntityKind; identifier: string } | null {
  const at = id.indexOf("~");
  if (at < 0) return null;
  const kind = CODE_KIND[id.slice(0, at)];
  if (!kind) return null;
  return { kind, identifier: id.slice(at + 1) };
}

// Split a qualified name into a de-emphasized prefix and the specific leaf the eye should land on.
// `raw.account_snapshots` -> {prefix:"raw.", leaf:"account_snapshots"}
export function splitQualified(name: string): { prefix: string; leaf: string } {
  const dot = name.lastIndexOf(".");
  if (dot >= 0) return { prefix: name.slice(0, dot + 1), leaf: name.slice(dot + 1) };
  const under = name.lastIndexOf("__");
  if (under >= 0) return { prefix: name.slice(0, under + 2), leaf: name.slice(under + 2) };
  return { prefix: "", leaf: name };
}

export function resolveDisplayName(
  slug: string,
  metaDisplayName?: unknown,
): string | null {
  if (typeof metaDisplayName === "string" && metaDisplayName.trim())
    return metaDisplayName.trim();
  if (SEED_DISPLAY_NAMES[slug]) return SEED_DISPLAY_NAMES[slug]!;
  const { leaf } = splitQualified(slug);
  if (SEED_DISPLAY_NAMES[leaf]) return SEED_DISPLAY_NAMES[leaf]!;
  return null;
}

// Meaningful columns first; loader/provenance columns (_ingested_at, _run_id, …) sink to the
// bottom so the eye lands on the real shape. Stable within each group (Node preserves order).
export function sortColumns(columns: Column[]): Column[] {
  return [...columns].sort((a, b) => Number(a.name.startsWith("_")) - Number(b.name.startsWith("_")));
}

function humanSize(bytes: number | null): string | null {
  if (bytes === null || bytes < 0) return null;
  const units = ["B", "kB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

function relative(at: string): string {
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(at)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

// ---- raw input shapes ---------------------------------------------------

export type WarehouseRelation = {
  schema: string;
  name: string;
  type: "table" | "view";
  columns: Column[];
  rows: number | null;
  bytes: number | null;
};
export type LastRun = {
  streamline_id: string;
  id: string;
  status: string;
  created_at: string;
  rows_written: string;
};
export type ManifestModel = {
  name: string;
  alias?: string;
  schema: string | null;
  description: string | null;
  materialized: string | null;
  tags: string[];
  meta: Record<string, unknown>;
  columns: Column[];
  upstream: string[]; // manifest node unique_ids
  downstream: string[]; // manifest node unique_ids
  folder: string;
};
export type ManifestSource = {
  sourceName: string;
  name: string;
  description: string | null;
  meta: Record<string, unknown>;
};
export type StreamlineRow = z.infer<typeof streamlineDto>;

export type CatalogInputs = {
  physicalRelations?: { schema: string; name: string }[];
  relations: WarehouseRelation[];
  streamlines: StreamlineRow[];
  lastRuns: LastRun[];
  models: ManifestModel[];
  sources: ManifestSource[];
  warehouseReachable: boolean;
  manifestReachable: boolean;
};

// bareModel strips the dbt unique_id prefix: model.pkg.name -> name, source.pkg.raw.x -> raw.x
export function bareNode(uid: string): string {
  return uid
    .replace(/^model\.[^.]+\./, "")
    .replace(/^source\.[^.]+\./, "");
}

function authorFromMeta(meta: Record<string, unknown>): string | null {
  const raw = meta.authored_by ?? meta.author ?? meta.generated_by ?? meta.owner;
  if (typeof raw !== "string" || !raw.trim()) return null;
  const value = raw.trim().toLowerCase();
  if (/(agent|llm|model|auto|generated|gpt|claude)/.test(value)) return "agent";
  if (/(human|hand|analyst|engineer|manual|authored)/.test(value)) return "human";
  return raw.trim();
}

// ---- catalog assembly ---------------------------------------------------

export function assembleCatalog(inputs: CatalogInputs): Catalog {
  const { relations, streamlines, lastRuns, models, sources } = inputs;
  const lastRunByStreamline = new Map(lastRuns.map((r) => [r.streamline_id, r]));
  const streamlineById = new Map(streamlines.map((s) => [s.id, s]));
  // which streamline writes a given qualified relation (raw.account_snapshots)
  const writerBySlug = new Map<string, StreamlineRow>();
  for (const s of streamlines)
    for (const w of s.writes) if (!writerBySlug.has(w)) writerBySlug.set(w, s);
  const sourceByRelation = new Map(
    sources.map((s) => [`${s.sourceName}.${s.name}`, s]),
  );
  const modelByName = new Map(models.map((m) => [m.name, m]));
  const relationBySlug = new Map(
    relations.map((r) => [`${r.schema}.${r.name}`, r]),
  );

  const lastFromWriter = (slug: string): LastActivity => {
    const writer = writerBySlug.get(slug);
    if (!writer) return null;
    const run = lastRunByStreamline.get(writer.id);
    if (!run) return null;
    return { at: run.created_at, runId: run.id, verb: "landed" };
  };

  const entities: Entity[] = [];

  // -- warehouse relations ------------------------------------------------
  for (const r of relations) {
    const slug = `${r.schema}.${r.name}`;
    const source = sourceByRelation.get(slug);
    const model = modelByName.get(r.name);
    const writer = writerBySlug.get(slug);
    const description =
      source?.description?.trim() ||
      model?.description?.trim() ||
      null;
    const displayName = resolveDisplayName(
      slug,
      source?.meta.display_name ?? model?.meta.display_name,
    );
    const links: EntityLink[] = [];
    if (model)
      links.push({ label: `dbt model ${model.name}`, href: `/explorer/e/${entityId("model", model.name)}` });
    if (writer)
      links.push({ label: `Function ${writer.source_key}`, href: `/functions/${writer.source_key}` });
    const providerKey =
      typeof source?.meta.source_key === "string" ? source.meta.source_key : null;
    const upstream: LineageRef[] = writer
      ? [{ name: writer.source_key, id: entityId("source", writer.source_key), kind: "source" }]
      : [];
    // downstream: models that declare this relation's leaf as a parent source/model
    const downstream: LineageRef[] = models
      .filter((m) => m.upstream.some((u) => bareNode(u) === slug || bareNode(u) === r.name))
      .map((m) => ({ name: m.name, id: entityId("model", m.name), kind: "model" as const }));
    entities.push({
      id: entityId(r.type, slug),
      kind: r.type,
      group: "warehouse",
      slug,
      qualified: slug,
      displayName,
      schema: r.schema,
      format: "Postgres",
      location: `Postgres · warehouse · ${r.schema}${providerKey ? ` · source ${providerKey}` : ""}`,
      description,
      author: source ? authorFromMeta(source.meta) : model ? authorFromMeta(model.meta) : null,
      columns: r.columns.map((column) => ({
        ...column,
        description: model?.columns.find((c) => c.name === column.name)?.description?.trim()
          || column.description,
      })),
      stats: { rows: r.rows, bytes: r.bytes, size: humanSize(r.bytes) },
      tags: model?.tags ?? [],
      cadence: writer?.cadence_tag ?? null,
      layer: writer?.layer ?? null,
      enabled: null,
      reads: [],
      writes: [],
      upstream,
      downstream,
      last: lastFromWriter(slug),
      links,
      materialization: r.type === "view" ? "view" : "table",
    });
  }

  // -- dbt models ---------------------------------------------------------
  for (const m of models) {
    const displayName = resolveDisplayName(m.name, m.meta.display_name);
    const tenant = m.tags.includes("scope:tenant") || m.schema?.startsWith("tenant_");
    const physical = tenant ? null : relations.find((r) =>
      r.name === (m.alias ?? m.name) && (r.schema === m.schema || r.schema === `explore_${m.schema}`),
    ) ?? null;
    const exists = !tenant && (inputs.physicalRelations ?? relations).some((r) =>
      r.name === (m.alias ?? m.name) && r.schema === m.schema,
    );
    const availability = tenant ? "unknown" : m.materialized === "ephemeral" ? "inline"
      : !inputs.warehouseReachable ? "unknown" : exists ? "built" : "not_built";
    const buildCommand = tenant ? undefined : localModelBuildCommand(m.name);
    const links: EntityLink[] = [];
    if (physical)
      links.push({
        label: `Warehouse ${physical.schema}.${physical.name}`,
        href: `/explorer/e/${entityId(physical.type, `${physical.schema}.${physical.name}`)}`,
      });
    // function that produces this model, if its name carries a known source_key or writes to it
    const producer =
      streamlines.find((s) => m.name.endsWith(`__${s.source_key}`)) ??
      (physical ? writerBySlug.get(`${physical.schema}.${physical.name}`) : undefined);
    if (producer)
      links.push({ label: `Function ${producer.source_key}`, href: `/functions/${producer.source_key}` });
    const upstream: LineageRef[] = m.upstream.map((u) => {
      const bare = bareNode(u);
      const asModel = modelByName.get(bare);
      if (asModel) return { name: bare, id: entityId("model", bare), kind: "model" as const };
      const rel = relationBySlug.get(bare);
      if (rel) return { name: bare, id: entityId(rel.type, bare), kind: rel.type };
      return { name: bare, id: null, kind: "source" as const };
    });
    const downstream: LineageRef[] = m.downstream.map((u) => {
      const bare = bareNode(u);
      const asModel = modelByName.get(bare);
      return asModel
        ? { name: bare, id: entityId("model", bare), kind: "model" as const }
        : { name: bare, id: null, kind: "model" as const };
    });
    entities.push({
      id: entityId("model", m.name),
      kind: "model",
      availability,
      ...(buildCommand ? { buildCommand } : {}),
      group: "dbt",
      slug: m.name,
      qualified: m.name,
      displayName,
      schema: m.folder,
      format: "dbt",
      location: `dbt model · ${m.folder}${m.materialized ? ` · ${m.materialized}` : ""}${availability === "not_built" ? " · Not built here" : ""}`,
      description: m.description?.trim() || null,
      author: authorFromMeta(m.meta),
      columns: m.columns,
      stats: {
        rows: physical?.rows ?? null,
        bytes: physical?.bytes ?? null,
        size: humanSize(physical?.bytes ?? null),
      },
      tags: m.tags,
      cadence: m.tags.find((t) => t.startsWith("cadence:"))?.split(":")[1] ?? null,
      layer: m.tags.find((t) => ["bronze", "silver", "gold", "universal"].includes(t)) ?? null,
      enabled: null,
      reads: [],
      writes: [],
      upstream,
      downstream,
      last: physical ? lastFromWriter(`${physical.schema}.${physical.name}`) : null,
      links,
      materialization: m.materialized,
    });
  }

  // -- source functions (streamlines) ------------------------------------
  for (const s of streamlines) {
    const run = lastRunByStreamline.get(s.id);
    const upstream: LineageRef[] = s.reads.map((slug) => {
      const rel = relationBySlug.get(slug);
      const asModel = modelByName.get(splitQualified(slug).leaf);
      if (rel) return { name: slug, id: entityId(rel.type, slug), kind: rel.type };
      if (asModel) return { name: slug, id: entityId("model", asModel.name), kind: "model" as const };
      return { name: slug, id: null, kind: "table" as const };
    });
    const downstream: LineageRef[] = s.writes.map((slug) => {
      const rel = relationBySlug.get(slug);
      return rel
        ? { name: slug, id: entityId(rel.type, slug), kind: rel.type }
        : { name: slug, id: null, kind: "table" as const };
    });
    const links: EntityLink[] = [
      { label: "Open in operations", href: `/functions/${s.source_key}` },
    ];
    entities.push({
      id: entityId("source", s.source_key),
      kind: "source",
      group: "functions",
      slug: s.source_key,
      qualified: s.source_key,
      displayName: resolveDisplayName(s.source_key),
      schema: s.layer,
      format: "Fetch",
      location: `Source function · ${s.layer}${s.cadence_tag ? ` · ${s.cadence_tag}` : " · on demand"}${s.external ? " · external" : ""}`,
      description: null,
      author: null,
      columns: [],
      stats: { rows: null, bytes: null, size: null },
      tags: [s.layer, s.cadence_tag ?? "on demand", ...(s.external ? ["external"] : [])],
      cadence: s.cadence_tag,
      layer: s.layer,
      enabled: s.enabled,
      reads: s.reads,
      writes: s.writes,
      upstream,
      downstream,
      last: run ? { at: run.created_at, runId: run.id, verb: "run" } : null,
      links,
      materialization: null,
    });
  }

  // -- grouping -----------------------------------------------------------
  const bySchema = (group: EntityGroup, order: string[]) => {
    const buckets = new Map<string, Entity[]>();
    for (const e of entities.filter((x) => x.group === group)) {
      const key = e.schema ?? "other";
      buckets.set(key, [...(buckets.get(key) ?? []), e]);
    }
    const keys = [...buckets.keys()].sort(
      (a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99) || a.localeCompare(b),
    );
    return keys.map((key) => ({
      key,
      label: key,
      entities: (buckets.get(key) ?? []).sort((a, b) => a.slug.localeCompare(b.slug)),
    }));
  };

  const allGroups: Group[] = [
    {
      key: "warehouse",
      label: "Warehouse",
      sublabel: "Postgres relations you can query",
      sections: bySchema("warehouse", ["raw", "marts", "public"]),
    },
    {
      key: "dbt",
      label: "dbt models",
      sublabel: "The transformation graph",
      sections: bySchema("dbt", ["staging", "intermediate", "bronze", "marts", "global"]),
    },
    {
      key: "functions",
      label: "Source functions",
      sublabel: "Fetch and enrichment streamlines",
      sections: bySchema("functions", ["bronze", "silver", "gold", "universal"]),
    },
  ];
  const groups = allGroups.filter((g) => g.sections.length > 0);

  return {
    groups,
    entities,
    index: buildSearchIndex(entities),
    warehouseReachable: inputs.warehouseReachable,
    manifestReachable: inputs.manifestReachable,
  };
}

export function previewOf(e: Entity): Preview {
  return {
    id: e.id,
    kind: e.kind,
    display: e.displayName ?? e.slug,
    slug: e.slug,
    qualified: e.qualified,
    group: e.group,
    location: e.location,
    format: e.format,
    description: e.description,
    rows: e.stats.rows,
    size: e.stats.size,
    columnCount: e.columns.length,
    columns: e.columns.slice(0, 8).map((c) => c.name),
    last: e.last ? `${e.last.verb} ${relative(e.last.at)}` : null,
    href: `/explorer/e/${e.id}`,
  };
}

export function buildSearchIndex(entities: Entity[]): SearchIndex {
  const columns: SearchIndex["columns"] = [];
  for (const e of entities)
    for (const c of e.columns)
      columns.push({ column: c.name, entity: e.displayName ?? e.slug, id: e.id, kind: e.kind });
  return { entities: entities.map(previewOf), columns };
}

export function lastActivityText(last: LastActivity): string | null {
  return last ? `${last.verb} ${relative(last.at)}` : null;
}

export function entityById(catalog: Catalog, id: string): Entity | undefined {
  return catalog.entities.find((e) => e.id === id);
}

// ---- live gathering -----------------------------------------------------

const here = dirname(fileURLToPath(import.meta.url));
// src -> control-api -> apps -> control -> repo root
const REPO_ROOT = resolve(here, "..", "..", "..", "..");

function manifestPath(): string {
  return process.env.MDP_DBT_MANIFEST_PATH ?? resolve(REPO_ROOT, "dbt", "target", "manifest.json");
}
function catalogPath(): string {
  return process.env.MDP_DBT_CATALOG_PATH ?? resolve(REPO_ROOT, "dbt", "target", "catalog.json");
}

let manifestCache: { key: string; models: ManifestModel[]; sources: ManifestSource[] } | null = null;

function readJson(path: string): unknown | null {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}
function mtimeKey(...paths: string[]): string {
  return paths
    .map((p) => {
      try {
        return String(statSync(p).mtimeMs);
      } catch {
        return "0";
      }
    })
    .join(":");
}

const manifestNode = z.object({
  name: z.string(),
  alias: z.string().optional(),
  schema: z.string().nullable().optional(),
  description: z.string().optional(),
  materialized: z.string().nullable().optional(),
  tags: z.array(z.string()).optional(),
  meta: z.record(z.string(), z.unknown()).optional(),
  path: z.string().optional(),
  config: z.object({ schema: z.string().nullable().optional(), materialized: z.string().nullable().optional() }).partial().optional(),
  columns: z.record(z.string(), z.object({ name: z.string(), description: z.string().optional(), data_type: z.string().nullable().optional(), meta: z.record(z.string(), z.unknown()).optional() })).optional(),
  depends_on: z.object({ nodes: z.array(z.string()).optional() }).optional(),
});

export function readDbtManifest(): { models: ManifestModel[]; sources: ManifestSource[] } {
  const key = mtimeKey(manifestPath(), catalogPath());
  if (manifestCache && manifestCache.key === key)
    return { models: manifestCache.models, sources: manifestCache.sources };
  const raw = readJson(manifestPath());
  if (!raw || typeof raw !== "object") {
    manifestCache = { key, models: [], sources: [] };
    return { models: [], sources: [] };
  }
  const doc = z.object({
    metadata: z.object({ adapter_type: z.string().optional() }).optional(),
    nodes: z.record(z.string(), z.unknown()).optional(),
    sources: z.record(z.string(), z.unknown()).optional(),
    child_map: z.record(z.string(), z.array(z.string())).optional(),
  }).parse(raw);
  const catalogDoc = z.object({
    nodes: z.record(z.string(), z.object({
      columns: z.record(z.string(), z.object({ type: z.string().optional() })).optional(),
    })).optional(),
  }).nullable().parse(readJson(catalogPath()));
  const childMap = doc.child_map ?? {};
  const models: ManifestModel[] = [];
  for (const [uid, node] of Object.entries(doc.nodes ?? {})) {
    if (!uid.startsWith("model.")) continue;
    const parsed = manifestNode.safeParse(node);
    if (!parsed.success) continue;
    const n = parsed.data;
    const catalogCols = catalogDoc?.nodes?.[uid]?.columns ?? {};
    const columns: Column[] = Object.values(n.columns ?? {}).map((c) => ({
      name: c.name,
      type: c.data_type ?? catalogCols[c.name]?.type ?? null,
      nullable: null, // dbt manifest does not carry nullability; physical relation supplies it
      description: c.description?.trim() || null,
    }));
    const folder = (n.path ?? "").split("/")[0] || "models";
    models.push({
      name: n.name,
      alias: n.alias ?? n.name,
      schema: doc.metadata?.adapter_type === "postgres"
        ? n.schema ?? n.config?.schema ?? null
        : n.config?.schema ?? n.schema ?? null,
      description: n.description ?? null,
      materialized: n.config?.materialized ?? n.materialized ?? null,
      tags: n.tags ?? [],
      meta: n.meta ?? {},
      columns: sortColumns(columns),
      upstream: (n.depends_on?.nodes ?? []).filter((u) => u.startsWith("model.") || u.startsWith("source.")),
      downstream: (childMap[uid] ?? []).filter((u) => u.startsWith("model.")),
      folder,
    });
  }
  const sources: ManifestSource[] = [];
  for (const [uid, node] of Object.entries(doc.sources ?? {})) {
    if (!uid.startsWith("source.")) continue;
    const s = z
      .object({
        name: z.string(),
        source_name: z.string(),
        description: z.string().optional(),
        meta: z.record(z.string(), z.unknown()).optional(),
      })
      .safeParse(node);
    if (!s.success) continue;
    sources.push({
      sourceName: s.data.source_name,
      name: s.data.name,
      description: s.data.description ?? null,
      meta: s.data.meta ?? {},
    });
  }
  manifestCache = { key, models, sources };
  return { models, sources };
}

// Read-only warehouse connection through the non-admin reader role.
let reader: postgres.Sql | undefined;
let readerTried = false;
export function warehouseReader(): postgres.Sql | null {
  if (readerTried) return reader ?? null;
  readerTried = true;
  const url = process.env.MDP_READER_URL;
  if (!url) return null;
  reader = postgres(url, { max: 4, connect_timeout: 5, idle_timeout: 20 });
  return reader;
}

const relationRow = z.object({ table_schema: z.string(), table_name: z.string(), table_type: z.string() });
const columnRow = z.object({
  table_schema: z.string(),
  table_name: z.string(),
  column_name: z.string(),
  data_type: z.string(),
  is_nullable: z.string(),
});
const statRow = z.object({ schema: z.string(), name: z.string(), est_rows: z.string(), bytes: z.string() });

async function loadWarehouse(): Promise<{ relations: WarehouseRelation[]; physicalRelations?: { schema: string; name: string }[]; reachable: boolean }> {
  const db = warehouseReader();
  if (!db) return { relations: [], reachable: false };
  try {
    const [tables, columns, stats] = await Promise.all([
      rows(
        db,
        relationRow,
        "SELECT table_schema,table_name,table_type FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_schema,table_name",
      ),
      rows(
        db,
        columnRow,
        "SELECT table_schema,table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name,ordinal_position",
      ),
      rows(
        db,
        statRow,
        "SELECT n.nspname AS schema,c.relname AS name,c.reltuples::bigint::text AS est_rows,pg_total_relation_size(c.oid)::text AS bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','v','m','p')",
      ),
    ]);
    const colsByRel = new Map<string, Column[]>();
    for (const c of columns) {
      const key = `${c.table_schema}.${c.table_name}`;
      const list = colsByRel.get(key) ?? [];
      list.push({
        name: c.column_name,
        type: c.data_type,
        nullable: c.is_nullable === "YES",
        description: null,
      });
      colsByRel.set(key, list);
    }
    const statByRel = new Map(stats.map((s) => [`${s.schema}.${s.name}`, s]));
    const relations: WarehouseRelation[] = tables.map((t) => {
      const key = `${t.table_schema}.${t.table_name}`;
      const stat = statByRel.get(key);
      const est = stat ? Number(stat.est_rows) : NaN;
      return {
        schema: t.table_schema,
        name: t.table_name,
        type: t.table_type === "VIEW" ? "view" : "table",
        columns: sortColumns(colsByRel.get(key) ?? []),
        rows: Number.isFinite(est) && est >= 0 ? est : null,
        bytes: stat ? Number(stat.bytes) : null,
      };
    });
    return { relations, physicalRelations: stats, reachable: true };
  } catch {
    return { relations: [], reachable: false };
  }
}

export async function gatherCatalog(context: CatalogContext): Promise<Catalog> {
  const { models, sources } = readDbtManifest();
  const [warehouse, streamlines, lastRuns] = await Promise.all([
    loadWarehouse(),
    rows(context.db, streamlineDto, "SELECT * FROM control.streamline ORDER BY source_key").catch(
      (): StreamlineRow[] => [],
    ),
    rows(
      context.db,
      z.object({
        streamline_id: z.string(),
        id: z.string(),
        status: z.string(),
        created_at: z.string(),
        rows_written: z.string(),
      }),
      "SELECT DISTINCT ON (streamline_id) streamline_id::text,id::text,status,created_at::text,rows_written::text FROM control.run WHERE streamline_id IS NOT NULL ORDER BY streamline_id,created_at DESC",
    ).catch((): LastRun[] => []),
  ]);
  const catalog = assembleCatalog({
    relations: context.identity?.admin === false ? warehouse.relations.filter(r => ["marts","intermediate","staging","catalog","explore_marts","explore_intermediate","explore_staging"].includes(r.schema)) : warehouse.relations,
    streamlines,
    lastRuns,
    models,
    sources,
    physicalRelations: warehouse.physicalRelations ?? [],
    warehouseReachable: warehouse.reachable,
    manifestReachable: models.length > 0 || sources.length > 0,
  });
  const rights=await rows(context.db,z.object({source_key:z.string(),category:z.string(),rights_status:z.string(),learning_eligible:z.boolean(),resale_permitted:z.boolean()}),"SELECT source_key,category,rights_status,learning_eligible,resale_permitted FROM control.rights_source");
  // Function pages describe their declared writers; warehouse relations use generated labels.
  // A missing registry row never becomes an affirmative permission.
  const entityMap=new Map(catalog.entities.map(e=>[e.id,e]));
  function writers(entity:Entity,seen=new Set<string>()):Set<string>{
    if(seen.has(entity.id))return new Set();seen.add(entity.id);
    const keys=new Set(streamlines.filter(s=>entity.group==='functions'?s.source_key===entity.slug:s.writes.includes(entity.qualified)).map(s=>s.source_key));
    const refs=[...entity.upstream];
    if(entity.group==='warehouse'){
      const model=catalog.entities.find(e=>e.group==='dbt'&&e.slug===splitQualified(entity.qualified).leaf);
      if(model)for(const key of writers(model,seen))keys.add(key);
    }
    for(const ref of refs){
      const parent=ref.id?entityMap.get(ref.id):catalog.entities.find(e=>e.qualified===ref.name);
      if(parent)for(const key of writers(parent,seen))keys.add(key);
    }
    return keys;
  }
  for(const entity of catalog.entities){
    if(entity.group!=="functions"){
      entity.labels={...warehouseLabels(entity.qualified,entity.schema),per_row:entity.columns.some(c=>c.name==='learning_eligible')};
      continue;
    }
    const keys=writers(entity),selected=rights.filter(r=>keys.has(r.source_key));
    const owners=streamlines.filter(s=>keys.has(s.source_key));
    const rawScope=entity.schema==='raw'?writerScope(owners.map(s=>s.tenant_bound)):undefined;
    entity.labels={...(rawScope?{tenant:rawScope}:{}),per_row:entity.columns.some(c=>c.name==='learning_eligible'),
      ...(selected.length?{category:[...new Set(selected.map(r=>r.category))].join(', '),rights_status:[...new Set(selected.map(r=>r.rights_status))].join(', ')}:{}),
      ...(selected.length&&selected.length===keys.size?{learning_eligible:!rawScope&&!entity.schema?.startsWith('tenant_')&&!entity.tags.includes('scope:tenant')&&selected.every(r=>r.learning_eligible),resale_permitted:selected.every(r=>r.resale_permitted)}:{})};
  }
  return catalog;
}

export function localModelBuildCommand(name: string, tenant = false): string {
  const scope = tenant ? "DBT_MDP_SCOPE=tenant:00000000-0000-0000-0000-000000000001 " : "";
  const vars = tenant ? '{dry_run: true, tenant_slug: demo}' : '{dry_run: true, cycle_opened_at: "2026-09-20T23:00:00Z"}';
  return `source ops/local/env.sh && ${scope}uv run --project dbt dbt build --target pg_local --vars '${vars}' --select +${name} --indirect-selection cautious`;
}
