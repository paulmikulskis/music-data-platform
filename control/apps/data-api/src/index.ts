import { serve } from "@hono/node-server";
import postgres from "postgres";
import { createApp } from "./app.js";
import { installRejectionHandler } from "./process-errors.js";
import { readerTypes } from "./read.js";
installRejectionHandler();
if (!process.env.MDP_READER_URL)
  throw new Error("MDP_READER_URL is required (reader_wh)");
if (!process.env.MDP_API_KEY_READER_URL)
  throw new Error("MDP_API_KEY_READER_URL is required (api_key_reader)");
const warehouse = postgres(process.env.MDP_READER_URL, {
  max: 5,
  connect_timeout: 5,
  types: readerTypes,
  connection: { statement_timeout: 5000 },
});
const keys = postgres(process.env.MDP_API_KEY_READER_URL, {
  max: 2,
  connect_timeout: 5,
});
serve({
  fetch: createApp(warehouse, keys).fetch,
  port: Number(process.env.PORT ?? 8091),
  hostname: process.env.HOST ?? "127.0.0.1",
});
console.log("Data API listening");
