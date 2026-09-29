import {
  linksSchema,
} from "../../control/apps/showcase/lib/links";
import { consolePath } from "../../control/apps/showcase/server/console-policy";
import { viewerPath } from "../../control/apps/showcase/lib/proof-link";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { lineageSchema } from "../../control/apps/showcase/lib/lineage";
import {
  stackSchema,
  buildEnvelopeSchema,
} from "../../control/apps/showcase/lib/stack";

const directory = new URL("../../control/apps/showcase/lib/", import.meta.url);
function read(name: string): unknown {
  return JSON.parse(readFileSync(new URL(name, directory), "utf8"));
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, item]) => [key, canonical(item)]),
    );
  }
  return value;
}
function hash(value: unknown) {
  return createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
}
function recent(value: string) {
  const age = Date.now() - Date.parse(value);
  if (age < -300000 || age > 7 * 86400000)
    throw new Error(
      "Artifact date is stale. Collect and validate a new overlay.",
    );
}
const lineage = lineageSchema.parse(read("lineage.generated.json"));
const stack = stackSchema.parse(read("stack.generated.json"));
const build = buildEnvelopeSchema.parse(read("artifacts.build.json"));
recent(stack.captured_at);
const links = linksSchema.parse(read("links.generated.json"));
recent(links.collected_at);
for (const entry of links.entries) {
  const destination = entry.destination;
  if ("route" in destination) {
    const path = destination.route.split("?", 1)[0] ?? "";
    if (
      (destination.back !== null &&
        (!consolePath(path) || !viewerPath(destination.back))) ||
      (destination.back === null && !viewerPath(destination.route))
    ) {
      throw new Error(`${entry.id} links_route`);
    }
  }
}
if (
  build.revision !== process.env.MDP_DEPLOY_REVISION ||
  hash(lineage) !== build.lineage_hash ||
  hash(stack) !== build.stack_hash ||
  hash(links) !== build.links_hash ||
  links.revision !== build.revision ||
  hash(links.input_hashes) !== hash(build.links_inputs) ||
  hash(build.lineage_inputs) !== hash(lineage.input_hashes) ||
  hash(build.stack_inputs) !== hash(stack.input_hashes)
) {
  throw new Error(
    "Artifact inputs differ. Build from the validated deploy context.",
  );
}
console.log("Artifact schemas and hashes match. Build the showcase image.");
