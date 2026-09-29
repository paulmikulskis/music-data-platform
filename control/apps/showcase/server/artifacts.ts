import "server-only";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { lineageSchema } from "../lib/lineage";
import { buildEnvelopeSchema, stackSchema } from "../lib/stack";

// Runtime files are copied explicitly by the Dockerfile. They are never public static files.
function load(name: string, directory: string): unknown {
  return JSON.parse(readFileSync(join(directory, name), "utf8"));
}
function normalized(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalized);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, item]) => [key, normalized(item)]),
    );
  }
  return value;
}
export function artifactHash(value: unknown) {
  return createHash("sha256")
    .update(JSON.stringify(normalized(value)))
    .digest("hex");
}
// The deploy image keeps the artifacts beside the server. A local check may point elsewhere.
export function artifactDirectory() {
  return process.env.MDP_SHOWCASE_ARTIFACTS_DIR ?? join(process.cwd(), "artifacts");
}
export function readArtifacts(directory = artifactDirectory()) {
  const lineage = lineageSchema.parse(
    load("lineage.generated.json", directory),
  );
  const stack = stackSchema.parse(load("stack.generated.json", directory));
  const build = buildEnvelopeSchema.parse(
    load("artifacts.build.json", directory),
  );
  if (
    artifactHash(lineage) !== build.lineage_hash ||
    artifactHash(stack) !== build.stack_hash
  ) {
    throw new Error(
      "Artifact inputs differ. Rebuild the showcase image with its validated overlay.",
    );
  }
  return { lineage, stack, build };
}
