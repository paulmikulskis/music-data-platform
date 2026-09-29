import { permittedInput, readableInputs, workbenchLink } from "./workbench-inputs.js";
import { LabelChips } from "./primitives.js";
import { relationLabels } from "./console-semantics.js";
import { Hono } from "hono";
import { Layout } from "./pages.js";
import { RelativeTime } from "./run-components.js";
import { AppError } from "./db.js";
import type { Context } from "./router.js";
import {
  gatherCatalog,
  entityById,
  splitQualified,
  lastActivityText,
  type Catalog,
  type Entity,
  type EntityKind,
  type LineageRef,
} from "./explorer-data.js";
import {
  explorerCss,
  explorerScript,
  GLYPHS,
  KIND_LABEL,
} from "./explorer-visual.js";

export const explorerPages = new Hono<{ Variables: { context: Context } }>();

function Glyph({ kind }: { kind: EntityKind }) {
  return <span class="ex-glyph" dangerouslySetInnerHTML={{ __html: GLYPHS[kind] }} />;
}

// Renders a qualified name so the eye lands on the specific leaf, with the prefix de-emphasized.
function QualifiedName({ name }: { name: string }) {
  const { prefix, leaf } = splitQualified(name);
  return (
    <>
      {prefix ? <span class="qn-prefix">{prefix}</span> : null}
      <span class="qn-leaf">{leaf}</span>
    </>
  );
}

function CopySlug({ value }: { value: string }) {
  return (
    <button type="button" class="ex-copy" data-copy={value} title="Copy identifier" aria-label={`Copy ${value}`}>
      {value} ⧉
    </button>
  );
}

// Compact activity for the tree rows: temporal only, kept on one line. The opaque run id
// (temporal + linked) is shown in full on the entity page header and the hover popover.
function LastActivity({ entity }: { entity: Entity }) {
  if (!entity.last) return null;
  return (
    <span class="ex-when">
      <span class="ex-dot" />
      {entity.last.verb} <RelativeTime at={entity.last.at} />
    </span>
  );
}

function EntityRow({ entity }: { entity: Entity }) {
  return (
    <a class="ex-row" href={`/explorer/e/${entity.id}`} data-entity-id={entity.id}>
      <Glyph kind={entity.kind} />
      <span class="ex-name">
        <span class={entity.displayName ? "ex-title" : "ex-title ex-title-slug"}>
          {entity.displayName ? entity.displayName : <QualifiedName name={entity.qualified} />}
        </span>
        {entity.displayName ? <span class="ex-slug">{entity.slug}</span> : null}
        <LabelChips labels={{...relationLabels(entity.qualified,entity.tags,entity.layer),...entity.labels}}/>
        {entity.availability === "not_built" && <p class="ex-notice">Not built here. Build locally: <code>{entity.buildCommand}</code></p>}
        {entity.availability === "inline" && <p class="caption">Inline SQL. Open a downstream model to query its result.</p>}
      </span>
      <LastActivity entity={entity} />
    </a>
  );
}

function GroupPanel({ group }: { group: Catalog["groups"][number] }) {
  const count = group.sections.reduce((n, s) => n + s.entities.length, 0);
  return (
    <section class="ex-group">
      <header>
        <h2>{group.label}</h2>
        <p class="ex-sub">{group.sublabel} · {count} {count === 1 ? "entity" : "entities"}</p>
      </header>
      {group.sections.map((section, i) => (
        <details class="ex-section" open={i === 0}>
          <summary>
            <span>{section.label} <span class="ex-count">{section.entities.length}</span></span>
            <span class="ex-caret" aria-hidden="true">›</span>
          </summary>
          {section.entities.length ? (
            section.entities.map((entity) => <EntityRow entity={entity} />)
          ) : (
            <p class="ex-empty">No entities. <a href="/functions">Choose a function →</a></p>
          )}
        </details>
      ))}
    </section>
  );
}

function IndexScript({ catalog }: { catalog: Catalog }) {
  const json = JSON.stringify(catalog.index).replace(/</g, "\\u003c");
  return (
    <>
      <script type="application/json" id="ex-index" dangerouslySetInnerHTML={{ __html: json }} />
      <div id="ex-pop" class="ex-pop" role="dialog" aria-label="Entity preview" hidden />
      <script dangerouslySetInnerHTML={{ __html: explorerScript }} />
    </>
  );
}

function matches(entity: Entity, q: string): boolean {
  const needle = q.toLowerCase();
  return (
    entity.slug.toLowerCase().includes(needle) ||
    (entity.displayName ?? "").toLowerCase().includes(needle) ||
    entity.qualified.toLowerCase().includes(needle)
  );
}

export function renderIndex(catalog: Catalog, q = "") {
  const query = q.trim();
  const entityHits = query ? catalog.entities.filter((e) => matches(e, query)) : [];
  const columnHits = query
    ? catalog.entities
        .flatMap((e) => e.columns.filter((c) => c.name.toLowerCase().includes(query.toLowerCase())).map((c) => ({ e, c })))
        .slice(0, 40)
    : [];
  return Layout({
    title: "Explorer",
    subtitle: "",
    children: (
      <div class="ex">
        <form class="ex-search" method="get" action="/explorer" role="search">
          <input
            name="q"
            value={query}
            data-ex-input
            autocomplete="off"
            spellcheck={false}
            placeholder="Search names and columns…"
            aria-label="Search entities and columns"
          />
          <div class="ex-drop" data-ex-drop hidden />
          <p class="ex-hint">Search names or columns. Hover a result to preview it.</p>
        </form>
        {!catalog.warehouseReachable ? (
          <p class="ex-notice">Warehouse reader is not reachable; showing dbt models and source functions only.</p>
        ) : null}
        {query ? (
          <section class="ex-group" style="margin-bottom:20px">
            <header>
              <h2>Results for “{query}”</h2>
              <p class="ex-sub">{entityHits.length} {entityHits.length === 1 ? "entity" : "entities"} · {columnHits.length} column {columnHits.length === 1 ? "match" : "matches"} · <a href="/explorer">Clear</a></p>
            </header>
            {entityHits.length ? entityHits.map((entity) => <EntityRow entity={entity} />) : <p class="ex-empty">No matching names. Try another name or clear the search.</p>}
            {columnHits.length ? (
              <details class="ex-section" open>
                <summary><span>Column matches <span class="ex-count">{columnHits.length}</span></span><span class="ex-caret" aria-hidden="true">›</span></summary>
                {columnHits.map(({ e, c }) => (
                  <a class="ex-row" href={`/explorer/e/${e.id}`} data-entity-id={e.id}>
                    <Glyph kind={e.kind} />
                    <span class="ex-name"><span class="ex-title">{c.name}</span><span class="ex-slug">in {e.displayName ?? e.slug}</span></span>
                    <span class="ex-when">{c.type ?? ""}</span>
                  </a>
                ))}
              </details>
            ) : null}
          </section>
        ) : null}
        <div class="ex-groups">
          {catalog.groups.map((group) => <GroupPanel group={group} />)}
        </div>
        <IndexScript catalog={catalog} />
        <style dangerouslySetInnerHTML={{ __html: explorerCss }} />
      </div>
    ),
  });
}

function LineageNode({ ref }: { ref: LineageRef }) {
  const inner = (
    <>
      <Glyph kind={ref.kind} />
      <span><QualifiedName name={ref.name} /></span>
    </>
  );
  const cls = `ex-node ${ref.kind === "source" ? "is-source" : ""}`;
  return ref.id ? (
    <a class={cls} href={`/explorer/e/${ref.id}`} data-entity-id={ref.id} title={ref.name}>{inner}</a>
  ) : (
    <span class={cls} title={ref.name}>{inner}</span>
  );
}

function Lineage({ entity }: { entity: Entity }) {
  const upstream = entity.upstream;
  const downstream = entity.downstream;
  if (!upstream.length && !downstream.length) return null;
  const upLabel = entity.kind === "source" ? "Reads" : "Upstream";
  const downLabel = entity.kind === "source" ? "Writes" : "Downstream";
  return (
    <figure class="ex-lineage">
      <h3>Composition</h3>
      <p class="ex-lin-sub">What feeds this and what it feeds</p>
      <div class="ex-lin-group">
        <span>{upLabel}</span>
        {upstream.length ? (
          <div class="ex-nodes">{upstream.map((ref) => <LineageNode ref={ref} />)}</div>
        ) : (
          <p class="ex-lin-empty">None declared</p>
        )}
      </div>
      <div class="ex-lin-group">
        <span>{downLabel}</span>
        {downstream.length ? (
          <div class="ex-nodes">{downstream.map((ref) => <LineageNode ref={ref} />)}</div>
        ) : (
          <p class="ex-lin-empty">None declared</p>
        )}
      </div>
    </figure>
  );
}

function SchemaTable({ entity }: { entity: Entity }) {
  if (!entity.columns.length) return null;
  return (
    <section class="card" id="schema">
      <div class="row">
        <h2>Schema</h2>
        <span class="muted" data-col-count>{entity.columns.length} columns</span>
      </div>
      <label>Filter columns<input class="ex-col-filter" data-ex-col-filter placeholder="Search columns" aria-label="Filter columns" /></label>
      <div class="table-wrap expanded-table ex-schema-table">
        <table>
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th>Nullability</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {entity.columns.map((column) => (
              <tr data-col-row data-col-name={column.name.toLowerCase()}>
                <td><strong>{column.name}</strong></td>
                <td class="muted">{column.type ?? "unknown"}</td>
                <td>
                  {column.nullable === null ? (
                    <span class="muted">Unknown</span>
                  ) : column.nullable ? (
                    <span class="ex-nullable-y">nullable</span>
                  ) : (
                    <span class="ex-nullable-n">required</span>
                  )}
                </td>
                <td>{column.description ? column.description : <span class="muted">None</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FunctionShape({ entity }: { entity: Entity }) {
  if (entity.kind !== "source") return null;
  return (
    <section class="card">
      <div class="row">
        <h2>Function</h2>
        <span class={`badge ${entity.enabled ? "active" : "paused"}`}>{entity.enabled ? "active" : "paused"}</span>
      </div>
      <p class="muted">
        {entity.layer} · {entity.cadence ?? "on demand"}{entity.tags.includes("external") ? " · external source" : ""}
      </p>
      <div class="row" style="justify-content:flex-start;gap:32px;margin-top:8px">
        <div>
          <div class="ex-stat-label">Reads</div>
          <p>{entity.reads.length ? entity.reads.map((r) => <span class="ex-chip" style="margin-right:6px">{r}</span>) : <span class="muted">Nothing</span>}</p>
        </div>
        <div>
          <div class="ex-stat-label">Writes</div>
          <p>{entity.writes.length ? entity.writes.map((r) => <span class="ex-chip" style="margin-right:6px">{r}</span>) : <span class="muted">Control operation · no output</span>}</p>
        </div>
      </div>
    </section>
  );
}

export function renderEntity(entity: Entity, catalog: Catalog) {
  const stats: { value: string; label: string }[] = [];
  if (entity.stats.rows !== null) stats.push({ value: entity.stats.rows.toLocaleString("en-US"), label: "rows (est.)" });
  if (entity.stats.size) stats.push({ value: entity.stats.size, label: "on disk" });
  if (entity.columns.length) stats.push({ value: String(entity.columns.length), label: "columns" });
  return Layout({
    title: entity.displayName ?? entity.slug,
    titleMono: !entity.displayName,
    subtitle: entity.location,
    children: (
      <div class="ex ex-entity">
        <div class="ex-crumbs">
          <a href="/explorer">← Data explorer</a>
          <span class="muted">{KIND_LABEL[entity.kind]} · {entity.format}</span>
        </div>
        <LabelChips labels={{...relationLabels(entity.qualified,entity.tags,entity.layer),...entity.labels}}/>
        {entity.availability === "not_built" && <p class="ex-notice">Not built here. Build locally: <code>{entity.buildCommand}</code></p>}
        {entity.availability === "inline" && <p class="caption">Inline SQL. Open a downstream model to query its result.</p>}
        <div class="ex-head">
          <Glyph kind={entity.kind} />
        </div>
        <div class="ex-slug-line">
          <CopySlug value={entity.slug} />
          {entity.last ? (
            <span class="ex-when">
              <span class="ex-dot" />
              {entity.last.verb} <RelativeTime at={entity.last.at} />
              {entity.last.runId ? <> on run <a href={`/runs/${entity.last.runId}`}>{entity.last.runId.slice(0, 8)} →</a></> : null}
            </span>
          ) : null}
        </div>
        <p>SQL access depends on your login. Run <code>select * from catalog.relations</code> to see what it can read.</p>
        {entity.description ? <p class="lead">{entity.description}</p> : null}
        {entity.author ? (
          <p class="ex-author">Description authored by <strong>{entity.author === "agent" ? "an agent" : entity.author === "human" ? "a human" : entity.author}</strong></p>
        ) : null}
        {stats.length ? (
          <div class="ex-stat-strip">
            {stats.map((stat) => (
              <div class="ex-stat">
                <div class="ex-stat-value">{stat.value}</div>
                <div class="ex-stat-label">{stat.label}</div>
              </div>
            ))}
          </div>
        ) : null}
        {entity.links.length ? (
          <div class="ex-crumbs">
            {entity.links.map((link) => <a href={link.href}>{link.label} →</a>)}
          </div>
        ) : null}
        <FunctionShape entity={entity} />
        <SchemaTable entity={entity} />
        <div style="margin-top:22px">
          <Lineage entity={entity} />
        </div>
        <IndexScript catalog={catalog} />
        <style dangerouslySetInnerHTML={{ __html: explorerCss }} />
      </div>
    ),
  });
}

explorerPages.get("/", async (c) => {
  const catalog = await gatherCatalog(c.get("context"));
  return c.html(renderIndex(catalog, c.req.query("q") ?? ""));
});

explorerPages.get("/e/:id", async (c) => {
  const catalog = await gatherCatalog(c.get("context"));
  const entity = entityById(catalog, c.req.param("id"));
  if (!entity) throw new AppError("not_found", `No entity is catalogued as ${c.req.param("id")}`, 404);
  const name = entity.kind === "model" ? entity.slug : entity.qualified.split(".").at(-1);
  if (entity.schema && name) {
    const readable = permittedInput({ schema: entity.schema, name }, await readableInputs(c.get("context").identity.admin));
    if (readable) entity.links.push({ label: "Open in Workbench", href: workbenchLink(readable) });
  }
  return c.html(renderEntity(entity, catalog));
});

export { lastActivityText };
