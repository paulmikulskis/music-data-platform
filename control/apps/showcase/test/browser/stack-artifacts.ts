import { forbidden, previewMarkers } from "./link-privacy";
import { stackSchema } from "../../lib/stack";
import linksFixture from "../fixtures/links.json";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
// The browser gate runs the app the way the image does: with a validated Stack artifact beside
// the server. The collector runs dated-only, as the deploy context does, so no deployment API
// is read. Returns the artifact directory, or null when the run asks for the dated fallback.
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, item]) => [key, canonical(item)]),
    );
  return value;
}
const hash = (value: unknown) =>
  createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
export function prepareStackArtifacts(repo: string, revision: string) {
  if (process.env.BROWSER_STACK_DATED === "1") return null;
  const directory = mkdtempSync(path.join(tmpdir(), "sc-stack-artifacts-"));
  const collected = spawnSync(
    "uv",
    [
      "run",
      "--project",
      "functions",
      "python",
      "ops/showcase/stack/collect.py",
      "--dated-only",
      "--output",
      path.join(directory, "stack.generated.json"),
    ],
    { cwd: repo, stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" },
  );
  if (collected.status !== 0)
    throw new Error(
      `Stack facts did not collect. Run ops/showcase/stack/collect.sh --dated-only.\n${collected.stderr}`,
    );
  const lineagePath = path.join(
    repo,
    "control/apps/showcase/lib/lineage.generated.json",
  );
  copyFileSync(lineagePath, path.join(directory, "lineage.generated.json"));
  const lineage: unknown = JSON.parse(readFileSync(lineagePath, "utf8"));
  const stack = stackSchema.parse(
    JSON.parse(
      readFileSync(path.join(directory, "stack.generated.json"), "utf8"),
    ),
  );
  // Match the deployed service shape, including the line that expands the avatar sheet.
  for (const service of stack.services) {
    service.machine_count =
      service.alias === "The clock"
        ? 3
        : 1;
    service.region = "New Jersey";
    service.cpu_total = 2 * service.machine_count;
    service.memory_mb_total = 2048 * service.machine_count;
    service.storage_gb_total =
      service.alias === "Warehouse"
        ? 80
        : service.alias === "MusicBrainz copy"
          ? 250
          : 0;
    service.encrypted = service.storage_gb_total > 0;
    service.measured_at = "2026-09-27T12:00:00Z";
  }
  writeFileSync(
    path.join(directory, "stack.generated.json"),
    JSON.stringify(stack),
  );
  const inputs = (value: unknown) =>
    value !== null &&
    typeof value === "object" &&
    "input_hashes" in value &&
    typeof value.input_hashes === "object"
      ? value.input_hashes
      : {};
  const links = {
    ...linksFixture,
    revision,
    collected_at: new Date().toISOString(),
    entries: linksFixture.entries.map((entry) => {
      if (entry.id === "stack-code-musicbrainz")
        return { ...entry, body: forbidden };
      if (entry.id === "stack-code-warehouse")
        return {
          ...entry,
          body: [previewMarkers[0], ...entry.body.slice(1)],
        };
      if (entry.id === "stack-code-clock")
        return {
          ...entry,
          body: [previewMarkers[1], ...entry.body.slice(1)],
          proper_names: [previewMarkers[2]],
        };
      if (entry.id === "more-served-doc")
        return {
          ...entry,
          proper_names: [
            previewMarkers[3] + " for reviewed example reading practice guide",
          ],
        };
      if (entry.id === "team-1-contributing")
        return { ...entry, body: ["Teamcard"] };
      if (entry.id === "sources-reader-code")
        return { ...entry, body: ["Readercard", ...entry.body.slice(1)] };
      if (entry.id === "viewer-node-code")
        return { ...entry, body: ["Peekcard", ...entry.body.slice(1)] };
      return entry;
    }),
  };
  writeFileSync(
    path.join(directory, "links.generated.json"),
    JSON.stringify(links),
  );
  writeFileSync(
    path.join(directory, "artifacts.build.json"),
    JSON.stringify(
      {
        schema_version: 1,
        revision,
        lineage_hash: hash(lineage),
        stack_hash: hash(stack),
        links_hash: hash(links),
        lineage_inputs: inputs(lineage),
        stack_inputs: inputs(stack),
        links_inputs: inputs(links),
      },
      null,
      2,
    ),
  );
  return directory;
}
