import { functionPart, functionParts } from "./function-view.js";
import { functionVerdict } from "./console-semantics.js";
import { FunctionVerdict } from "./function-components.js";
import { readSources } from "./platform.js";
import { bundledRunbook, RunbookBody } from "./runbooks.js";
import { healthSummary } from "./status.js";
import {
  consoleFailures,
  runCoverage,
  sourceLabels,
  targetHealth,
} from "./console-data.js";
import {
  FailureBanner,
  LabelChips,
  CoverageMeter,
  RefusalPopover,
  TargetHealth,
} from "./primitives.js";
import { sandboxPages } from "./sandbox-page.js";
import { workbenchPages } from "./workbench-page.js";
import { explorerPages } from "./explorer-page.js";
import { referencePage } from "./reference-page.js";
import { Sparkline, RelativeTime, RunReceipts } from "./run-components.js";
import { savePreview, readPreview } from "./import-preview.js";
import { randomUUID } from "node:crypto";
import { uiScript } from "./ui.js";
import { sanitize } from "./audit.js";
import { Hono } from "hono";
import { bodyLimit } from "hono/body-limit";
import { RPCHandler } from "@orpc/server/fetch";
import { OpenAPIHandler } from "@orpc/openapi/fetch";
import { createRouterClient, ORPCError } from "@orpc/server";
import { z } from "zod";
import { router, type Context } from "./router.js";
import { disableOperatorForms, isOperatorRequest } from "./staff-access.js";
import { authenticate, checkOrigin } from "./auth.js";
import { database, rows, one, AppError, platformRead } from "./db.js";
import {
  opsPage,
  statusPage,
  platformPage,
  tenantsPage,
  functionView,
  weeklyPage,
  action,
  Layout,
  Table,
  Result,
  Badge,
  css,
} from "./pages.js";
import { verifyWebhook, webhookPayload } from "./webhook.js";
import { errorHint, jsonRow, runDto } from "@mdp/contracts";
export const app = new Hono<{ Variables: { context: Context } }>();
const rpc = new RPCHandler(router);
const api = new OpenAPIHandler(router);
app.use("*", bodyLimit({ maxSize: 1_100_000 }));
app.onError((err, c) => {
  const data =
    err instanceof AppError || err instanceof ORPCError
      ? {
          error_class:
            err instanceof AppError
              ? err.error_class
              : String(
                  (err.data as { error_class?: string } | undefined)
                    ?.error_class ?? err.code,
                ),
          message: err.message,
        }
      : {
          error_class: "internal_error",
          message: "Page unavailable. Try again.",
        };
  console.error(
    JSON.stringify({
      event: "request_failed",
      type: err.constructor.name,
      message: err.message,
    }),
  );
  const hint = errorHint(data.error_class);
  if (
    c.req.method === "GET" &&
    !c.req.path.startsWith("/api/") &&
    !c.req.path.startsWith("/rpc/")
  ) {
    const notFound = err instanceof AppError && err.status === 404;
    return c.html(
      Layout({
        title: notFound ? "Not found" : "Request failed",
        subtitle: data.message,
        children: (
          <section class="card error">
            <p>{hint.next_step}</p>
            {hint.runbook && (
              <a href={`/runbooks/${hint.runbook}`}>Open runbook →</a>
            )}
            <div class="actions">
              {!notFound && (
                <a class="button retry-button" href={c.req.path}>
                  Try again
                </a>
              )}
              <a href="/functions">Functions →</a>
              <a href="/ops">Ops →</a>
            </div>
          </section>
        ),
      }),
      err instanceof AppError || err instanceof ORPCError
        ? (err.status as 400)
        : 500,
    );
  }
  return new Response(
    JSON.stringify({
      ...data,
      next_step: hint.next_step,
      runbook: hint.runbook ? `/runbooks/${hint.runbook}` : null,
      next_steps: [
        { label: "Functions", href: "/functions" },
        { label: "Ops", href: "/ops" },
      ],
    }),
    {
      status:
        err instanceof AppError || err instanceof ORPCError ? err.status : 500,
      headers: { "content-type": "application/json" },
    },
  );
});
app.notFound(() => {
  throw new AppError(
    "not_found",
    "No page or API route matches this address",
    404,
  );
});
app.get("/style.css", (c) => c.text(css, 200, { "content-type": "text/css" }));
app.get("/ui.js", (c) =>
  c.text(uiScript, 200, { "content-type": "text/javascript" }),
);
app.get("/favicon.ico", (c) =>
  c.text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#102d35"/><path d="M8 22V10h6v12m4 0V6h6v16" fill="none" stroke="#fff" stroke-width="3"/></svg>',
    200,
    { "content-type": "image/svg+xml" },
  ),
);
app.get("/health", (c) => c.json({ status: "ok" }));
// Share one bounded read across public probes for 30 seconds, including failed reads.
let healthRead: Promise<Awaited<ReturnType<typeof healthSummary>>> | undefined;
let healthReadUntil = 0;
app.get("/health/status", async (c) => {
  c.header("Cache-Control", "no-store");
  try {
    if (!healthRead || Date.now() >= healthReadUntil) {
      healthReadUntil = Date.now() + 30_000;
      healthRead = platformRead(database(), healthSummary);
    }
    const summary = await healthRead;
    return c.json(summary, summary.ok ? 200 : 503);
  } catch {
    return c.json(
      {
        ok: false,
        next_step: "Open /ops and check the control API connection.",
      },
      503,
    );
  }
});
app.post("/webhooks/dbt", async (c) => {
  const raw = await c.req.text();
  verifyWebhook(
    raw,
    c.req.header("authorization") ?? c.req.header("x-dbt-signature") ?? null,
    process.env.DBT_CLOUD_WEBHOOK_SECRET,
  );
  const context: Context = {
    identity: {
      actor: "dbt-webhook",
      tenant_id: null,
      tenant_slug: null,
      admin: true,
    },
    db: database(),
  };
  return c.json(
    await createRouterClient(router, { context }).dbt.webhook(
      webhookPayload(JSON.parse(raw)),
    ),
  );
});
app.use("*", async (c, next) => {
  if (c.req.method === "POST") checkOrigin(c.req.raw);
  const identity = await authenticate(c.req.raw);
  // A promoter key reaches the target API only; the router checks the procedure.
  if (
    !identity.admin &&
    !identity.staff &&
    !(identity.promoter && c.req.path.startsWith("/api/targets"))
  )
    throw new AppError(
      "forbidden",
      "Console access needs staff or admin. Ask an operator for a staff key; open the access runbook.",
      403,
    );
  if (
    identity.staff &&
    !identity.admin &&
    isOperatorRequest(c.req.method, c.req.path)
  )
    throw new AppError(
      "forbidden",
      "This action needs the admin role. Ask an operator to perform it; open the access runbook.",
      403,
    );
  c.set("context", { identity, db: database() });
  await next();
});
app.get("/queries", async (c) => {
  const result = await createRouterClient(router, {
    context: c.get("context"),
  })
    .workbench.queries({})
    .catch(() => null);
  if (!result) {
    const hint = errorHint("query_history_unavailable");
    return c.html(
      Layout({
        title: "Query review",
        subtitle: "",
        children: (
          <section
            class="card error"
            data-error-class="query_history_unavailable"
          >
            <p>
              {hint.summary} <a href="/workbench">Open Workbench</a> to run a
              query.
            </p>
          </section>
        ),
      }),
    );
  }
  return c.html(
    Layout({
      title: "Query review",
      subtitle: c.get("context").identity.admin
        ? "Cross-tenant inputs appear first. Labels describe all input tenants, including rows narrowed by filters."
        : "Your workbench queries. Open Workbench to run another query.",
      children: (
        <section class="card">
          <table>
            <thead>
              <tr>
                <th>Review</th>
                <th>Who</th>
                <th>When</th>
                <th>Tenants</th>
                <th>Labels</th>
                <th>Query hash</th>
              </tr>
            </thead>
            <tbody>
              {result.queries.map((q) => (
                <tr
                  style={
                    q.cross_tenant || q.unresolved ? "color:var(--danger)" : ""
                  }
                >
                  <td>
                    {q.cross_tenant
                      ? "Mixes tenant data"
                      : q.unresolved
                        ? "Unresolved inputs"
                        : "One scope"}
                  </td>
                  <td>{String(q.actor)}</td>
                  <td>{String(q.occurred_at)}</td>
                  <td>{JSON.stringify(q.tenants)}</td>
                  <td>{JSON.stringify(q.labels)}</td>
                  <td>
                    <code>{String(q.query_hash)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ),
    }),
  );
});
for (const path of ["/rpc/showcase/*", "/api/showcase/*"]) {
  app.use(path, async (c, next) => {
    await next();
    c.header("Cache-Control", "no-store");
  });
}

app.use("/rpc/*", async (c, next) => {
  const result = await rpc.handle(c.req.raw, {
    prefix: "/rpc",
    context: c.get("context"),
  });
  return result.matched ? result.response : next();
});
app.use("/api/*", async (c, next) => {
  const result = await api.handle(c.req.raw, {
    prefix: "/api",
    context: c.get("context"),
  });
  return result.matched ? result.response : next();
});
// One server-rendered safety surface covers all authenticated HTML pages.
app.use("*", async (c, next) => {
  await next();
  if (
    c.req.method !== "GET" ||
    !c.res.headers.get("content-type")?.includes("text/html")
  )
    return;
  if (/^\/functions\/[^/]+\/part\//.test(c.req.path)) {
    if (!c.get("context").identity.admin)
      c.res = new Response(disableOperatorForms(await c.res.text()), {
        status: c.res.status,
        headers: c.res.headers,
      });
    return;
  }
  const failures = await consoleFailures(c.get("context").db);
  const banner = failures.length
    ? await FailureBanner({ failures, all: c.req.path === "/ops" })?.toString()
    : "";
  const html = await c.res.text();
  const body = html.includes('class="function-page"')
    ? html.replace('<div class="footer">', `${banner}<div class="footer">`)
    : html.replace("<main>", `<main>${banner}`);
  c.res = new Response(
    c.get("context").identity.admin ? body : disableOperatorForms(body),
    { status: c.res.status, headers: c.res.headers },
  );
});
app.route("/workbench", workbenchPages);
app.route("/sandboxes", sandboxPages);
app.route("/sandbox", sandboxPages);
app.route("/explorer", explorerPages);
app.get("/", (c) => c.redirect("/ops"));
async function actionResult(context: Context, id: string | undefined) {
  if (!id) return undefined;
  const found = await rows(
    context.db,
    jsonRow,
    "SELECT id,action,after FROM control.audit_log WHERE id=$1 AND actor=$2",
    [z.uuid().parse(id), context.identity.actor],
  );
  const row = found[0];
  if (!row) return undefined;
  const detail = z.record(z.string(), z.unknown()).parse(row.after);
  const result = z.record(z.string(), z.unknown()).safeParse(detail.result);
  if (result.success && typeof result.data.run_id === "string") {
    const live = await rows(
      context.db,
      z.object({ status: z.string() }),
      "SELECT status FROM control.run WHERE id=$1",
      [result.data.run_id],
    );
    if (live[0]) detail.result = { ...result.data, status: live[0].status };
  }
  return { ...detail, action: row.action, audit_id: row.id };
}
app.get("/ops/platform", async (c) =>
  c.html(await platformPage(c.get("context"), c.req.query())),
);
app.get("/status", async (c) => c.html(await statusPage(c.get("context"))));
app.get("/tenants", async (c) =>
  c.html(
    await tenantsPage(
      c.get("context"),
      await actionResult(c.get("context"), c.req.query("result")),
      c.req.query("key"),
    ),
  ),
);
app.get("/ops", async (c) =>
  c.html(
    await opsPage(
      c.get("context"),
      await actionResult(c.get("context"), c.req.query("result")),
      c.req.query(),
    ),
  ),
);
app.get("/functions", async (c) => {
  const context = c.get("context");
  const client = createRouterClient(router, { context });
  const [functions, runs, data] = await Promise.all([
    client.streamlines.list({}),
    rows(
      context.db,
      runDto,
      "SELECT DISTINCT ON (streamline_id) * FROM control.run WHERE status<>'superseded' ORDER BY streamline_id,created_at DESC",
    ),
    readSources(context.db),
  ]);
  const cadence = c.req.query("cadence");
  const cards = functions
    .filter((f) => !cadence || f.cadence_tag === cadence)
    .map((f) => {
      const last = runs.find((r) => r.streamline_id === f.id);
      const source = data.sources.find((s) => s.source_key === f.source_key);
      let verdict = functionVerdict(
        f,
        last,
        undefined,
        f.parked_inputs,
        0,
        source?.evidence?.incident
          ? [{ class: source.evidence.incident.class }]
          : [],
      );
      if (verdict.label === "Healthy" && last?.coverage === "partial")
        verdict = { ...verdict, label: "Needs a look" };
      const group = verdict.label === "Failing" ? "Needs a look" : verdict.label;
      return { f, last, source, verdict, group };
    });
  const group = (label: string) => (
    <div class="grid function-grid">
      {cards
        .filter((c) => c.group === label)
        .map(({ f, last, source, verdict }) => (
          <a
            class="card function-card"
            data-fn-card
            href={`/functions/${f.source_key}`}
          >
            <div class="function-heading">
              <h2>{source?.display_name ?? f.source_key}</h2>
              <FunctionVerdict verdict={verdict} />
            </div>
            <code>{f.source_key}</code>
            <p class="muted">
              {last ? (
                <>
                  Read <RelativeTime at={last.created_at} /> ·{" "}
                  {Number(last.rows_written).toLocaleString("en")} rows
                </>
              ) : (
                "No rows yet"
              )}
            </p>
            <span>
              {f.cadence_tag
                ? f.cadence_tag[0]!.toUpperCase() + f.cadence_tag.slice(1)
                : "On demand"}
            </span>
            {source && source.days.every((day) => Number(day.entries) === 0) ? (
              <p class="muted">No rows in the last 14 days</p>
            ) : (
              <div class="daily-bars" aria-label="Rows over 14 days">
                {source?.days.map((day) => (
                  <span
                    style={`height:${Math.max(3, (Number(day.entries) * 48) / Math.max(1, ...source.days.map((d) => Number(d.entries))))}px`}
                    title={`${day.day}: ${day.entries} rows`}
                  />
                ))}
              </div>
            )}
          </a>
        ))}
    </div>
  );
  return c.html(
    Layout({
      title: "Functions",
      subtitle: "",
      children: (
        <div class="function-list">
          <label>
            Find a function
            <input type="search" data-fn-filter placeholder="Find a function" />
          </label>
          {["Needs a look", "Running", "Healthy", "Not run yet", "Paused"].map(
            (label) =>
              label === "Paused" ? (
                <details data-fn-group>
                  <summary>
                    Paused · {cards.filter((c) => c.group === label).length}
                  </summary>
                  {group(label)}
                </details>
              ) : (
                <section data-fn-group>
                  <h2>{label}</h2>
                  {group(label)}
                  {!cards.some((c) => c.group === label) && (
                    <p class="muted">
                      None. <a href="/functions">All functions →</a>
                    </p>
                  )}
                </section>
              ),
          )}
          <p hidden data-fn-empty>
            No functions match.{" "}
            <button class="secondary" data-clear-fn>
              Clear search
            </button>
          </p>
        </div>
      ),
    }),
  );
});
app.get("/targets/:id", async (c) => {
  const client = createRouterClient(router, { context: c.get("context") });
  const id = z.uuid().parse(c.req.param("id"));
  const target = (await client.targets.list({})).find((t) => t.id === id);
  if (!target) throw new AppError("not_found", "Target does not exist", 404);
  const alerts = await client.alerts.list({ subject_id: id });
  const health = (await targetHealth(c.get("context").db, [id]))[id];
  const audits = await rows(
    c.get("context").db,
    jsonRow,
    "SELECT id,action,at FROM control.audit_log WHERE subject=$1 OR after->'input'->>'id'=$1 ORDER BY at DESC LIMIT 20",
    [id],
  );
  return c.html(
    Layout({
      title: "Target",
      subtitle: target.handle ?? target.platform_account_id ?? id,
      children: (
        <>
          <Result
            result={await actionResult(c.get("context"), c.req.query("result"))}
          />
          {health && (
            <section class="card">
              <TargetHealth health={health} back={`/targets/${id}`} />
            </section>
          )}
          <section class="card">
            <h2>Recent changes</h2>
            {audits.map((a) => (
              <p>
                <a href={`/audit/${a.id}`}>{String(a.action)} · audit</a> ·{" "}
                <RelativeTime at={String(a.at)} />
              </p>
            ))}
          </section>
          <section class="card">
            <h2>Settings</h2>
            <form class="stack" method="post" action="/actions/review">
              <input type="hidden" name="id" value={id} />
              <label>
                Handle
                <input name="handle" value={target.handle ?? ""} />
              </label>
              <label>
                Display name
                <input name="display_name" value={target.display_name ?? ""} />
              </label>
              <label>
                Activation
                <select name="active">
                  <option value="false">Inactive</option>
                  <option
                    value="true"
                    selected={Boolean(
                      target.activated_at && !target.deactivated_at,
                    )}
                  >
                    Active (resolved identities only)
                  </option>
                </select>
              </label>
              <button>Save</button>
            </form>
          </section>
          <section class="card">
            <h2>Provider identity</h2>
            <p>Correct and verify the provider account ID before activating.</p>
            <form method="post" action="/actions/resolve">
              <input type="hidden" name="id" value={id} />
              <input
                name="platform_account_id"
                aria-label="Platform account ID"
                value={target.platform_account_id ?? ""}
                required
              />
              <button>Resolve</button>
            </form>
          </section>
          <section class="card">
            <h2>Alerts</h2>
            <Table
              nextStep={{ label: "View functions", href: "/functions" }}
              rows={alerts}
              empty="No unresolved alerts for this target."
            />
          </section>
        </>
      ),
    }),
  );
});
app.get("/functions/:source_key", async (c) => {
  const key = c.req.param("source_key");
  const found = await rows(
    c.get("context").db,
    jsonRow,
    "SELECT id FROM control.streamline WHERE source_key=$1",
    [key],
  );
  if (!found.length)
    throw new AppError("not_found", `No function is registered as ${key}`, 404);
  return c.html(
    await functionView(
      c.get("context"),
      c.req.param("source_key"),
      await actionResult(c.get("context"), c.req.query("result")),
      {
        ...(c.req.query("open") ? { open: c.req.query("open")! } : {}),
        ...(c.req.query("state") ? { state: c.req.query("state")! } : {}),
        ...(c.req.query("run") ? { run: c.req.query("run")! } : {}),
        ...(c.req.query("preview_table")
          ? { preview_table: c.req.query("preview_table")! }
          : {}),
        ...(c.req.query("preview_cursor")
          ? { preview_cursor: c.req.query("preview_cursor")! }
          : {}),
      },
    ),
  );
});
app.get("/functions/:source_key/part/:part", async (c) => {
  const part = functionParts.safeParse(c.req.param("part"));
  if (!part.success)
    throw new AppError(
      "not_found",
      "No section matches this link. Open the function.",
      404,
    );
  return c.html(
    await functionPart(
      c.get("context"),
      c.req.param("source_key"),
      part.data,
      c.req.query(),
    ),
  );
});
app.get("/functions/:source_key/preview.csv", async (c) => {
  const page = await createRouterClient(router, {
    context: c.get("context"),
  }).functions.page({
    source_key: c.req.param("source_key"),
    ...(c.req.query("table") ? { preview_table: c.req.query("table")! } : {}),
    ...(c.req.query("preview_cursor")
      ? { preview_cursor: c.req.query("preview_cursor")! }
      : {}),
  });
  const preview = page.output_preview.find(
    (p) => p.table === c.req.query("table"),
  );
  if (!preview)
    throw new AppError(
      "not_found",
      "No declared output matches this preview",
      404,
    );
  const columns = Object.keys(preview.rows[0] ?? {});
  const cell = (value: unknown) => {
    let text =
      value === null || value === undefined
        ? ""
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
    if (/^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return c.text(
    [
      columns.map(cell).join(","),
      ...preview.rows.map((row) =>
        columns.map((col) => cell(row[col])).join(","),
      ),
    ].join("\r\n"),
    200,
    {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": "attachment; filename=preview.csv",
    },
  );
});
app.get("/reference", async (c) =>
  c.html(
    await referencePage(
      c.get("context"),
      await actionResult(c.get("context"), c.req.query("result")),
    ),
  ),
);
app.get("/screen/weekly", async (c) =>
  c.html(await weeklyPage(c.get("context"))),
);
app.post("/actions/run-sample", (c) =>
  c.html(
    Layout({
      title: "Choose a function action",
      subtitle: "",
      children: (
        <p>
          This action is no longer available. Choose Run now or Probe on the
          function page. <a href="/functions">Open functions →</a>
        </p>
      ),
    }),
    410,
  ),
);
app.post("/actions/:action", async (c) => {
  const data = await c.req.parseBody({ all: true });
  const fields: Record<string, string> = {};
  for (const [k, v] of Object.entries(data))
    if (typeof v === "string") fields[k] = v;
    else if (Array.isArray(v))
      fields[k] = v
        .filter((item): item is string => typeof item === "string")
        .join(",");
  const file = data.file;
  if (file instanceof File && file.size) fields.csv = await file.text();
  if (fields.preview_token) {
    const preview = readPreview(
      fields.preview_token,
      c.get("context").identity.actor,
    );
    fields.csv = preview.csv;
    fields.target_set_id = preview.target_set_id;
  }
  const previewToken =
    c.req.param("action") === "import"
      ? savePreview(
          c.get("context").identity.actor,
          fields.csv ?? "",
          fields.target_set_id ?? "",
        )
      : undefined;
  let result: unknown;
  let failed = false;
  try {
    result = await action(c.get("context"), c.req.param("action"), fields);
  } catch (e) {
    failed = true;
    result =
      e instanceof ORPCError
        ? (e.data ?? { error_class: e.code, message: e.message })
        : e instanceof AppError
          ? { error_class: e.error_class, message: e.message }
          : {
              error_class: "invalid_request",
              message: e instanceof Error ? e.message : "Operation failed",
            };
  }
  const context = c.get("context");
  let auditId = context.audit_id;
  if (!auditId || c.req.param("action") === "ack-alert-group") {
    auditId = randomUUID();
    await context.db.unsafe(
      "INSERT INTO control.audit_log(id,actor,action,subject,after) VALUES ($1,$2,$3,$4,$5::text::jsonb)",
      [
        auditId,
        context.identity.actor,
        c.req.param("action") === "ack-alert-group"
          ? "alerts.acknowledgeGroup"
          : `form.${c.req.param("action")}`,
        fields.id ?? fields.source_key ?? "form",
        JSON.stringify({
          state: failed ? "failed" : "succeeded",
          input: sanitize(fields),
          result: sanitize(result),
        }),
      ],
    );
  }
  const back =
    fields.back &&
    /^\/(?:ops(?:#[a-z-]+)?|tenants|reference|functions\/[a-z][a-z0-9_]*|targets\/[0-9a-f-]+)$/.test(
      fields.back,
    )
      ? fields.back
      : "/ops";
  const location = new URL(back, c.req.url);
  location.searchParams.set("result", auditId);
  if (previewToken) location.searchParams.set("preview", previewToken);
  return c.redirect(location.pathname + location.search + location.hash, 303);
});
app.get("/runbooks/:slug", async (c) => {
  const r =
    bundledRunbook(c.req.param("slug")) ??
    (await one(
      database(),
      z.object({ title: z.string(), body_md: z.string() }),
      "SELECT title,body_md FROM control.runbook WHERE slug=$1",
      [c.req.param("slug")],
    ));
  return c.html(
    Layout({
      title: r.title,
      subtitle: "Recovery runbook",
      children: (
        <section class="card">
          <RunbookBody body={r.body_md} />
          <div class="actions">
            <a href="/runbooks/runners-held">Check held runners →</a>
            <a href="/runbooks/service-unreachable#external-heartbeat">
              Set up heartbeat →
            </a>
          </div>
          <a href="/ops">Open operations →</a>
        </section>
      ),
    }),
  );
});
app.get("/runs", async (c) => {
  const client = createRouterClient(router, { context: c.get("context") });
  const runs = await client.runs.list({});
  const status = c.req.query("status");
  return c.html(
    Layout({
      title: "Runs",
      subtitle: status ? `${status} runs` : "",
      children: (
        <section class="card">
          <p>
            <a href="/screen/weekly">Weekly overview →</a>
          </p>
          {runs
            .filter((r) => !status || r.status === status)
            .map((r) => (
              <p class="row">
                <a href={`/runs/${r.id}`}>{r.id.slice(0, 8)} →</a>
                <Badge value={r.status} />
                <span>{r.rows_written} landed</span>
                <RelativeTime at={r.created_at} />
              </p>
            ))}
        </section>
      ),
    }),
  );
});
app.get("/runs/:id", async (c) => {
  const client = createRouterClient(router, { context: c.get("context") });
  const id = c.req.param("id");
  const found = await rows(
    c.get("context").db,
    jsonRow,
    "SELECT id FROM control.run WHERE id::text=$1 OR left(id::text,8)=$1",
    [id],
  );
  if (!found.length)
    throw new AppError("not_found", `No run exists as ${id}`, 404);
  const runId = String(found[0]!.id);
  const [state, events] = await Promise.all([
    client.runs.get({ id: runId }),
    client.runs.events({ id: runId }),
  ]);
  const coverage = await runCoverage(c.get("context").db, [runId]);
  const [stream] = await rows(
    c.get("context").db,
    jsonRow,
    "SELECT source_key,layer FROM control.streamline WHERE id=$1",
    [state.run.streamline_id],
  );
  const labels = {
    ...(stream
      ? await sourceLabels(c.get("context").db, String(stream.source_key))
      : {}),
    ...(stream ? { layer: String(stream.layer) } : {}),
    tenant: state.run.scope,
    ...(state.run.scope.startsWith("tenant:")
      ? { learning_eligible: false }
      : {}),
  };
  return c.html(
    Layout({
      title: `Run ${id.slice(0, 8)}`,
      subtitle: `${state.run.status} · ${state.run.coverage ?? "No coverage yet"} · ${state.repairs_pending} repairs pending`,
      children: (
        <>
          <section class="card">
            <div class="row">
              <h2>Run & trace</h2>
              <span
                class={`badge ${Number(state.run.rows_rejected) ? "warning" : ""}`}
              >
                rejected {state.run.rows_rejected}
              </span>
            </div>
            <LabelChips labels={labels} />
            <p>
              <CoverageMeter coverage={coverage[runId]} />
            </p>
            <p>{state.run.error_message}</p>
            {(state.run.error_class || state.run.status === "failed") && (
              <RefusalPopover
                code={state.run.error_class ?? "unmapped"}
                message={state.run.error_message ?? undefined}
                runId={runId}
                traceId={state.run.trace_id ?? id}
              />
            )}
            <a href={`/traces/${state.run.trace_id ?? id}`}>Open trace →</a>
            <Table
              nextStep={{ label: "View functions", href: "/functions" }}
              rows={[
                {
                  status: state.run.status,
                  coverage: state.run.coverage,
                  rows_written: state.run.rows_written,
                  rows_rejected: state.run.rows_rejected,
                  created_at: state.run.created_at,
                },
              ]}
            />
            <details>
              <summary>Full run record</summary>
              <pre>{JSON.stringify(state.run, null, 2)}</pre>
            </details>
          </section>
          <RunReceipts
            runs={[state.run]}
            receipts={[state.receipts]}
            coverage={coverage}
            expanded
          />
          <section class="card">
            <h2>Events</h2>
            <Table
              nextStep={{ label: "View functions", href: "/functions" }}
              rows={events}
            />
          </section>
        </>
      ),
    }),
  );
});
app.get("/traces/:id", async (c) => {
  const data = await rows(
    database(),
    jsonRow,
    "SELECT e.* FROM control.run_event e JOIN control.run r ON r.id=e.run_id WHERE (r.trace_id=$1 OR r.id::text=$1) AND ($2::text IS NULL OR e.level=$2) AND ($3::text IS NULL OR e.event_type=$3) ORDER BY e.at LIMIT 1000",
    [
      c.req.param("id"),
      c.req.query("level") || null,
      c.req.query("event") || null,
    ],
  );
  return c.html(
    Layout({
      title: "Trace timeline",
      subtitle: c.req.param("id"),
      children: (
        <section class="card">
          <form method="get">
            <label>
              Level
              <input name="level" value={c.req.query("level") ?? ""} />
            </label>
            <label>
              Event type
              <input name="event" value={c.req.query("event") ?? ""} />
            </label>
            <button class="secondary">Filter</button>
          </form>
          <Table
            nextStep={{ label: "View functions", href: "/functions" }}
            rows={data}
            empty="No recorded spans yet. Refresh after the worker begins."
          />
        </section>
      ),
    }),
  );
});
app.get("/functions/:source_key/logs", async (c) => {
  const data = await rows(
    database(),
    jsonRow,
    "SELECT e.* FROM control.run_event e JOIN control.run r ON r.id=e.run_id JOIN control.streamline s ON s.id=r.streamline_id WHERE s.source_key=$1 AND ($2::text IS NULL OR e.level=$2) AND ($3::text IS NULL OR e.event_type=$3) AND ($4::uuid IS NULL OR e.run_id=$4) AND ($5::timestamptz IS NULL OR e.at<$5) ORDER BY e.at DESC,e.id DESC LIMIT 200",
    [
      c.req.param("source_key"),
      c.req.query("level") || null,
      c.req.query("event") || null,
      c.req.query("run") ? z.uuid().parse(c.req.query("run")) : null,
      c.req.query("before")
        ? z.iso.datetime().parse(c.req.query("before"))
        : null,
    ],
  );
  return c.html(
    Layout({
      title: "Function logs",
      subtitle: c.req.param("source_key"),
      children: (
        <section class="card">
          <form method="get">
            <label>
              Level
              <input name="level" value={c.req.query("level") ?? ""} />
            </label>
            <label>
              Event type
              <input name="event" value={c.req.query("event") ?? ""} />
            </label>
            <input type="hidden" name="run" value={c.req.query("run") ?? ""} />
            <button class="secondary">Filter</button>
          </form>
          {data.length === 200 && (
            <a
              href={`?${new URLSearchParams({ ...c.req.query(), before: String(data.at(-1)?.at) })}`}
            >
              Older events →
            </a>
          )}
          <Table
            nextStep={{ label: "View functions", href: "/functions" }}
            rows={data}
          />
        </section>
      ),
    }),
  );
});

app.get("/audit", async (c) => {
  const entries = await rows(
    c.get("context").db,
    jsonRow,
    "SELECT id,action,at FROM control.audit_log ORDER BY at DESC LIMIT 50",
  );
  return c.html(
    Layout({
      title: "Audit",
      subtitle: "Open an action to inspect its audit.",
      children: (
        <section class="card">
          {entries.map((entry) => (
            <p>
              <a href={`/audit/${entry.id}`}>{String(entry.action)} →</a> ·{" "}
              <RelativeTime at={String(entry.at)} />
            </p>
          ))}
          <a href="/ops">Ops →</a>
        </section>
      ),
    }),
  );
});
app.get("/audit/:id", async (c) => {
  const rows_ = await rows(
    database(),
    jsonRow,
    "SELECT * FROM control.audit_log WHERE id=$1",
    [z.uuid().parse(c.req.param("id"))],
  );
  return c.html(
    Layout({
      title: "Audit",
      subtitle: "",
      children: (
        <section class="card">
          <Table
            nextStep={{ label: "View functions", href: "/functions" }}
            rows={rows_}
          />
        </section>
      ),
    }),
  );
});
