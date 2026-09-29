import { afterEach, expect, it, vi } from "vitest";
import { Hono } from "hono";
import type { Context } from "../src/router.js";
vi.mock("../src/db.js", async (original) => ({
  ...(await original<typeof import("../src/db.js")>()),
  database: () => ({ unsafe: vi.fn().mockResolvedValue([]) }),
}));
vi.mock("../src/workbench-inputs.js", async (original) => ({
  ...(await original<typeof import("../src/workbench-inputs.js")>()),
  readableInputs: vi.fn(async () => [
    { schema: "explore_staging", name: "stg_billboard__chart_entries" },
  ]),
}));
vi.mock("../src/router.js", async () => ({
  router: {
    workbench: (await import("../src/workbench-router.js")).workbenchRouter,
  },
}));
import { workbenchPages } from "../src/workbench-page.js";
import { readableInputs, chartExample } from "../src/workbench-inputs.js";
const sessionId = "00000000-0000-4000-8000-000000000001";
const runId = "00000000-0000-4000-8000-000000000002";
const context: Context = {
  identity: { actor: "test", admin: true, tenant_id: null, tenant_slug: null },
  db: { unsafe: vi.fn() },
};
const app = new Hono<{ Variables: { context: Context } }>();
app.use("*", async (c, next) => {
  c.set("context", context);
  await next();
});
app.route("/workbench", workbenchPages);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

function service(refuseImmediately = false) {
  vi.stubEnv("MDP_WORKBENCH_URL", "http://workbench.invalid");
  vi.stubEnv("MDP_SERVICE_TOKEN", "test-token");
  const queries: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init: RequestInit) => {
      const action = url.pathname.split("/").at(-1);
      if (action === "createSession")
        return Response.json({ sessionId, scratchSchema: "wb_test" });
      if (action === "query") {
        queries.push(JSON.parse(String(init.body)));
        if (refuseImmediately)
          return Response.json(
            {
              error_class: "workbench_permission_denied",
              message:
                "permission denied for view stg_billboard__chart_entries",
            },
            { status: 403 },
          );
        return Response.json({
          runId,
          status: "queued",
          progress: 0,
          error: null,
        });
      }
      if (action === "models")
        return Response.json({
          models: [],
          cycles: [],
          cycleDetails: [],
          sources: [],
        });
      if (action === "draft")
        return Response.json({
          model: "mart_chart_explore",
          sql: chartExample,
        });
      if (action === "history") return Response.json({ runs: [] });
      return Response.json({
        runId,
        status: "failed",
        progress: 1,
        error: {
          error_class: "workbench_permission_denied",
          message: "permission denied for view stg_billboard__chart_entries",
        },
      });
    }),
  );
  return queries;
}
function post(fields: Record<string, string>) {
  return app.request("http://localhost/workbench", {
    method: "POST",
    body: new URLSearchParams(fields),
  });
}
it("the first button starts a query against the safe copy", async () => {
  const queries = service();
  const response = await post({ action: "create", intent: "charts" });
  expect(response.status).toBe(303);
  expect(response.headers.get("location")).toContain(runId);
  expect(queries[0]).toMatchObject({ sql: chartExample });
});
it("a refusal names the safe copy and posts rewritten SQL to rerun", async () => {
  const queries = service();
  const sql = chartExample.replace("explore_staging.", "staging.");
  const response = await post({
    action: "status",
    sessionId,
    runId,
    model: "mart_chart_explore",
    operation: "query",
    sql,
  });
  const html = await response.text();
  expect(html).toContain(
    "Use the readable copy explore_staging.stg_billboard__chart_entries and rerun.",
  );
  const recoveryForm = html.match(
    /<form[^>]*>(?:(?!<\/form>)[\s\S])*Use safe copy and rerun<\/button><\/form>/,
  )?.[0];
  expect(recoveryForm).toBeTruthy();
  const fields: Record<string, string> = {};
  for (const match of recoveryForm!.matchAll(
    /name="([^"]+)" value="([^"]*)"/g,
  )) {
    fields[match[1]!] = match[2]!
      .replaceAll("&quot;", '"')
      .replaceAll("&#39;", "'")
      .replaceAll("&amp;", "&");
  }
  expect(fields.action).toBe("query");
  expect(fields.sql).toContain("explore_staging.stg_billboard__chart_entries");
  expect((await post(fields)).status).toBe(200);
  expect(queries.at(-1)).toMatchObject({ sql: fields.sql });
});
it("keeps the next step when a safe copy is unavailable", async () => {
  service();
  vi.mocked(readableInputs).mockResolvedValueOnce([]);
  const html = await (
    await post({
      action: "status",
      sessionId,
      runId,
      model: "mart_chart_explore",
      operation: "query",
      sql: "select * from staging.secret",
    })
  ).text();
  expect(html).not.toContain("Use safe copy and rerun</button>");
  expect(html).toContain("choose a permitted global input");
});

it("offers a safe copy for an immediate service refusal", async () => {
  service(true);
  const html = await (
    await post({
      action: "query",
      sessionId,
      model: "mart_chart_explore",
      sql: chartExample.replace("explore_staging.", "staging."),
    })
  ).text();
  expect(html).toContain("Use safe copy and rerun</button>");
  expect(html).toContain(
    "Use the readable copy explore_staging.stg_billboard__chart_entries and rerun.",
  );
});

it("preserves escaped query text through a fresh session without running it", async () => {
  const queries = service();
  const sql = "select '</textarea><script>sentinel</script>\\quote' as text";
  const href = `/workbench?${new URLSearchParams({ intent: "query", sql })}`;
  const landing = await (await app.request(href)).text();
  expect(landing).toContain("Start session with this query");
  expect(landing).toContain("&lt;/textarea&gt;&lt;script&gt;");
  expect(landing).not.toContain("<script>sentinel</script>");
  expect(queries).toEqual([]);
  const created = await post({ action: "create", intent: "query", sql });
  expect(created.status).toBe(303);
  const location = created.headers.get("location")!;
  expect(new URL(location, "http://localhost").searchParams.get("sql")).toBe(
    sql,
  );
  const existing = await (
    await app.request(location, {
      headers: { cookie: `mdp_workbench_session=${sessionId}` },
    })
  ).text();
  expect(existing).toContain("&lt;/textarea&gt;&lt;script&gt;");
  expect(queries).toEqual([]);
});
it("rejects decoded queries over four KB before creating a session", async () => {
  service();
  const sql = "界".repeat(1366);
  expect(
    (
      await app.request(
        `/workbench?${new URLSearchParams({ intent: "query", sql })}`,
      )
    ).status,
  ).toBe(400);
  expect((await post({ action: "create", intent: "query", sql })).status).toBe(
    400,
  );
  expect(fetch).not.toHaveBeenCalled();
});
