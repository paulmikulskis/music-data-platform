import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
vi.mock("server-only", () => ({}));
import fixture from "./fixtures/links.json";
import lineageValue from "../lib/lineage.generated.json";
import { linksSchema, linksManifestSchema } from "../lib/links";
import manifest from "../../../../ops/showcase/links/links.json";
import { readLinks } from "../server/links";
import { artifactHash, readArtifacts } from "../server/artifacts";
import { stackAliases, datedFacts } from "../lib/stack-facts";
import { sensitiveStrings, stackSchema } from "../lib/stack";
import { lineageSchema } from "../lib/lineage";
import { consolePath } from "../server/console-policy";
import { viewerPath } from "../lib/proof-link";

const folders: string[] = [];

it("keeps public hashes and slugs while refusing credential-shaped preview text", () => {
  for (const value of [
    "0123456789abcdef".repeat(2) + "01234567",
    "0123456789abcdef".repeat(4),
    "open-the-source-and-check-the-longer-heading",
    "source-12345678-preview",
    "00000000-0000-0000-0000-000000000000",
  ]) {
    expect(sensitiveStrings({ secret: value })).toEqual([]);
  }
  for (const value of [
    String.fromCharCode(81, 55, 109, 90, 50, 97, 76, 57, 118, 66, 52, 99, 78, 56, 120, 82, 54, 116, 89, 51, 107, 80, 53, 119, 72, 49, 106, 68, 48, 115, 70, 113),
    "AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEf",
    "ghp_" + "a".repeat(40),
    "gho_" + "x".repeat(24),
    "github_pat_" + "a".repeat(40),
    "sk-" + "a".repeat(40),
    "sk_live_" + "a".repeat(40),
    "pk_live_" + "a".repeat(40),
    "xoxb-" + "x".repeat(24),
    "AKIA" + "A1".repeat(8),
    "FlyV1 " + "x".repeat(24),
    "eyJ" + "x".repeat(12) + "." + "y".repeat(16) + "." + "z".repeat(16),
    "-----BEGIN " + "OPENSSH PRIVATE " + "KEY-----",
    "abcdefghijklmno-123456789-pqrstuvwxyz",
    "0123456789ABCDEF".repeat(2) + "01234567",
    "a".repeat(40) + "==",
  ]) {
    expect(sensitiveStrings({ secret: value })).toEqual(["$.secret"]);
  }
});
afterEach(() => {
  for (const folder of folders.splice(0))
    rmSync(folder, { recursive: true, force: true });
});
it("checks opaque preview values only in a secret context", () => {
  const token = String.fromCharCode(81, 55, 109, 90, 50, 97, 76, 57, 118, 66, 52, 99, 78, 56, 120, 82, 54, 116, 89, 51, 107, 80, 53, 119, 72, 49, 106, 68, 48, 115, 70, 113);
  for (const value of [
    token,
    `secret\n${token}`,
    { playlist_id: token },
    { playlist_id: `pl.${"ab12".repeat(8)}` },
    { monkey: token, author: token },
  ]) {
    expect(sensitiveStrings(value)).toEqual([]);
  }
  for (const value of [
    `api_key=${token}`,
    `X-Api-Key: ${token}`,
    `A private value is ${token}.`,
    { secret: token },
    { apiKey: token },
    { credentials: { value: [token] } },
    "ghp_" + "x".repeat(36),
    "eyJ" + "x".repeat(12) + "." + "y".repeat(16) + "." + "z".repeat(16),
    "-----BEGIN " + "OPENSSH PRIVATE " + "KEY-----",
  ]) {
    expect(sensitiveStrings(value).length).toBeGreaterThan(0);
  }
});
function payload(value: unknown = { ...fixture, revision: "a".repeat(40) }) {
  const directory = mkdtempSync(join(tmpdir(), "sc-links-test-"));
  folders.push(directory);
  const lineage = lineageSchema.parse(lineageValue);
  const stack = stackSchema.parse({
    schema_version: 1,
    input_hashes: {},
    captured_at: fixture.collected_at,
    services: stackAliases.map((alias) => ({
      alias,
      kind: "Server",
      process_role: "Service",
      region: "Not checked",
      machine_count: null,
      cpu_total: null,
      memory_mb_total: null,
      storage_gb_total: null,
      encrypted: null,
      measured_at: null,
      public_link: null,
      status: { state: "not_checked", name: "Not checked", checked_at: null },
      facts: datedFacts[alias] ?? [],
    })),
  });
  for (const [name, data] of Object.entries({
    "lineage.generated.json": lineage,
    "stack.generated.json": stack,
    "links.generated.json": value,
    "artifacts.build.json": {
      schema_version: 1,
      revision: "a".repeat(40),
      lineage_hash: artifactHash(lineage),
      stack_hash: artifactHash(stack),
      links_hash: artifactHash(value),
      lineage_inputs: lineage.input_hashes,
      stack_inputs: stack.input_hashes,
      links_inputs: fixture.input_hashes,
    },
  }))
    writeFileSync(join(directory, name), JSON.stringify(data));
  return directory;
}

describe("link previews", () => {
  it("keeps the manifest and fixture strict and excludes held code", () => {
    const parsed = linksManifestSchema.parse(manifest);
    expect(parsed.links).toHaveLength(68);
    expect(parsed.links.find((link) => link.id === "team-5-ready")).toMatchObject({
      destination: {
        repo: "music-data-platform",
        path: "docs/DEVELOPING.md",
        anchor: "before-a-pr",
      },
      thumbnail: "doc",
      headings: ["before-a-pr"],
    });
    expect(linksSchema.parse(fixture).revision).toBeNull();
    expect(linksSchema.safeParse({ ...fixture, extra: true }).success).toBe(
      false,
    );
    expect(
      linksSchema.safeParse({ ...fixture, revision: "main" }).success,
    ).toBe(false);
    expect(fixture.entries.some((entry) => entry.highlights.length === 3)).toBe(
      true,
    );
    expect(fixture.entries.some((entry) => entry.id === "team-4-guide")).toBe(
      false,
    );
  });
  it("returns drawn text and a fallback for missing previews", () => {
    const reader = readLinks(payload());
    expect(reader.get("stack-code-warehouse")?.properNames).toHaveLength(3);
    expect(reader.get("stack-code-warehouse")?.frame).toContain(
      "Code at this page's build · rev aaaaaaa",
    );
    expect(reader.get("team-4-guide")?.frame).toContain(
      "Preview not recorded at this deploy",
    );
    expect(reader.get("unknown")).toBeNull();
  });
  it("falls back for one invalid entry and retains another", () => {
    const value = {
      ...fixture,
      revision: "a".repeat(40),
      entries: fixture.entries.map((entry) =>
        entry.id === "stack-code-warehouse"
          ? { ...entry, line_count: -1 }
          : entry,
      ),
    };
    const reader = readLinks(payload(value));
    expect(reader.get("stack-code-warehouse")?.available).toBe(false);
    expect(reader.get("stack-open-console")?.available).toBe(true);
  });
  it.each([
    { name: "internal host", text: "synthetic.internal" },
    { name: "IP address", text: "127.0.0.1" },
    { name: "secret name", text: "EXAMPLE_SECRET" },
    { name: "volume ID", text: "vol_abc123def456" },
    { name: "email", text: "synthetic-reader@example.invalid" },
    { name: "home path", text: "/home/synthetic-reader/project" },
    { name: "token", text: "ghp_" + "x".repeat(36) },
    { name: "private key", text: "-----BEGIN PRIVATE KEY-----" },
    { name: "price", text: "$123" },
  ])("keeps $name hard in preview text", ({ text }) => {
    const value = {
      ...fixture,
      revision: "a".repeat(40),
      entries: fixture.entries.map((entry) =>
        entry.id === "stack-code-warehouse"
          ? { ...entry, body: [text] }
          : entry,
      ),
    };
    const reader = readLinks(payload(value));
    expect(reader.get("stack-code-warehouse")?.available).toBe(false);
    expect(reader.get("stack-open-console")?.available).toBe(true);
  });
  it.each(["missing", "invalid", "hash mismatch"])(
    "keeps lineage and Stack readable with %s links",
    (failure) => {
      const directory = payload();
      const file = join(directory, "links.generated.json");
      if (failure === "missing") rmSync(file);
      else writeFileSync(file, failure === "invalid" ? "{" : "{}");
      expect(readLinks(directory).get("stack-code-warehouse")?.available).toBe(
        false,
      );
      expect(readArtifacts(directory).lineage.nodes.length).toBeGreaterThan(0);
      expect(readArtifacts(directory).stack.services).toHaveLength(
        stackAliases.length,
      );
    },
  );
  it("keeps unknown graphic parts on a reviewed page", () => {
    const reader = readLinks(payload());
    for (const variant of ["constructor", "__proto__", "missing"]) {
      expect(reader.get("team-desk-anchors", variant)?.destination).toEqual({
        route: "/team#screen",
        back: null,
      });
    }
  });
  it("omits code destinations when the revision is absent", () => {
    const directory = payload();
    rmSync(join(directory, "artifacts.build.json"));
    const result = readLinks(directory).get("stack-code-warehouse");
    expect(result?.destination).toBeNull();
    expect(result?.kind).toBe("none");
  });
  it("allows only reviewed Console routes and viewer return paths", () => {
    for (const entry of linksSchema.parse(fixture).entries) {
      const destination = entry.destination;
      if (!("route" in destination)) continue;
      if (destination.back !== null) {
        expect(consolePath(destination.route.split("?")[0] ?? "")).toBe(true);
        expect(viewerPath(destination.back)).toBe(destination.back);
      } else expect(viewerPath(destination.route)).toBe(destination.route);
    }
  });
  it("keeps artifacts and readers out of client modules", () => {
    const docker = readFileSync(
      new URL("../Dockerfile", import.meta.url),
      "utf8",
    );
    expect(docker).toContain("./apps/showcase/artifacts/links.generated.json");
    expect(docker).not.toContain("public/links.generated.json");
  });
});
