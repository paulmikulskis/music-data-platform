import { AppError } from "./db.js";
import { errorHint } from "@mdp/contracts";
import {
  readableInputs,
  safeCopy,
  chartExample,
  relationFromName,
  permittedInput,
  inputQuery,
} from "./workbench-inputs.js";
import { LabelChips } from "./label-chips.js";
import {
  workbenchScript,
  workbenchCss,
  bareModel,
  rowLabel,
  isNumericType,
} from "./workbench-visual.js";
import { Hono } from "hono";
import { getCookie, setCookie } from "hono/cookie";
import { createRouterClient } from "@orpc/server";
import { z } from "zod";
import { router, type Context } from "./router.js";
import { RelativeTime } from "./run-components.js";
import {
  CopyChip,
  EmptyState,
  EntityName,
  EntityPopover,
  RunRef,
  SchemaDisclosure,
} from "./primitives.js";
import { Layout } from "./pages.js";
import {
  PreviewResult,
  BacktestResult,
  ExplainResult,
  AsyncStatus,
} from "../../../packages/contracts/src/workbench.js";

// GET displays this text. Only the explicit Run action executes a query.
export function queryPrefill(value: unknown) {
  const parsed = z
    .string()
    .refine((text) => Buffer.byteLength(text, "utf8") <= 4096)
    .safeParse(value);
  return parsed.success ? parsed.data : null;
}

export const workbenchPages = new Hono<{ Variables: { context: Context } }>();
function actionFailure(error: unknown) {
  const service = z
    .object({ data: z.object({ error_class: z.string() }) })
    .safeParse(error);
  return {
    message:
      error instanceof Error
        ? error.message
        : "Operation failed. Edit the SQL and rerun.",
    error_class:
      error instanceof AppError
        ? error.error_class
        : service.success
          ? service.data.data.error_class
          : undefined,
  };
}
const text = (value: unknown): string =>
  value == null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
const cell = (value: unknown) =>
  text(value).length > 65 ? (
    <details class="cell-value">
      <summary title={text(value)}>{text(value)}</summary>
      <p>{text(value)}</p>
    </details>
  ) : (
    text(value)
  );
const hidden = (fields: Record<string, string>) =>
  Object.entries(fields)
    .filter(([key]) => key !== "action")
    .map(([key, value]) => <input type="hidden" name={key} value={value} />);
const copy = (value: string, label = value) => (
  <button type="button" class="copy" data-copy={value} title="Copy">
    {label}
  </button>
);
const css = `.wb{min-width:0}.wb .card{min-width:0}.wb .tools{display:flex;gap:10px;justify-content:flex-start;align-items:center;flex-wrap:wrap}.wb .editor{font:13px/1.7 ui-monospace,monospace;min-height:230px;tab-size:2;background:var(--paper);resize:vertical}.wb .controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.wb input,.wb select{max-width:100%;width:100%}.wb .workspace{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:22px}.wb .history{max-height:520px;overflow:auto}.wb .history a{display:block;padding:12px 0;border-bottom:1px solid var(--line);font-size:12px;overflow-wrap:anywhere}.wb .facts{display:flex;gap:18px;flex-wrap:wrap;padding:12px 0;color:var(--muted);font-size:12px;border-bottom:1px solid var(--line);margin-bottom:16px}.wb .table-wrap{max-height:430px}.wb th button{font:inherit;letter-spacing:inherit;text-transform:inherit;background:transparent;color:var(--teal);padding:0;border:0}.wb .inspector{position:fixed;z-index:20;right:18px;top:110px;bottom:24px;width:320px;max-width:calc(100vw - 32px);overflow:auto;background:white;border:1px solid var(--line);border-radius:var(--card-radius);padding:22px;box-shadow:0 12px 44px color-mix(in srgb,var(--foreground) 12%,transparent)}.wb .inspector[hidden]{display:none}.wb .inspector dd{margin:4px 0 18px;overflow-wrap:anywhere;font-size:13px}.wb .inspector dt{font-size:11px;color:var(--muted)}.wb .patch-commands code{white-space:pre-wrap;overflow-wrap:anywhere}.wb .patch-commands li{margin-bottom:12px}.wb .diff-line{display:block;white-space:pre-wrap}.wb .plus{background:color-mix(in srgb,var(--teal) 10%,white)}.wb .minus{background:color-mix(in srgb,var(--danger) 10%,white)}.wb .ticket{display:flex;gap:18px;align-items:center;flex-wrap:wrap}.wb .heartbeat{display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%;background:var(--teal);animation:wb-pulse 1.4s infinite}.wb .copy{max-width:100%;white-space:normal;overflow-wrap:anywhere}.wb .caption{color:var(--muted);font-size:12px;overflow-wrap:anywhere}.wb details{margin-top:14px}.wb .empty{text-align:center;padding:32px}.wb .summary{font-size:13px}.wb .button{padding:8px 10px;display:inline-block}.wb .result-top{align-items:flex-start}.wb .history small{display:block;color:var(--muted)}@keyframes wb-pulse{50%{opacity:.3}}@media(max-width:850px){.wb .workspace{grid-template-columns:minmax(0,1fr)}.wb .history{max-height:180px}.wb .controls{grid-template-columns:minmax(0,1fr)}.wb .inspector{top:80px;right:16px}.wb .ticket{align-items:flex-start}.wb pre{max-width:100%}}@media(prefers-reduced-motion:reduce){.wb .heartbeat{animation:none}}`;

function FreshnessDot({ rows }: { rows: Record<string, unknown>[] }) {
  const timestamps = rows
    .map((row) => row.last_published_at)
    .filter(
      (at): at is string =>
        typeof at === "string" && Number.isFinite(Date.parse(at)),
    )
    .sort((a, b) => Date.parse(a) - Date.parse(b));
  const at = timestamps[0];
  const label = rows
    .map(
      (row) =>
        `${text(row.source_key)} · ${row.last_published_at ? text(row.last_published_at) : "No source timestamp"}`,
    )
    .join("; ");
  return (
    <span
      class={`lineage-freshness ${at ? "published" : "unknown"}`}
      title={label}
      aria-label={label}
    >
      {at ? <RelativeTime at={at} /> : <span>No source timestamp</span>}
    </span>
  );
}

export function CompositionFrame({
  data,
  model,
  cycle,
  cycleOpenedAt,
  comparisonCycle,
  explanation,
  artifact,
}: {
  data: z.infer<typeof PreviewResult>;
  model: string;
  cycle: string;
  comparisonCycle?: string | undefined;
  cycleOpenedAt?: string | undefined;
  explanation: z.infer<typeof ExplainResult> | null;
  artifact: { url: string } | null;
}) {
  const counts = new Map<string, number>();
  for (const column of data.columns) {
    if (column.source) {
      const key = bareModel(column.source);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  const feeders = data.upstream.map((id) => ({
    id,
    key: bareModel(id),
    source: id.startsWith("source."),
    count: counts.get(bareModel(id)) || 0,
  }));
  const maxCount = Math.max(1, ...feeders.map((feeder) => feeder.count));
  const freshness = explanation?.sourceFreshness || [];
  const matchedFreshness = feeders.some(
    (feeder) =>
      feeder.source &&
      freshness.some((row) => bareModel(text(row.source_key)) === feeder.key),
  );
  const runs = (explanation?.producingRuns || []).flatMap((run) =>
    typeof run.id === "string" && run.id ? [{ ...run, id: run.id }] : [],
  );
  const latestRun = runs[0]; // explain returns producing runs newest first.
  return (
    <figure class="lineage" aria-label={`Inputs to ${bareModel(model)}`}>
      {data.labels && <LabelChips labels={data.labels} />}
      <figcaption>
        <div class="lineage-heading">
          <EntityName slug={model} as="strong" />
          <span class="caption lineage-facts">
            {latestRun ? (
              <RunRef id={latestRun.id} at={cycleOpenedAt ?? null} />
            ) : cycle ? (
              <RunRef id={cycle} kind="cycle" />
            ) : null}
            {comparisonCycle && (
              <>
                {" "}
                → <RunRef id={comparisonCycle} kind="cycle" />
              </>
            )}
            <span>
              {rowLabel(data.rows.length, data.truncated, data.totalRows)} ·{" "}
              {(data.timingMs / 1000).toFixed(2)}s
            </span>
          </span>
        </div>
        <div class="lineage-actions">
          {!matchedFreshness && freshness.length > 0 && (
            <FreshnessDot rows={freshness} />
          )}
          {runs.length > 0 && (
            <details class="lineage-runs">
              <summary>{runs.length} runs</summary>
              <ul>
                {runs.map((run) => (
                  <li>
                    <RunRef id={run.id} />
                  </li>
                ))}
              </ul>
            </details>
          )}
          {artifact?.url && (
            <a
              class="result-file"
              href={artifact.url}
              title={data.artifactRef}
              download
              aria-label="Download result file"
            >
              result file ↓
            </a>
          )}
        </div>
      </figcaption>
      {feeders.length ? (
        <div class="nodes">
          {feeders.slice(0, 8).map((feeder) => {
            const matched = feeder.source
              ? freshness.filter(
                  (row) => bareModel(text(row.source_key)) === feeder.key,
                )
              : [];
            const shape = data.columns.filter(
              (column) =>
                column.source && bareModel(column.source) === feeder.key,
            );
            const qualified = feeder.source
              ? feeder.id.split(".").slice(-2).join(".")
              : feeder.key;
            const dot = qualified.lastIndexOf(".");
            const published = matched
              .map((row) => row.last_published_at)
              .filter(
                (at): at is string =>
                  typeof at === "string" && Number.isFinite(Date.parse(at)),
              )
              .sort()[0];
            return (
              <EntityPopover
                summary={feeder.id}
                facts={{
                  links: [
                    {
                      label: "Explore input",
                      href: `/explorer?q=${encodeURIComponent(feeder.key)}`,
                    },
                  ],
                  format: feeder.source ? "source" : "model",
                  shape,
                  details: [
                    {
                      label: "Kind",
                      value: feeder.source ? "Source" : "Model",
                    },
                    { label: "Output columns", value: feeder.count },
                    {
                      label: "Last published",
                      value: published || "No source timestamp",
                    },
                  ],
                }}
                label={
                  <span class={`node ${feeder.source ? "source" : "model"}`}>
                    <span class="node-name">
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 16 16"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="1.2"
                        aria-hidden="true"
                      >
                        {feeder.source ? (
                          <>
                            <ellipse cx="8" cy="3" rx="5" ry="2" />
                            <path d="M3 3v9c0 1.1 2.2 2 5 2s5-.9 5-2V3M3 7.5c0 1.1 2.2 2 5 2s5-.9 5-2" />
                          </>
                        ) : (
                          <>
                            <rect x="2" y="2" width="9" height="8" rx="1.5" />
                            <path d="M5 12h7a2 2 0 0 0 2-2V5M5 5h3M5 7h3" />
                          </>
                        )}
                      </svg>
                      <span class="entity">
                        <strong class="entity-title">
                          {dot < 0 ? (
                            qualified
                          ) : (
                            <>
                              <span class="qual-prefix">
                                {qualified.slice(0, dot + 1)}
                              </span>
                              {qualified.slice(dot + 1)}
                            </>
                          )}
                        </strong>
                        <CopyChip value={feeder.id} label="copy" />
                      </span>
                      {matched.length > 0 && <FreshnessDot rows={matched} />}
                    </span>
                    <span class="caption">
                      {feeder.count ? `${feeder.count} cols` : "join only"}
                    </span>
                    {feeder.count > 0 && (
                      <i
                        class="column-share"
                        style={`width:${(feeder.count / maxCount) * 100}%`}
                        aria-hidden="true"
                      />
                    )}
                  </span>
                }
              />
            );
          })}
          {feeders.length > 8 && (
            <div
              class="node more caption"
              title={feeders
                .slice(8)
                .map((feeder) => feeder.id)
                .join("\n")}
            >
              +{feeders.length - 8} more
            </div>
          )}
        </div>
      ) : (
        <p class="caption lineage-empty">
          No declared upstream · defined in draft SQL
        </p>
      )}
      {!!explanation?.downstream.length && (
        <p class="caption lineage-downstream">
          feeds: {explanation.downstream.map(bareModel).join(" · ")}
        </p>
      )}
      <SchemaDisclosure columns={data.columns} />
    </figure>
  );
}

async function page(
  context: Context,
  fields: Record<string, string> = {},
  result?: unknown,
) {
  const client = createRouterClient(router, { context });
  const sessionId = fields.sessionId;
  if (!sessionId)
    return (
      <Layout title="Workbench" subtitle="">
        <style dangerouslySetInnerHTML={{ __html: workbenchCss }} />
        <section class="card wb-landing">
          <div>
            <h2>New session</h2>
            <p>Write a query or compare a model across cycles.</p>
            {fields.intent === "query" && fields.sql !== undefined && (
              <form method="post" action="/workbench">
                <input type="hidden" name="intent" value="query" />
                <label for="prefill-sql">Query</label>
                <textarea id="prefill-sql" name="sql" class="editor">
                  {fields.sql}
                </textarea>
                <button name="action" value="create">
                  Start session with this query
                </button>
              </form>
            )}
            <div class="wb-jumpoffs">
              {[
                {
                  intent: "charts",
                  label: "Explore chart data",
                  detail: "Start with account snapshots",
                },
                {
                  intent: "query",
                  label: "Write SQL",
                  detail: "Use a temporary schema",
                },
                {
                  intent: "models",
                  label: "Browse models",
                  detail: "Start from a checked-in model",
                },
              ].map((item) => (
                <form method="post" action="/workbench">
                  <input type="hidden" name="intent" value={item.intent} />
                  <button class="secondary" name="action" value="create">
                    <strong>
                      {item.label} <span aria-hidden="true">→</span>
                    </strong>
                    <span>{item.detail}</span>
                  </button>
                </form>
              ))}
            </div>
            <form method="post" action="/workbench">
              <input
                type="hidden"
                name="relation"
                value={fields.relation || ""}
              />
              <button name="action" value="create">
                Start session
              </button>
            </form>
          </div>
        </section>
      </Layout>
    );
  const [{ models, cycles, sources, cycleDetails }, draft, history] =
    await Promise.all([
      client.workbench.models({ sessionId }),
      client.workbench.draft({ sessionId }),
      client.workbench.history({ sessionId }),
    ]);
  if (!fields.cycleB) delete fields.cycleB;
  fields = {
    model: draft.model,
    sql: draft.sql,
    cycleA: cycles[1] || cycles[0] || "",
    cycleB: cycles[0] || "",
    keyColumns: "input_ref",
    ...fields,
  };
  const model = fields.model || draft.model;
  const pr = z
    .object({
      branch: z.string(),
      diff: z.string(),
      pr_opened: z.boolean(),
      url: z.string().nullable(),
      message: z.string(),
      reviewToken: z.string().optional(),
    })
    .safeParse(result);
  if (pr.success && !fields.runId)
    fields.runId =
      history.runs.find(
        (run) =>
          run.status === "succeeded" &&
          run.input.model === model &&
          run.input.sql === fields.sql,
      )?.id || "";
  if (pr.success && fields.runId)
    result = await client.workbench.result({ runId: fields.runId });
  const preview = PreviewResult.safeParse(result),
    backtest = BacktestResult.safeParse(result),
    explain = ExplainResult.safeParse(result),
    status = AsyncStatus.safeParse(result);
  const data = preview.success
    ? preview.data
    : backtest.success
      ? backtest.data.buildB
      : null;
  const running =
    status.success && ["queued", "running"].includes(status.data.status);
  const failed = status.success && status.data.status === "failed";
  const cancelled = status.success && status.data.status === "cancelled";
  const operation = fields.operation || "preview";
  const explanation =
    data && operation !== "query"
      ? await client.workbench.explain({ sessionId, model, sql: fields.sql })
      : explain.success
        ? explain.data
        : null;
  const artifact = data
    ? await client.workbench.artifact({ artifactRef: data.artifactRef })
    : null;
  const error = z
    .object({ message: z.string(), error_class: z.string().optional() })
    .safeParse(result);

  const ide = process.env.DBT_CLOUD_IDE_URL;
  const cycleLabel = (id: string | undefined) => {
    id = id || "";
    return cycleLabelValue(id);
  };
  const cycleLabelValue = (id: string) => {
    const item = cycleDetails.find((c) => c.id === id);
    return item
      ? `${item.cadence} · ${new Date(item.opened_at).toISOString().slice(0, 16).replace("T", " ")} UTC · ${id.slice(0, 8)}`
      : id.slice(0, 8);
  };
  const returnRunId =
    fields.runId || history.runs.find((run) => run.status === "succeeded")?.id;
  const state = { ...fields, sessionId };
  const missingRefs = [
    ...fields.sql!.matchAll(/ref\(\s*['"]([^'"]+)['"]\s*\)/g),
  ]
    .map((m) => m[1]!)
    .filter((name) => !models.includes(name));
  const permissionDenied =
    (failed &&
      status.data.error?.error_class === "workbench_permission_denied") ||
    (error.success && error.data.error_class === "workbench_permission_denied");
  const recovery = permissionDenied
    ? safeCopy(fields.sql || "", await readableInputs(context.identity.admin))
    : null;
  const deniedHint = errorHint("workbench_permission_denied");
  const failureMessage =
    recovery?.message ||
    (permissionDenied
      ? `${deniedHint.summary} ${deniedHint.next_step}`
      : null) ||
    (failed && missingRefs.length
      ? `Could not build this SQL: ref('${missingRefs[0]}') does not name a registered model.`
      : status.success
        ? status.data.error?.message
        : "");
  const showResultHints = !history.runs.some(
    (run) => run.status === "succeeded" && run.id !== fields.runId,
  );
  const numericColumns = new Set(
    data?.columns
      .filter((col) => isNumericType(col.type))
      .map((col) => col.name),
  );
  const elapsed = Math.max(
    0,
    Math.floor(
      (status.success
        ? (status.data.durationMs ?? Number(fields.durationMs || 0))
        : Number(fields.durationMs) || data?.timingMs || 0) / 1000,
    ),
  );
  const outcome = pr.success
    ? pr.data.pr_opened
      ? pr.data.url
        ? `PR #${pr.data.url.split("/").at(-1)} opened`
        : "Pull request opened"
      : pr.data.reviewToken
        ? "Draft ready for review"
        : "Patch ready"
    : cancelled
      ? "Cancelled"
      : failed
        ? "Build failed"
        : error.success
          ? "Action failed"
          : explain.success
            ? "SQL explained"
            : data
              ? `${operation === "query" ? "Read" : "Built"} in ${elapsed}s`
              : "";
  return (
    <Layout title="Workbench" subtitle="">
      <style dangerouslySetInnerHTML={{ __html: css + workbenchCss }} />
      <script dangerouslySetInnerHTML={{ __html: workbenchScript }} />
      <div class="wb">
        {!context.identity.admin && (
          <p class="caption">
            Preview and Backtest use current global inputs for both cycle
            contexts. To rebuild past inputs, ask an operator;{" "}
            <a href="/runbooks/forbidden">open the access runbook</a>.
          </p>
        )}
        <div
          id="wb-running-strip"
          class="running-strip"
          role="status"
          hidden={!running && !outcome}
          data-terminal={running ? undefined : "true"}
        >
          {running ? (
            <>
              <span>
                <span class="heartbeat" />
                {operation === "query" ? "Running" : "Building"}{" "}
                <strong>{bareModel(model)}</strong> ·{" "}
                <span data-elapsed data-started={fields.started} />
              </span>
              <form method="post" action="/workbench#wb-ticket">
                {hidden({
                  ...state,
                  runId: status.success ? status.data.runId : "",
                })}
                <button class="secondary" name="action" value="cancel">
                  Cancel
                </button>
              </form>
            </>
          ) : (
            <span>
              {outcome} · {bareModel(model)}
            </span>
          )}
        </div>
        <div class="facts">
          <span>Session {copy(sessionId.slice(0, 8))}</span>
          <span>
            Scratch schema <code>wb_{sessionId.replaceAll("-", "")}</code>
          </span>
          <span>Expires after one day</span>
        </div>
        <div class="workspace">
          <section class="card">
            <div class="row">
              <h2>Editor</h2>
              <span class="badge">Draft · auto-saved</span>
            </div>
            <form
              method="post"
              action="/workbench#wb-ticket"
              class="stack"
              id="builder"
            >
              <input type="hidden" name="sessionId" value={sessionId} />
              {fields.runId && (
                <input type="hidden" name="runId" value={fields.runId} />
              )}
              <div class="controls">
                <label>
                  Model name
                  <input
                    name="model"
                    value={model}
                    required
                    pattern="[a-z][a-z0-9_]*"
                  />
                </label>
                <label>
                  Starting model
                  <select id="wb-models" name="startingModel">
                    <option value="">Choose a checked-in model</option>
                    {models.map((m) => (
                      <option value={m}>{m}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div class="tools">
                <button
                  class="secondary"
                  name="action"
                  value="load"
                  disabled={running}
                >
                  Load model
                </button>
                <span class="caption">New name = new mart</span>
              </div>
              <div class="controls">
                <label>
                  Insert source
                  <select data-insert="source">
                    <option value="">Insert source…</option>
                    {sources.map((source) => (
                      <option value={source}>{source}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Insert model reference
                  <select data-insert="ref">
                    <option value="">Insert ref…</option>
                    {models.map((m) => (
                      <option value={m}>{m}</option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                SQL editor
                <textarea
                  id="wb-sql"
                  class="editor"
                  name="sql"
                  rows={9}
                  spellcheck={false}
                >
                  {fields.sql}
                </textarea>
              </label>
              <div class="controls">
                <label>
                  Cycle A · baseline
                  <select name="cycleA">
                    {cycles.map((id) => (
                      <option value={id} selected={id === fields.cycleA}>
                        {cycleLabel(id)}
                        {id === cycles[0] ? " · latest" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Cycle B · comparison
                  <select name="cycleB">
                    {fields.cycleB && !cycles.includes(fields.cycleB) && (
                      <option value={fields.cycleB} selected>
                        {fields.cycleB.slice(0, 8)} · cycle no longer available
                      </option>
                    )}
                    {cycles.map((id) => (
                      <option value={id} selected={id === fields.cycleB}>
                        {cycleLabel(id)}
                        {id === cycles[0] ? " · latest" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                Business key columns
                <input name="keyColumns" value={fields.keyColumns} />
              </label>
              <div class="tools">
                <button
                  data-build-action
                  name="action"
                  value="query"
                  disabled={running}
                >
                  Run <kbd>Cmd/Ctrl Enter</kbd>
                </button>
                <button
                  data-build-action
                  class="secondary"
                  name="action"
                  value="preview"
                  disabled={running}
                >
                  Preview <kbd>Cmd/Ctrl Shift Enter</kbd>
                </button>
                <button
                  data-build-action
                  class="secondary"
                  name="action"
                  value="backtest"
                  disabled={running}
                >
                  Backtest <kbd>Cmd/Ctrl Alt Enter</kbd>
                </button>
                <button
                  data-build-action
                  class="secondary"
                  name="action"
                  value="explain"
                  disabled={running}
                >
                  Explain
                </button>
              </div>
            </form>
            <div class="tools compact">
              {ide ? (
                <a
                  class="button secondary"
                  href={ide}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open in dbt Cloud IDE ↗
                </a>
              ) : (
                <>
                  <button class="secondary" disabled>
                    Open in dbt Cloud IDE
                  </button>
                  <a
                    class="caption"
                    href="https://docs.getdbt.com/docs/cloud/dbt-cloud-ide/develop-in-the-cloud"
                  >
                    Configure the dbt Cloud IDE →
                  </a>
                </>
              )}
            </div>
          </section>
          <aside class="card history">
            <h2>History</h2>
            {history.runs.length ? (
              history.runs.map((run, i) => (
                <a
                  class="history-entry"
                  aria-current={run.id === fields.runId ? "page" : undefined}
                  href={`/workbench?runId=${run.id}#wb-ticket`}
                  title={text(run.input.model)}
                >
                  <span class="history-name">
                    <span class={`status-dot ${run.status}`} />
                    {bareModel(text(run.input.model))}
                  </span>
                  <small>
                    {text(run.input.operation || run.kind)} · {run.status} ·
                    cycle{" "}
                    {text(
                      run.input.cycleB || run.input.cycleId || run.input.cycleA,
                    ).slice(0, 8)}{" "}
                    · {new Date(run.created_at).toISOString().slice(11, 19)} UTC
                    {history.runs
                      .slice(i + 1)
                      .find(
                        (other) => other.input.model === run.input.model,
                      ) && (
                      <>
                        {" "}
                        ·{" "}
                        {history.runs
                          .slice(i + 1)
                          .find(
                            (other) => other.input.model === run.input.model,
                          )?.input.sql === run.input.sql
                          ? "same SQL, new cycle"
                          : "SQL changed"}
                      </>
                    )}
                  </small>
                </a>
              ))
            ) : (
              <div class="empty">
                <EmptyState
                  message="No runs yet."
                  nextStep={{ label: "Write a query", href: "#wb-sql" }}
                />
              </div>
            )}
            <p class="caption">Up to 100 rows</p>
            <a class="history-footer" href="/ops">
              Open operations →
            </a>
          </aside>
        </div>
        <div
          id="wb-ticket"
          data-land={result !== undefined ? "true" : undefined}
        >
          {fields.operation === "backtest" &&
            fields.runId &&
            fields.cycleB &&
            fields.cycleB !== cycles[0] && (
              <p class="cycle-notice">
                Newer cycle available. <a href="#builder">Choose a cycle →</a>
              </p>
            )}
          {status.success && (
            <section
              id="build"
              class={`card ${running ? "building-card" : "terminal-card"} ${failed ? "error" : cancelled ? "" : "result"}`}
              role="status"
            >
              <div
                class="ticket"
                data-live-run={running ? "true" : undefined}
                data-started={fields.started || Date.now()}
              >
                <h2>
                  {running
                    ? "Building"
                    : cancelled
                      ? "Build cancelled"
                      : failed
                        ? "Build failed"
                        : "Build complete"}
                </h2>
                <span class={`badge ${failed ? "" : status.data.status}`}>
                  {running && <span class="heartbeat" />}
                  {status.data.status}
                </span>
                {copy(status.data.runId)}
                <span
                  class="caption"
                  data-elapsed={running ? true : undefined}
                  data-started={fields.started}
                >
                  {running ? null : `${elapsed}s elapsed`}
                </span>
              </div>
              <p class="caption">
                {model} · {operation} · A{" "}
                {copy(fields.cycleA || "", cycleLabel(fields.cycleA))}
                {operation === "backtest" && (
                  <>
                    {" "}
                    → B {copy(fields.cycleB || "", cycleLabel(fields.cycleB))}
                  </>
                )}
              </p>
              {status.data.error && !cancelled && <p>{failureMessage}</p>}
              {recovery && (
                <form method="post" action="/workbench#wb-ticket">
                  {hidden({ ...state, sql: recovery.sql })}
                  <button
                    name="action"
                    value={
                      operation === "query"
                        ? "query"
                        : operation === "backtest"
                          ? "backtest"
                          : "preview"
                    }
                  >
                    Use safe copy and rerun
                  </button>
                </form>
              )}
              {(failed || cancelled) && (
                <div class="tools">
                  <button type="button" class="secondary" data-edit-sql>
                    Edit SQL and rerun
                  </button>
                  {failed &&
                    copy(failureMessage || "Build failed", "Copy error")}
                </div>
              )}
              {running && (
                <>
                  <p class="caption">Refreshing every 3s</p>
                  <form method="post" action="/workbench#wb-ticket" data-poll>
                    {hidden({ ...state, runId: status.data.runId })}
                    <button class="secondary" name="action" value="status">
                      Refresh build
                    </button>
                  </form>
                </>
              )}
            </section>
          )}
          {error.success && !pr.success && !status.success && (
            <section class="card error terminal-card" role="alert">
              <h2>Action failed</h2>
              <p>{permissionDenied ? failureMessage : error.data.message}</p>
              {recovery && (
                <form method="post" action="/workbench#wb-ticket">
                  {hidden({ ...state, sql: recovery.sql })}
                  <button
                    name="action"
                    value={
                      operation === "query"
                        ? "query"
                        : operation === "backtest"
                          ? "backtest"
                          : "preview"
                    }
                  >
                    Use safe copy and rerun
                  </button>
                </form>
              )}
              <div class="tools">
                <button type="button" class="secondary" data-edit-sql>
                  Edit SQL and rerun
                </button>
                {copy(error.data.message, "Copy error")}
              </div>
            </section>
          )}
          {data && (
            <section class="card result result-card">
              <div class="result-top">
                <div>
                  <h2>
                    {operation === "query"
                      ? "Query results"
                      : backtest.success
                        ? "Backtest results"
                        : "Preview results"}
                  </h2>
                </div>
                <form method="post" action="/workbench#wb-ticket">
                  {hidden(state)}
                  <button
                    class="secondary"
                    name="action"
                    value={
                      operation === "backtest"
                        ? "backtest"
                        : operation === "query"
                          ? "query"
                          : "preview"
                    }
                    data-build-action
                    disabled={running}
                  >
                    Rerun
                  </button>
                  <button
                    class="secondary"
                    name="action"
                    value="save"
                    data-build-action
                    disabled={running}
                  >
                    Save as PR
                  </button>
                </form>
              </div>
              <CompositionFrame
                data={data}
                model={model}
                cycle={fields.cycleA || ""}
                cycleOpenedAt={
                  cycleDetails.find((item) => item.id === fields.cycleA)
                    ?.opened_at
                }
                comparisonCycle={backtest.success ? fields.cycleB : undefined}
                explanation={explanation}
                artifact={artifact}
              />
              {showResultHints && data.invokeSkipped && (
                <p class="caption result-hint">
                  Preview · functions skipped (fixtures)
                </p>
              )}
              {(data.truncated ||
                (data.totalRows ?? data.rows.length) > 100) && (
                <p class="caption result-hint">First 100 rows</p>
              )}
              <div class="result-grid">
                <div class="result-data">
                  {data.rows.length ? (
                    <>
                      <div
                        class="table-wrap expanded-table"
                        data-overflow-hint={showResultHints ? undefined : "off"}
                      >
                        <table>
                          <thead>
                            <tr>
                              {data.columns.map((col, i) => (
                                <th
                                  class={
                                    numericColumns.has(col.name)
                                      ? "numeric"
                                      : undefined
                                  }
                                >
                                  <button
                                    type="button"
                                    data-inspect={`inspector-${i}`}
                                  >
                                    {col.name} ↗
                                  </button>
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {data.rows.slice(0, 100).map((row) => {
                              const change = backtest.success
                                ? backtest.data.changed.find((change) =>
                                    backtest.data.keyColumns.every(
                                      (key) =>
                                        text(change.after[key]) ===
                                        text(row[key]),
                                    ),
                                  )
                                : undefined;
                              const added =
                                backtest.success &&
                                backtest.data.added.some((added) =>
                                  backtest.data.keyColumns.every(
                                    (key) =>
                                      text(added[key]) === text(row[key]),
                                  ),
                                );
                              return (
                                <tr
                                  class={
                                    added
                                      ? "added-row"
                                      : change
                                        ? "changed-row"
                                        : ""
                                  }
                                >
                                  {data.columns.map((col) => (
                                    <td
                                      class={`${numericColumns.has(col.name) ? "numeric" : ""} ${row[col.name] == null ? "muted" : ""} ${change && backtest.success && backtest.data.comparedColumns.includes(col.name) && text(change.before[col.name]) !== text(change.after[col.name]) ? "changed-cell" : ""}`}
                                    >
                                      {text(row[col.name]).length > 65 ? (
                                        <details class="cell-value">
                                          <summary title={text(row[col.name])}>
                                            {text(row[col.name])}
                                          </summary>
                                          <p>{text(row[col.name])}</p>
                                        </details>
                                      ) : (
                                        text(row[col.name])
                                      )}
                                    </td>
                                  ))}
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  ) : (
                    <div class="empty">
                      <EmptyState
                        message="No rows."
                        nextStep={{ label: "Edit the query", href: "#wb-sql" }}
                      />
                    </div>
                  )}
                </div>
                {data.columns.map((col, i) => {
                  const values = data.rows
                      .map((row) => row[col.name])
                      .filter((v) => v != null),
                    ordered = [...values].sort((a, b) =>
                      typeof a === "number" && typeof b === "number"
                        ? a - b
                        : text(a).localeCompare(text(b)),
                    );
                  return (
                    <aside
                      id={`inspector-${i}`}
                      class="inspector"
                      hidden
                      aria-label={`Inspect ${col.name}`}
                    >
                      <div class="row">
                        <h2>{col.name}</h2>
                        <button
                          class="secondary"
                          type="button"
                          data-close-inspector
                        >
                          Close
                        </button>
                      </div>
                      <dl>
                        <dt>Source</dt>
                        <dd>
                          {col.source
                            ? bareModel(col.source)
                            : `Defined in draft SQL · ${model}`}
                        </dd>
                        <dt>Type</dt>
                        <dd>{col.type}</dd>
                        <dt>Nullable</dt>
                        <dd>{col.nullable ? "Yes" : "No"}</dd>
                        <dt>Samples</dt>
                        <dd>
                          {values.slice(0, 3).map((v) => (
                            <p>{text(v)}</p>
                          ))}
                        </dd>
                        <dt>Distinct in returned rows</dt>
                        <dd>{new Set(values.map(text)).size}</dd>
                        <dt>Minimum / maximum in returned rows</dt>
                        <dd>
                          <p class="inspector-extreme">{text(ordered[0])}</p>
                          <p class="inspector-extreme">
                            {text(ordered.at(-1))}
                          </p>
                        </dd>
                      </dl>
                    </aside>
                  );
                })}
              </div>
              <details>
                <summary>Compiled SQL</summary>
                <pre>{data.compiledSql.trimStart()}</pre>
              </details>
            </section>
          )}
          {backtest.success && (
            <section class="card">
              <h2>Changes between cycles</h2>
              <p class="summary">
                {backtest.data.added.length} added ·{" "}
                {backtest.data.removed.length} removed ·{" "}
                {backtest.data.changed.length} changed
              </p>
              <p class="caption">
                Compared:{" "}
                {backtest.data.comparedColumns
                  .filter((col) => !backtest.data.keyColumns.includes(col))
                  .join(", ")}
              </p>
              {backtest.data.added.length +
                backtest.data.removed.length +
                backtest.data.changed.length >
              0 ? (
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Kind</th>
                        <th>Business key</th>
                        <th>Column</th>
                        <th class="numeric">Before</th>
                        <th class="numeric">After</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        {
                          kind: "added",
                          label: "✓ Added",
                          rows: backtest.data.added,
                        },
                        {
                          kind: "removed",
                          label: "− Removed",
                          rows: backtest.data.removed,
                        },
                      ].flatMap((group) =>
                        group.rows.flatMap((row) =>
                          backtest.data.comparedColumns.map((col, i) => (
                            <tr class={`${group.kind}-row`}>
                              <td
                                class={
                                  group.kind === "added"
                                    ? "added-kind"
                                    : undefined
                                }
                              >
                                {i === 0 ? group.label : ""}
                              </td>
                              <td>
                                {i === 0
                                  ? backtest.data.keyColumns
                                      .map((key) => text(row[key]))
                                      .join(" · ")
                                  : ""}
                              </td>
                              <td>{col}</td>
                              <td
                                class={
                                  numericColumns.has(col)
                                    ? "numeric"
                                    : undefined
                                }
                              >
                                {group.kind === "removed"
                                  ? cell(row[col])
                                  : "Not recorded"}
                              </td>
                              <td
                                class={`${numericColumns.has(col) ? "numeric" : ""} ${group.kind === "added" ? "plus" : ""}`}
                              >
                                {group.kind === "added"
                                  ? cell(row[col])
                                  : "Not recorded"}
                              </td>
                            </tr>
                          )),
                        ),
                      )}
                      {[...backtest.data.changed]
                        .sort((a, b) =>
                          backtest.data.keyColumns
                            .map((key) => text(a.after[key]))
                            .join(" · ")
                            .localeCompare(
                              backtest.data.keyColumns
                                .map((key) => text(b.after[key]))
                                .join(" · "),
                              undefined,
                              { numeric: true },
                            ),
                        )
                        .flatMap((change) =>
                          backtest.data.comparedColumns
                            .filter(
                              (col) =>
                                !backtest.data.keyColumns.includes(col) &&
                                text(change.before[col]) !==
                                  text(change.after[col]),
                            )
                            .map((col) => (
                              <tr>
                                <td>Changed</td>
                                <td>
                                  {backtest.data.keyColumns
                                    .map((key) => text(change.after[key]))
                                    .join(" · ")}
                                </td>
                                <td>{col}</td>
                                <td
                                  class={`minus ${numericColumns.has(col) ? "numeric" : ""}`}
                                >
                                  − {text(change.before[col])}
                                </td>
                                <td
                                  class={`plus ${numericColumns.has(col) ? "numeric" : ""}`}
                                >
                                  + {text(change.after[col])}
                                </td>
                              </tr>
                            )),
                        )}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  message="No changes."
                  nextStep={{
                    label: "Compare another cycle",
                    href: "#builder",
                  }}
                />
              )}
            </section>
          )}
          {explain.success && explanation && (
            <section class="card terminal-card">
              <div class="row">
                <h2>Explain</h2>
              </div>
              <div class="controls">
                <div>
                  <h3>Upstream</h3>
                  <p class="caption">
                    {explanation.upstream.length
                      ? explanation.upstream.map((id) => (
                          <span class="model-label" title={id}>
                            {bareModel(id)}
                          </span>
                        ))
                      : "No upstream models"}
                  </p>
                </div>
                <div>
                  <h3>Downstream</h3>
                  <p class="caption">
                    {explanation.downstream.length
                      ? explanation.downstream.map((id) => (
                          <span class="model-label" title={id}>
                            {bareModel(id)}
                          </span>
                        ))
                      : "No downstream models"}
                  </p>
                </div>
              </div>
              <details>
                <summary>Compiled SQL</summary>
                <pre>{explanation.compiledSql.trimStart()}</pre>
              </details>
              {returnRunId && (
                <a
                  class="explain-return"
                  href={`/workbench?runId=${returnRunId}#wb-ticket`}
                >
                  ← Return to results
                </a>
              )}
            </section>
          )}
          {pr.success && (
            <section id="pr-review" class="card result pr-review">
              <h2>
                {pr.data.pr_opened
                  ? "Pull request opened"
                  : pr.data.reviewToken
                    ? "Draft ready"
                    : "Patch ready"}
              </h2>
              <p>
                {pr.data.pr_opened ? (
                  pr.data.url ? (
                    <a href={pr.data.url}>Open the pull request ↗</a>
                  ) : (
                    `Pull request opened from branch ${pr.data.branch}. Find it on GitHub by that branch.`
                  )
                ) : pr.data.reviewToken ? (
                  `No PR was opened. ${pr.data.message}`
                ) : (
                  pr.data.message
                )}
              </p>
              {!pr.data.pr_opened && !pr.data.reviewToken && (
                <>
                  <div class="tools">
                    <a
                      download={`${fields.model}.patch`}
                      href={`data:text/x-diff;charset=utf-8,${encodeURIComponent(pr.data.diff)}`}
                    >
                      Download patch
                    </a>
                    {copy(pr.data.diff, "Copy patch")}
                  </div>
                  <ol class="patch-commands">
                    {[
                      `git fetch origin && git switch -c ${pr.data.branch} origin/main`,
                      `git apply ${fields.model}.patch`,
                      "bash ops/ready.sh",
                    ].map((command) => (
                      <li>
                        <code>{command}</code> {copy(command, "Copy command")}
                      </li>
                    ))}
                  </ol>
                  <p>
                    Then push the branch and open a pull request with the
                    workbench label. No push access? Send the patch file to the
                    data team.
                  </p>
                </>
              )}
              <p>
                {fields.runId && (
                  <a href={`/workbench?runId=${fields.runId}`}>
                    Reopen successful run →
                  </a>
                )}
              </p>
              {(pr.data.reviewToken || pr.data.pr_opened) && (
                <p class="caption">
                  {pr.data.reviewToken
                    ? "Diff expires in 15 min"
                    : "Check CI and use the workbench label for review."}
                </p>
              )}
              <p>Branch {copy(pr.data.branch)}</p>
              <pre>
                {pr.data.diff.split("\n").map((line) => (
                  <span
                    class={`diff-line ${line.startsWith("+") ? "plus" : line.startsWith("-") ? "minus" : ""}`}
                  >
                    {line || " "}
                  </span>
                ))}
              </pre>
              {pr.data.reviewToken && (
                <form method="post" action="/workbench#pr-review">
                  {hidden({ ...state, reviewToken: pr.data.reviewToken })}
                  <button name="action" value="confirm-save">
                    Open pull request
                  </button>
                  <button type="button" class="secondary" data-edit-sql>
                    Edit SQL
                  </button>
                </form>
              )}
            </section>
          )}
        </div>
      </div>
    </Layout>
  );
}
workbenchPages.get("/artifact", async (c) => {
  const client = createRouterClient(router, { context: c.get("context") });
  const content = await client.workbench.artifactContent({
    artifactRef: c.req.query("ref") || "",
  });
  return c.body(JSON.stringify(content, null, 2), 200, {
    "content-type": "application/json",
    "content-disposition": "attachment; filename=workbench-result.json",
  });
});
workbenchPages.get("/", async (c) => {
  const sessionId = getCookie(c, "mdp_workbench_session");
  const intent = c.req.query("intent");
  const sql = queryPrefill(c.req.query("sql") ?? "");
  if (sql === null)
    return c.text(
      "The query is too long. Use at most 4 KB and reopen Workbench.",
      400,
    );
  if (!sessionId)
    return c.html(
      await page(c.get("context"), {
        relation: c.req.query("relation") || "",
        ...(intent === "query" ? { intent, sql } : {}),
      }),
    );
  const client = createRouterClient(router, { context: c.get("context") });
  let fields: Record<string, string> = { sessionId };
  let result: unknown;
  if (intent === "query")
    fields = { ...fields, model: "mart_new_query", sql, operation: "query" };
  const requested = relationFromName(c.req.query("relation") || "");
  if (requested) {
    const readable = permittedInput(
      requested,
      await readableInputs(c.get("context").identity.admin),
    );
    if (readable)
      fields = {
        ...fields,
        model: "mart_new_query",
        sql: inputQuery(readable),
        operation: "query",
      };
    else
      result = {
        message:
          "This input is unavailable. Open Explorer and choose a readable table.",
      };
  }
  const runId = c.req.query("runId");
  if (runId) {
    const history = await client.workbench.history({ sessionId });
    const run = history.runs.find((r) => r.id === runId);
    if (run) {
      fields = {
        ...fields,
        ...Object.fromEntries(
          Object.entries(run.input).map(([k, v]) => [
            k,
            Array.isArray(v) ? v.join(",") : String(v),
          ]),
        ),
        operation: String(run.input.operation || run.kind),
        cycleA: String(run.input.cycleA || run.input.cycleId || ""),
        cycleB: String(run.input.cycleB || ""),
        runId,
        durationMs: String(run.duration_ms || 0),
        started: String(new Date(run.created_at).getTime()),
      };
      const status = await client.workbench.status({ runId });
      result =
        status.status === "succeeded"
          ? await client.workbench.result({ runId })
          : status;
    }
  }
  try {
    return c.html(await page(c.get("context"), fields, result));
  } catch {
    return c.html(await page(c.get("context"), { intent: "query", sql }));
  }
});
workbenchPages.post("/", async (c) => {
  const fields = z
    .record(z.string(), z.string())
    .parse(await c.req.parseBody());
  const client = createRouterClient(router, { context: c.get("context") });
  if (fields.action === "create") {
    const prefill = queryPrefill(fields.sql ?? "");
    if (prefill === null)
      return c.text(
        "The query is too long. Use at most 4 KB and reopen Workbench.",
        400,
      );
    const session = await client.workbench.createSession({});
    setCookie(c, "mdp_workbench_session", session.sessionId, {
      httpOnly: true,
      secure: true,
      sameSite: "Lax",
      path: "/workbench",
      maxAge: 86400,
    });
    if (fields.intent === "charts") {
      const sql = chartExample;
      try {
        const result = await client.workbench.query({
          sessionId: session.sessionId,
          model: "mart_chart_explore",
          sql,
        });
        return c.redirect(
          `/workbench?runId=${encodeURIComponent(result.runId)}#wb-ticket`,
          303,
        );
      } catch (error) {
        return c.html(
          await page(
            c.get("context"),
            { sessionId: session.sessionId, model: "mart_chart_explore", sql },
            actionFailure(error),
          ),
        );
      }
    }
    if (fields.relation)
      return c.redirect(
        `/workbench?relation=${encodeURIComponent(fields.relation)}#wb-sql`,
        303,
      );
    if (fields.intent === "query")
      return c.redirect(
        `/workbench?${new URLSearchParams({ intent: "query", ...(prefill ? { sql: prefill } : {}) })}#wb-sql`,
        303,
      );
    if (fields.intent === "models")
      return c.redirect("/workbench#wb-models", 303);
    return c.redirect("/workbench", 303);
  }
  const sessionId = fields.sessionId || "",
    model = fields.model || "mart_chart_history";
  let result: unknown;
  try {
    if (
      [
        "query",
        "preview",
        "backtest",
        "explain",
        "save",
        "confirm-save",
      ].includes(fields.action || "") &&
      !fields.sql?.trim()
    )
      throw new Error(
        "SQL is empty. Select a checked-in model or write a query.",
      );
    const draft = { sessionId, model, sql: fields.sql };
    switch (fields.action) {
      case "load": {
        const selected = await client.workbench.draft({
          sessionId,
          model: fields.startingModel,
        });
        fields.model = selected.model;
        fields.sql = selected.sql;
        break;
      }
      case "query":
        fields.operation = "query";
        fields.started = String(Date.now());
        result = await client.workbench.query(draft);
        break;
      case "preview":
        fields.operation = "preview";
        fields.started = String(Date.now());
        result = await client.workbench.previewModel({
          ...draft,
          cycleId: fields.cycleA || undefined,
        });
        break;
      case "backtest":
        fields.operation = "backtest";
        fields.started = String(Date.now());
        result = await client.workbench.backtest({
          ...draft,
          cycleA: fields.cycleA || "",
          cycleB: fields.cycleB || "",
          keyColumns: (fields.keyColumns || "").split(",").map((k) => k.trim()),
        });
        break;
      case "explain":
        await client.workbench.draft(draft);
        result = await client.workbench.explain(draft);
        break;
      case "save":
        delete fields.reviewToken;
        result = await client.workbench.saveAsPr({
          ...draft,
          sql: fields.sql || "",
        });
        break;
      case "confirm-save":
        if (!fields.reviewToken)
          throw new Error(
            "The diff needs review. Preview it before opening a pull request.",
          );
        result = await client.workbench.saveAsPr({
          ...draft,
          sql: fields.sql || "",
          reviewToken: fields.reviewToken,
        });
        delete fields.reviewToken;
        break;
      case "cancel":
        result = await client.workbench.cancel({ runId: fields.runId || "" });
        fields.durationMs = String(
          Date.now() - Number(fields.started || Date.now()),
        );
        break;
      case "status": {
        const status = await client.workbench.status({
          runId: fields.runId || "",
        });
        result =
          status.status === "succeeded"
            ? await client.workbench.result({ runId: status.runId })
            : status;
        break;
      }
    }
  } catch (error) {
    result = actionFailure(error);
  }
  const asyncResult = AsyncStatus.safeParse(result);
  if (asyncResult.success) fields.runId = asyncResult.data.runId;
  return c.html(await page(c.get("context"), fields, result));
});
