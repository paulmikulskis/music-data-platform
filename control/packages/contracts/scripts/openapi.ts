import { OpenAPIGenerator } from "@orpc/openapi";
import { ZodToJsonSchemaConverter } from "@orpc/zod/zod4";
import { writeFileSync } from "node:fs";
import { router } from "../../../apps/control-api/src/router.js";
import { router as dataRouter } from "../../../apps/data-api/src/generated/router.js";

for (const [name, title, implementation] of [
  ["control-api", "MDP Control API", router],
  ["data-api", "MDP Data API", dataRouter],
] as const) {
  const doc = await new OpenAPIGenerator({
    schemaConverters: [new ZodToJsonSchemaConverter()],
  }).generate(implementation, {
    info: { title, version: "1.0.0" },
    servers: [{ url: "/api" }],
  });
  writeFileSync(
    new URL(`../openapi/${name}.json`, import.meta.url),
    JSON.stringify(doc, null, 2) + "\n",
  );
  console.log(`Emitted ${name}.json: ${Object.keys(doc.paths ?? {}).length} paths`);
}
