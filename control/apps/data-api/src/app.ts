import { randomUUID } from "node:crypto";
import { requestCorrelation } from "./process-errors.js";
import { Hono } from "hono";
import { RPCHandler } from "@orpc/server/fetch";
import { OpenAPIHandler } from "@orpc/openapi/fetch";
import postgres from "postgres";
import type { DB } from "../../control-api/src/db.js";
import type { ReadContext } from "./read.js";
import { router } from "./generated/router.js";
export { router };
// Each procedure authenticates its own request, so refusals reach clients as typed DATA errors.
export function createApp(warehouse: postgres.Sql, keys: DB) {
  const app = new Hono<{ Variables: { context: ReadContext } }>();
  const rpc = new RPCHandler(router);
  const api = new OpenAPIHandler(router);
  app.get("/version", (c) => c.json({ git_sha: process.env.MDP_BUILD_SHA || null }));
  app.get("/health", (c) => c.json({ status: "ok" }));
  app.use("*", async (c, next) => {
    c.set("context", { request: c.req.raw, warehouse, keys });
    const correlation = randomUUID();
    await requestCorrelation.run(correlation, next);
    c.header("x-correlation-id", correlation);
  });
  app.use("/rpc/*", async (c, next) => {
    const r = await rpc.handle(c.req.raw, {
      prefix: "/rpc",
      context: c.get("context"),
    });
    return r.matched ? r.response : next();
  });
  app.use("/api/*", async (c, next) => {
    const r = await api.handle(c.req.raw, {
      prefix: "/api",
      context: c.get("context"),
    });
    return r.matched ? r.response : next();
  });
  app.onError(() => new Response(JSON.stringify({
    error_class: "data_unavailable",
    message: "Read failed; verify the mart build and session scope",
  }), { status: 503, headers: { "content-type": "application/json" } }));
  return app;
}
