import { Server } from "node:http";
import { serve } from "@hono/node-server";
import { app } from "./app.js";
import { startEmailWorker } from "./email.js";
import { startTargetProbeWorker } from "./target-probe-worker.js";
const server = serve({
  fetch: app.fetch,
  port: Number(process.env.PORT ?? 8090),
  hostname: process.env.HOST ?? "127.0.0.1",
});
// Keep polling connections usable across slow form redirects and preview renders.
if (server instanceof Server) {
  server.keepAliveTimeout = 65_000;
  server.headersTimeout = 66_000;
}
startEmailWorker();
startTargetProbeWorker();
console.log("Control API listening");
