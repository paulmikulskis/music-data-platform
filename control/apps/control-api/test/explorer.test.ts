import { describe, it, expect } from "vitest";
import {
  splitQualified,
  resolveDisplayName,
  assembleCatalog,
  entityId,
  decodeEntityId,
  entityById,
  type CatalogInputs,
} from "../src/explorer-data.js";
import { renderIndex, renderEntity } from "../src/explorer-page.js";
import { explorerCss } from "../src/explorer-visual.js";

const nowMinus = (mins: number) => new Date(Date.now() - mins * 60000).toISOString();
const runId = "33f03c9e-1111-2222-3333-444455556666";

function fixtureInputs(): CatalogInputs {
  return {
    warehouseReachable: true,
    manifestReachable: true,
    relations: [
      {
        schema: "raw",
        name: "billboard_chart_entries",
        type: "table",
        rows: 1238,
        bytes: 417792,
        columns: [
          { name: "platform_account_id", type: "text", nullable: false, description: null },
          { name: "followers", type: "bigint", nullable: true, description: null },
          { name: "_ingested_at", type: "timestamp with time zone", nullable: true, description: null },
        ],
      },
      {
        schema: "marts",
        name: "mart_chart_history",
        type: "table",
        rows: 42,
        bytes: 65536,
        columns: [{ name: "input_ref", type: "text", nullable: false, description: null }],
      },
      {
        schema: "public",
        name: "unmapped_relation",
        type: "view",
        rows: null,
        bytes: null,
        columns: [{ name: "some_col", type: "text", nullable: true, description: null }],
      },
    ],
    streamlines: [
      {
        id: "aaaaaaaa-0000-0000-0000-000000000001",
        source_key: "billboard_hot100",
        layer: "bronze",
        tenant_bound: false,
        writes: ["raw.billboard_chart_entries"],
        reads: [],
        external: true,
        cadence_tag: "hourly",
        enabled: true,
        allow_partial: false,
        batch_size: 1,
        max_concurrency: 1,
        timeout_s: 300,
        storage: "heap",
        acknowledged_fingerprints: [], parked_inputs: 0,
      },
      {
        id: "aaaaaaaa-0000-0000-0000-000000000002",
        source_key: "fixture_transform",
        layer: "silver",
        tenant_bound: false,
        writes: ["raw.fixture_transform"],
        reads: ["marts.mart_chart_history"],
        external: false,
        cadence_tag: "daily",
        enabled: false,
        allow_partial: false,
        batch_size: 1,
        max_concurrency: 1,
        timeout_s: 300,
        storage: "heap",
        acknowledged_fingerprints: [], parked_inputs: 0,
      },
    ],
    lastRuns: [
      {
        streamline_id: "aaaaaaaa-0000-0000-0000-000000000001",
        id: runId,
        status: "succeeded",
        created_at: nowMinus(8),
        rows_written: "1238",
      },
    ],
    models: [
      {
        name: "mart_chart_history",
        schema: "marts",
        description: "Latest follower growth per account.",
        materialized: "table",
        tags: ["silver", "scope:global", "cadence:daily"],
        meta: {},
        columns: [{ name: "input_ref", type: "text", nullable: null, description: "Business key" }],
        upstream: ["source.music_data_platform.raw.billboard_chart_entries", "model.music_data_platform.stg_billboard__chart_entries"],
        downstream: ["model.music_data_platform.silver_invoke__fixture_transform"],
        folder: "marts",
      },
      {
        name: "stg_billboard__chart_entries",
        schema: "main_staging",
        description: "",
        materialized: "view",
        tags: ["bronze", "scope:global", "cadence:hourly"],
        meta: { display_name: "Billboard Hot 100 snapshots (staged)" },
        columns: [{ name: "platform_account_id", type: null, nullable: null, description: null }],
        upstream: ["source.music_data_platform.raw.billboard_chart_entries"],
        downstream: ["model.music_data_platform.mart_chart_history"],
        folder: "staging",
      },
    ],
    sources: [
      {
        sourceName: "raw",
        name: "billboard_chart_entries",
        description: "Global account snapshots from Billboard Hot 100.",
        meta: { source_key: "billboard_hot100" },
      },
    ],
  };
}

describe("explorer data helpers", () => {
  it("splits qualified names so the leaf is emphasizable", () => {
    expect(splitQualified("raw.billboard_chart_entries")).toEqual({ prefix: "raw.", leaf: "billboard_chart_entries" });
    expect(splitQualified("stg_billboard__chart_entries")).toEqual({ prefix: "stg_billboard__", leaf: "chart_entries" });
    expect(splitQualified("billboard_hot100")).toEqual({ prefix: "", leaf: "billboard_hot100" });
  });

  it("resolves display_name by meta, then seed, then leaf-seed, else null", () => {
    expect(resolveDisplayName("raw.billboard_chart_entries", "Meta wins")).toBe("Meta wins");
    expect(resolveDisplayName("billboard_hot100")).toBe("Billboard Hot 100");
    expect(resolveDisplayName("raw.billboard_chart_entries")).toBe("Billboard chart entries");
    expect(resolveDisplayName("some_unknown_relation")).toBeNull();
  });

  it("round-trips entity ids", () => {
    const id = entityId("table", "raw.billboard_chart_entries");
    expect(id).toBe("table~raw.billboard_chart_entries");
    expect(decodeEntityId(id)).toEqual({ kind: "table", identifier: "raw.billboard_chart_entries" });
    expect(decodeEntityId("nope")).toBeNull();
  });
});

describe("assembleCatalog", () => {
  const catalog = assembleCatalog(fixtureInputs());

  it("shows manifest column descriptions on warehouse tables while keeping their physical types", async () => {
    const table = entityById(catalog, entityId("table", "marts.mart_chart_history"))!;
    expect(table.columns).toEqual([
      { name: "input_ref", type: "text", nullable: false, description: "Business key" },
    ]);
    expect(entityById(catalog, entityId("table", "raw.billboard_chart_entries"))!.columns[0]!.description).toBeNull();
    expect(String(await renderEntity(table, catalog))).toContain("Business key");
  });

  it("groups warehouse, dbt and functions", () => {
    expect(catalog.groups.map((g) => g.key)).toEqual(["warehouse", "dbt", "functions"]);
    const warehouse = catalog.groups.find((g) => g.key === "warehouse")!;
    expect(warehouse.sections.map((s) => s.label)).toEqual(["raw", "marts", "public"]);
  });

  it("enriches a warehouse table with display_name, provider, producer and temporal-linked activity", () => {
    const table = entityById(catalog, entityId("table", "raw.billboard_chart_entries"))!;
    expect(table.displayName).toBe("Billboard chart entries");
    expect(table.description).toBe("Global account snapshots from Billboard Hot 100.");
    expect(table.location).toContain("source billboard_hot100");
    expect(table.stats.rows).toBe(1238);
    expect(table.stats.size).toBe("408 kB");
    expect(table.last).toEqual({ at: expect.any(String), runId, verb: "landed" });
    expect(table.upstream.some((u) => u.name === "billboard_hot100")).toBe(true);
    expect(table.links.some((l) => l.href === "/functions/billboard_hot100")).toBe(true);
  });

  it("honors dbt meta.display_name over the seed and carries lineage", () => {
    const model = entityById(catalog, entityId("model", "stg_billboard__chart_entries"))!;
    expect(model.displayName).toBe("Billboard Hot 100 snapshots (staged)");
    expect(model.upstream.some((u) => u.name === "raw.billboard_chart_entries")).toBe(true);
    expect(model.downstream.some((d) => d.name === "mart_chart_history")).toBe(true);
  });

  it("models a source function's reads/writes as lineage and keeps its enabled state", () => {
    const fn = entityById(catalog, entityId("source", "billboard_hot100"))!;
    expect(fn.displayName).toBe("Billboard Hot 100");
    expect(fn.enabled).toBe(true);
    expect(fn.downstream.some((d) => d.name === "raw.billboard_chart_entries" && d.id !== null)).toBe(true);
    const paused = entityById(catalog, entityId("source", "fixture_transform"))!;
    expect(paused.enabled).toBe(false);
  });

  it("indexes entities and columns for search", () => {
    expect(catalog.index.entities.length).toBe(catalog.entities.length);
    expect(catalog.index.columns.some((c) => c.column === "followers")).toBe(true);
  });
});

describe("explorer rendering", () => {
  const catalog = assembleCatalog(fixtureInputs());

  it("renders the index with search, display names, slugs and the inline index", async () => {
    const html = String(await renderIndex(catalog));
    expect(html).toContain("Explorer");
    expect(html).toContain('data-ex-input');
    expect(html).toContain("Billboard chart entries"); // display name
    expect(html).toContain("raw.billboard_chart_entries"); // demoted slug
    expect(html).toContain("Warehouse");
    expect(html).toContain("Source functions");
    expect(html).toContain('id="ex-index"');
    expect(html).toContain('id="ex-pop"');
  });

  it("server-filters when a query is supplied (works without JS)", async () => {
    const html = String(await renderIndex(catalog, "follow"));
    expect(html).toContain("Results for");
    expect(html).toContain("Column matches");
    expect(html).toContain("followers");
  });

  it("renders an entity view with schema, stats, copyable slug and a linked run", async () => {
    const table = entityById(catalog, entityId("table", "raw.billboard_chart_entries"))!;
    const html = String(await renderEntity(table, catalog));
    expect(html).toContain("platform_account_id");
    expect(html).toContain("required"); // non-nullable rendering
    expect(html).toContain("nullable");
    expect(html).toContain("1,238"); // formatted row estimate
    expect(html).toContain("408 kB");
    expect(html).toContain(`data-copy="raw.billboard_chart_entries"`);
    expect(html).toContain(`/runs/${runId}`); // temporal id is linked, never bare
    expect(html).toContain("Composition"); // lineage frame
    expect(html).toContain("/functions/billboard_hot100"); // cross link
  });

  it("uses hierarchical name emphasis for slug-titled entities", async () => {
    const unmapped = entityById(catalog, entityId("view", "public.unmapped_relation"))!;
    // no seed and no meta display name, so the qualified name renders with emphasis
    expect(unmapped.displayName).toBeNull();
    const indexHtml = String(await renderIndex(catalog));
    expect(indexHtml).toContain("qn-leaf");
    expect(indexHtml).toContain("qn-prefix");
  });
});

describe("house aesthetic guardrails", () => {
  it("never uses a single-side border accent rail", () => {
    expect(explorerCss).not.toMatch(/border-left/);
    expect(explorerCss).not.toMatch(/border-right\s*:/);
    expect(explorerCss).not.toMatch(/inset\s+\d+px\s+0\s+0/);
  });
});

describe("physical model availability", () => {
  it.each(["tenant_live_marts", "marts", null])("omits tenant availability with physical schema %s and a global manifest", async (schema) => {
    const inputs = fixtureInputs();
    inputs.models = inputs.models.filter((model) => model.name === "mart_chart_history").map((model) => ({
      ...model,
      name: "mart_creator_directory",
      tags: ["scope:tenant"],
    }));
    inputs.relations = [];
    inputs.physicalRelations = schema ? [{ schema, name: "mart_creator_directory" }] : [];
    const catalog = assembleCatalog(inputs);
    const model = entityById(catalog, entityId("model", "mart_creator_directory"))!;
    expect(model.availability).toBe("unknown");
    expect(model.buildCommand).toBeUndefined();
    expect(model.location).not.toContain("Not built here");
    expect(String(await renderEntity(model, catalog))).not.toContain("Not built here");
    expect(model.links.some((link) => link.label.startsWith("Warehouse"))).toBe(false);
  });

  it("marks missing relations with a local build command", async () => {
    const inputs = fixtureInputs();
    inputs.relations = [];
    const catalog = assembleCatalog(inputs);
    const model = entityById(catalog, entityId("model", "mart_chart_history"))!;
    expect(model.availability).toBe("not_built");
    expect(model.buildCommand).toContain("--select +mart_chart_history");
    expect(String(await renderEntity(model, catalog))).toContain("Not built here");
  });

  it("does not mistake another schema's namesake for the model", () => {
    const inputs = fixtureInputs();
    inputs.relations = inputs.relations.map((relation) => ({ ...relation, schema: "tenant_demo_marts" }));
    const model = assembleCatalog(inputs).entities.find((entity) => entity.kind === "model" && entity.slug === "mart_chart_history");
    expect(model?.availability).toBe("not_built");
  });

  it("keeps unknown distinct from absent and recognizes physical metadata without SELECT grants", () => {
    const inputs = fixtureInputs();
    inputs.warehouseReachable = false;
    expect(assembleCatalog(inputs).entities.find((entity) => entity.kind === "model")?.availability).toBe("unknown");
    inputs.warehouseReachable = true;
    inputs.relations = [];
    inputs.physicalRelations = [{ schema: "marts", name: "mart_chart_history" }];
    expect(assembleCatalog(inputs).entities.find((entity) => entity.kind === "model")?.availability).toBe("built");
  });
});
