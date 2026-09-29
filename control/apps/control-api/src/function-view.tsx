import { randomUUID } from "node:crypto";
import { createRouterClient } from "@orpc/server";
import { z } from "zod";
import { jsonRow, runDto, sourceWording } from "@mdp/contracts";
import { router, type Context } from "./router.js";
import { rows } from "./db.js";
import { service } from "./service.js";
import {
  functionTargetHealth,
  rejectedByReason,
  runCoverage,
  sourceLabels,
} from "./console-data.js";
import {
  driftHistory,
  functionVerdict,
  rejectLabel,
} from "./console-semantics.js";
import { pausedSince } from "./page-data.js";
import { readSources } from "./platform.js";
import {
  CopyChip,
  DriftPopover,
  SchemaDisclosure,
  RelativeTime,
} from "./primitives.js";
import { Layout, Result } from "./pages.js";
import { RunReceipts } from "./run-components.js";
import {
  FlowLine,
  FunctionVerdict,
  LatestRows,
  LazyDisclosure,
  ProblemsCard,
  RightsChip,
  RunStrip,
  TargetStrip,
  targetWords,
} from "./function-components.js";

export const functionParts = z.enum([
  "live",
  "rows",
  "targets",
  "receipts",
  "schema",
  "settings",
  "cursors",
]);
export type FunctionPart = z.infer<typeof functionParts>;
function Action({
  source,
  action,
  label,
  scope,
  enabled,
  disabled = false,
  hover,
}: {
  source: string;
  action: string;
  label: string;
  scope: string;
  enabled?: boolean;
  disabled?: boolean;
  hover?: string | undefined;
}) {
  return (
    <form
      method="post"
      action={`/actions/${action}`}
      data-confirm={
        action === "pause" && enabled === false
          ? "Pause this function? Scheduled runs stop until you resume it."
          : undefined
      }
    >
      <input type="hidden" name="source_key" value={source} />
      <input type="hidden" name="back" value={`/functions/${source}`} />
      <input type="hidden" name="scope" value={scope} />
      <input type="hidden" name="key" value={randomUUID()} />
      {enabled !== undefined && (
        <input type="hidden" name="enabled" value={String(enabled)} />
      )}
      <button
        disabled={disabled}
        data-pending={
          action === "probe"
            ? "Probing…"
            : action === "pause" && !enabled
              ? "Pausing…"
              : "Running…"
        }
        data-hover={hover}
        data-next-href={`/functions/${source}/logs`}
        data-next-label="Open logs →"
      >
        {label}
        {action === "run" ? " ↵" : ""}
      </button>
    </form>
  );
}
async function metadata(context: Context, key: string) {
  const c = createRouterClient(router, { context });
  const [page, s] = await Promise.all([
    c.functions.page({ source_key: key, metadata_only: true }),
    c.streamlines.get({ source_key: key }),
  ]);
  const last = page.last_runs.find((r) => r.status !== "superseded");
  return { c, page, s, last };
}
export async function functionView(
  context: Context,
  key: string,
  result?: unknown,
  query: {
    preview_table?: string;
    preview_cursor?: string;
    open?: string;
    state?: string;
    run?: string;
  } = {},
) {
  const { page, s, last } = await metadata(context, key);
  const [health, sourceData, coverages, pauses, labels, alerts, week] =
    await Promise.all([
      functionTargetHealth(context.db, s.id, last?.id),
      readSources(context.db, { source_key: key }),
      runCoverage(context.db, last ? [last.id] : []),
      pausedSince(context.db),
      sourceLabels(context.db, key),
      rows(
        context.db,
        jsonRow,
        "SELECT a.class FROM control.alert a LEFT JOIN control.run r ON r.id=a.run_id WHERE a.resolved_at IS NULL AND a.acknowledged_by IS NULL AND (r.streamline_id=$1 OR (a.subject_type='streamline' AND a.subject_id=$1::text))",
        [s.id],
      ),
      rows(
        context.db,
        runDto,
        "SELECT * FROM control.run WHERE streamline_id=$1 AND created_at>now()-interval '7 days' ORDER BY created_at DESC LIMIT 40",
        [s.id],
      ),
    ]);
  const source = sourceData.sources[0];
  const drift = driftHistory(page.fingerprint_history).filter(
    (d) =>
      d.changes.length &&
      !s.acknowledged_fingerprints.some(
        (f) => f === `${key}:${d.table}:${d.fingerprint}`,
      ),
  );
  const verdict = functionVerdict(
    s,
    last,
    last ? coverages[last.id] : undefined,
    s.parked_inputs,
    drift.length,
    alerts.map((a) => ({ class: String(a.class) })),
  );
  const scope = s.tenant_bound
    ? `tenant:${context.identity.tenant_id ?? ""}`
    : "global";
  const disabled = s.tenant_bound && !context.identity.tenant_id;
  const sourceDetail = [
    source?.description,
    source?.tracked
      ? `${source.tracked.count} ${source.tracked.unit} tracked.`
      : "Membership not measured yet.",
    source
      ? `${source.days.reduce((n, day) => n + BigInt(day.entries), 0n).toLocaleString("en")} rows in 14 days.`
      : "Rows not measured yet.",
  ]
    .filter(Boolean)
    .join(" ");
  const title = source?.display_name ?? sourceWording[key]?.name ?? key;
  const open = functionParts.safeParse(query.open);
  const inline = open.success
    ? await functionPart(context, key, open.data, query)
    : undefined;
  const previewTable = query.preview_table ?? s.writes[0];
  const rowsUrl = `/functions/${key}/part/rows${previewTable ? `?preview_table=${encodeURIComponent(previewTable)}` : ""}`;
  return Layout({
    title: key,
    subtitle: "",
    functionPage: true,
    children: (
      <div class="function-view" data-function={key}>
        <Result result={result} />
        <div class="function-heading">
          <div>
            <span data-breadcrumb>
              <a href="/functions">Functions</a> /
            </span>{" "}
            <h1
              tabindex={0}
              data-hover={sourceDetail}
              data-next-href={`/functions/${key}/logs`}
              data-next-label="Open logs →"
            >
              {title}
            </h1>
          </div>
          <div class="actions desktop-actions">
            <a
              href={
                verdict.label === "Healthy" ? verdict.href : "#output-preview"
              }
            >
              See rows
            </a>
          </div>
        </div>
        <div class="function-identity">
          <FunctionVerdict verdict={verdict} />
          <CopyChip value={key} />
          <span class="function-meta">
            {s.cadence_tag
              ? s.cadence_tag[0]!.toUpperCase() + s.cadence_tag.slice(1)
              : "On demand"}{" "}
            · <RightsChip labels={labels} />
          </span>
        </div>
        <FlowLine
          reads={
            Array.isArray(page.manifest.reads) && page.manifest.reads.length
              ? page.manifest.reads.map(String)
              : [source?.brand ?? source?.family ?? "not measured yet"]
          }
          writes={s.writes}
        />
        <p class="function-fact">
          {last ? (
            <>
              {health.total ? (
                <>
                  Read {health.read} of {health.total} targets
                </>
              ) : (
                <>Last run</>
              )}{" "}
              <RelativeTime at={last.created_at} /> ·{" "}
              {Number(last.rows_written).toLocaleString("en")} rows landed
            </>
          ) : (
            <>
              Not measured yet.{" "}
              <a href={`/functions/${key}/logs`}>Open logs →</a>
            </>
          )}
        </p>
        <div class="function-controls" id="run-controls">
          {verdict.command ? (
            <Action
              source={key}
              action={verdict.command}
              label={verdict.action ?? "Probe"}
              scope={scope}
              enabled={true}
              disabled={!!disabled}
              hover={
                verdict.command === "probe"
                  ? "Makes a few live requests. Publishes nothing."
                  : undefined
              }
            />
          ) : verdict.action && verdict.label !== "Healthy" ? (
            <a class="button" href={verdict.href}>
              {verdict.action}
            </a>
          ) : verdict.label === "Healthy" ? (
            <Action
              source={key}
              action="run"
              label="Run now"
              scope={scope}
              disabled={!!disabled}
              hover={`Reads all ${health.total} targets now and lands rows.`}
            />
          ) : null}
          {verdict.runbook && <a href={verdict.runbook}>Recovery guide →</a>}
          <details class="function-menu">
            <summary aria-label="More function actions">⋯</summary>
            <div class="function-menu-body">
              <span class="muted">Layer: {s.layer}</span>
              <Action
                source={key}
                action="probe"
                label="Probe"
                scope={scope}
                disabled={!!disabled}
                hover="Makes a few live requests. Publishes nothing."
              />
              <Action
                source={key}
                action="pause"
                label={s.enabled ? "Pause" : "Resume"}
                scope={scope}
                enabled={!s.enabled}
              />
              <a href={`/functions/${key}/logs`}>Logs →</a>
              <a href={`/explorer?q=${encodeURIComponent(key)}`}>
                Function in Explorer →
              </a>
            </div>
          </details>
        </div>
        <div class="function-grid">
          <TargetStrip health={health} source={key} />
          {open.success && open.data === "live" ? (
            inline
          ) : (
            <RunStrip runs={page.last_runs} week={week} source={key} />
          )}
        </div>
        {open.success && open.data === "targets" ? (
          <section class="card" id="targets">
            {inline}
          </section>
        ) : null}
        <section class="card" id="output-preview">
          <div class="row">
            <h2>Latest rows</h2>
            <div class="row-tabs" role="tablist" aria-label="Output table">
              {s.writes.map((t) => (
                <a
                  role="tab"
                  data-density-value
                  aria-selected={t === previewTable}
                  data-rows-tab
                  href={`/functions/${key}?open=rows&preview_table=${encodeURIComponent(t)}#output-preview`}
                  data-part-url={`/functions/${key}/part/rows?preview_table=${encodeURIComponent(t)}`}
                >
                  {t.replace(/^raw\./, "")}
                </a>
              ))}
            </div>
          </div>
          <div
            data-auto-part={context.identity.admin ? rowsUrl : undefined}
            data-loaded={
              open.success && open.data === "rows" ? "true" : undefined
            }
          >
            {!context.identity.admin ? (
              <p>
                Output previews need admin. <a href="/workbench">Workbench →</a>
              </p>
            ) : open.success && open.data === "rows" ? (
              inline
            ) : (
              <a href={`/functions/${key}?open=rows#output-preview`}>
                See rows →
              </a>
            )}
          </div>
        </section>
        {(s.parked_inputs > 0 ||
          drift.length > 0 ||
          Number(last?.rows_rejected) > 0) && (
          <ProblemsCard>
            {Number(last?.rows_rejected) > 0 && (
              <p>
                {last?.rows_rejected} rejected ·{" "}
                <a href={`/functions/${key}?open=rows#output-preview`}>
                  Sample →
                </a>{" "}
                <a href="/runbooks/partial-coverage">Recovery guide →</a>
              </p>
            )}
            {s.parked_inputs > 0 && (
              <div id="parked-inputs" class="row">
                <span
                  data-hover="Repeated failures leave these inputs out of new reads. Release them after fixing the cause."
                  data-next-href={`/functions/${key}/logs`}
                  data-next-label="Open logs →"
                  tabindex={0}
                >
                  {s.parked_inputs} parked inputs
                </span>
                <Action
                  source={key}
                  action="unpark"
                  label="Release"
                  scope={scope}
                />
              </div>
            )}
            {drift.map((d) => (
              <div class="problem-line">
                <span>
                  {d.changes.length} changed columns in {d.table} ·{" "}
                  {d.changes.map((c) => c.name).join(", ")}
                </span>
                <DriftPopover
                  changes={d.changes}
                  fingerprint={d.fingerprint}
                  source={key}
                />
              </div>
            ))}
          </ProblemsCard>
        )}
        {(["receipts", "schema", "settings", "cursors"] as const).map(
          (part) => (
            <LazyDisclosure
              source={key}
              part={part}
              label={
                part === "receipts"
                  ? "Receipts · latest run"
                  : part === "schema"
                    ? `Schema · ${page.fingerprint_history.length} versions`
                    : part === "settings"
                      ? `Settings · ${s.batch_size} per batch · ${s.max_concurrency} concurrent`
                      : "Cursors"
              }
              open={open.success && open.data === part}
            >
              {open.success && open.data === part ? inline : undefined}
            </LazyDisclosure>
          ),
        )}
        {!s.enabled && (
          <p class="muted">
            Paused since{" "}
            {pauses.find((p) => p.source_key === key)?.at
              ? String(pauses.find((p) => p.source_key === key)?.at)
              : "not measured yet"}
            . <a href="?open=settings#settings">Open settings →</a>
          </p>
        )}
        <dialog id="target-drill" class="row-sheet">
          <form method="dialog">
            <button class="secondary">Close</button>
          </form>
          <div data-drill-body />
          <a href={`/functions/${key}?open=targets#targets`}>All targets →</a>
        </dialog>
      </div>
    ),
  });
}
export async function functionPart(
  context: Context,
  key: string,
  part: FunctionPart,
  query: {
    preview_table?: string;
    preview_cursor?: string;
    state?: string;
    run?: string;
  } = {},
) {
  const c = createRouterClient(router, { context });
  const s = await c.streamlines.get({ source_key: key });
  if (part === "live") {
    const recent = await rows(
      context.db,
      runDto,
      "SELECT * FROM control.run WHERE streamline_id=$1 ORDER BY created_at DESC LIMIT 20",
      [s.id],
    );
    const week = await rows(
      context.db,
      runDto,
      "SELECT * FROM control.run WHERE streamline_id=$1 AND created_at>now()-interval '7 days' ORDER BY created_at DESC LIMIT 40",
      [s.id],
    );
    return <RunStrip runs={recent} week={week} source={key} />;
  }
  if (part === "targets") {
    const health = await functionTargetHealth(context.db, s.id);
    const selected = health.rows.filter(
      (t) => !query.state || query.state === "all" || t.state === query.state,
    );
    return (
      <div data-target-list>
        <h2>Targets · {selected.length}</h2>
        <label>
          Filter targets
          <input data-target-filter placeholder="Find a target" />
        </label>
        <label>
          State
          <select
            data-target-state
            data-part-url={`/functions/${key}/part/targets`}
          >
            <option
              value="all"
              selected={!query.state || query.state === "all"}
            >
              All states
            </option>
            {Object.entries(targetWords).map(([state, label]) => (
              <option value={state} selected={query.state === state}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div class="target-list">
          {selected.map((t, i) => (
            <a hidden={i >= 25} data-target-row href={`/targets/${t.id}`}>
              <strong>{t.name}</strong>
              <span>
                {targetWords[t.state]} ·{" "}
                {t.at ? <RelativeTime at={t.at} /> : "not measured yet"}
              </span>
            </a>
          ))}
        </div>
        {selected.length > 25 && (
          <button class="secondary" data-target-all>
            Show all {selected.length}
          </button>
        )}
        {!selected.length && (
          <p>
            No matching targets.{" "}
            <a href={`/functions/${key}?open=targets#targets`}>All targets →</a>
          </p>
        )}
      </div>
    );
  }
  if (part === "rows") {
    if (!context.identity.admin)
      return (
        <p>
          Output previews need admin. <a href="/workbench">Workbench →</a>
        </p>
      );
    const page = await c.functions.page({
      source_key: key,
      preview_table: query.preview_table ?? s.writes[0],
      ...(query.preview_cursor ? { preview_cursor: query.preview_cursor } : {}),
    });
    const preview = page.output_preview.find(
      (p) => p.table === (query.preview_table ?? s.writes[0]),
    );
    const last = page.last_runs.find((r) => r.status !== "superseded");
    return (
      <>
        {preview ? (
          <LatestRows
            preview={{ ...preview, rows: preview.rows.slice(0, 10) }}
            source={key}
            {...(last ? { run: last, total: last.rows_written } : {})}
          />
        ) : (
          <p>
            No output rows yet.{" "}
            <a href={`/functions/${key}/logs`}>Open logs →</a>
          </p>
        )}
        {rejectedByReason(page.rejected_sample).map((g) => (
          <p>
            {g.count} sampled rejects ·{" "}
            {rejectLabel(String(g.sample.reason ?? g.code))} ·{" "}
            <a href={`/runs/${String(g.sample._run_id ?? last?.id ?? "")}`}>
              Sample →
            </a>{" "}
            <a href="/runbooks/partial-coverage">Recovery guide →</a>
          </p>
        ))}
      </>
    );
  }
  if (part === "receipts") {
    const latest = await rows(
      context.db,
      runDto,
      "SELECT * FROM control.run WHERE streamline_id=$1 AND status<>'superseded' AND ($2::uuid IS NULL OR id=$2) ORDER BY created_at DESC LIMIT 1",
      [s.id, query.run ? z.uuid().parse(query.run) : null],
    );
    if (!latest[0])
      return (
        <p>
          No run yet. <a href={`/functions/${key}/logs`}>Open logs →</a>
        </p>
      );
    const poll = await c.runs.get({ id: latest[0].id });
    const coverage = await runCoverage(context.db, [latest[0].id]);
    return (
      <>
        <RunReceipts
          runs={latest}
          receipts={[poll.receipts.slice(0, 25)]}
          coverage={coverage}
          expanded
          id="latest-receipts"
          showCoverage={false}
        />
        <a href={`/runs/${latest[0].id}`}>Open run →</a>
      </>
    );
  }
  if (part === "schema") {
    const page = await c.functions.page({
      source_key: key,
      metadata_only: true,
    });
    const history = driftHistory(page.fingerprint_history);
    return (
      <>
        {history.map((d) => (
          <div>
            <h3>{d.table}</h3>
            <DriftPopover
              changes={d.changes}
              fingerprint={d.fingerprint}
              source={key}
              acknowledged={s.acknowledged_fingerprints.includes(
                `${key}:${d.table}:${d.fingerprint}`,
              )}
            />
            <SchemaDisclosure
              columns={Object.entries(
                z
                  .record(z.string(), z.unknown())
                  .parse(
                    page.fingerprint_history.find(
                      (h) => h.fingerprint === d.fingerprint,
                    )?.columns ?? {},
                  ),
              ).map(([name, type]) => ({
                name,
                type: String(type),
                nullable: true,
              }))}
            />
          </div>
        ))}
        <a href={`/explorer?q=${encodeURIComponent(key)}`}>
          Function in Explorer →
        </a>
      </>
    );
  }
  if (part === "settings") {
    return (
      <>
        <p>
          {s.batch_size} per batch · {s.max_concurrency} concurrent ·{" "}
          {s.storage} · {s.cadence_tag}
        </p>

        <a href={`/functions/${key}/logs`}>Open logs →</a>
      </>
    );
  }
  const cursors = await rows(
    context.db,
    jsonRow,
    "SELECT target_id,cursor_key FROM control.cursor WHERE streamline_id=$1 ORDER BY cursor_key LIMIT 100",
    [s.id],
  );
  return (
    <>
      {cursors.length ? (
        <form method="post" action="/actions/reset-cursor">
          <input type="hidden" name="source_key" value={key} />
          <input type="hidden" name="back" value={`/functions/${key}`} />
          <label>
            Cursor
            <select name="cursor_choice">
              {cursors.map((cursor) => (
                <option value={JSON.stringify(cursor)}>
                  {String(cursor.cursor_key)}
                </option>
              ))}
            </select>
          </label>
          <button>Reset cursor generation</button>
        </form>
      ) : (
        <p>
          No cursor yet. <a href={`/functions/${key}/logs`}>Open logs →</a>
        </p>
      )}
    </>
  );
}
