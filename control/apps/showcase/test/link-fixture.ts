import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import fixture from "./fixtures/links.json";
import { artifactHash } from "../server/artifacts";

export function linkFixture(
  value: unknown = { ...fixture, revision: "a".repeat(40) },
) {
  const directory = mkdtempSync(join(tmpdir(), "sc-r-links-b-test-"));
  writeFileSync(join(directory, "links.generated.json"), JSON.stringify(value));
  writeFileSync(
    join(directory, "artifacts.build.json"),
    JSON.stringify({
      schema_version: 1,
      revision: "a".repeat(40),
      lineage_hash: "b".repeat(64),
      stack_hash: "c".repeat(64),
      links_hash: artifactHash(value),
      lineage_inputs: {},
      stack_inputs: {},
      links_inputs: fixture.input_hashes,
    }),
  );
  return directory;
}
