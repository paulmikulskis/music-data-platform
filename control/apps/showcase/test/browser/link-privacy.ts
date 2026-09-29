import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import type { Page } from "@playwright/test";

export const previewMarkers = [
  "Foldercard",
  "Clockcard",
  "clock_fixture_probe.sh",
  "Doccard reference",
  "Teamcard",
  "Readercard",
  "Peekcard",
];
// Synthetic values exercise the same classes as the collector and server-reader checks.
export const forbidden = [
  "synthetic-client-zebra",
  "synthetic-person-otter",
  "private.internal",
  "private.flycast",
  "10.20.30.40",
  "fdaa:0:1234:a7b::2",
  "e784e9d4b12345",
  "vol_abc123def456",
  "pgdata",
  "registry.fly.io/synthetic:deployment",
  "sk_" + "live_" + "abcdefghijklmnop",
  "EXAMPLE_SECRET",
  "Bearer abcdefghijklmnopqrstuvwxyz",
  "api_key=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "postgresql://fixture:fixture@host/db",
  "$123",
  "raw.githubusercontent.com",
  "token=short",
  "postgresql://fixture:fixture@127.0.0.1/db",
  "postgresql://user:password@host/db",
  "synthetic-reader@example.invalid",
  "token=synthetic-credential",
  "postgresql://fixture:synthetic-password@database.invalid/fixture",
  "mysql://fixture:synthetic-password@database.invalid/fixture",
  "api_key=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "api_key=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxdata",
  "api_key=Ab12+/Ab12+/Ab12+/Ab12+/Ab12+/Ab12+/Ab12+/Ab12+/==",
  "api_key=" + "Q7mZ2aL9vB4cN8xR" + "6tY3kP5wH1jD0sFq",
  "api_key=AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEf",
  "gho_xxxxxxxxxxxxxxxxxxxxxxxx",
  "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "pk_live_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "xoxb-xxxxxxxxxxxxxxxxxxxxxxxx",
  "AKIAA1A1A1A1A1A1A1A1",
  "FlyV1 xxxxxxxxxxxxxxxxxxxxxxxx",
  "sk_" + "live_" + "abcdefghijklmnop",
  "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "github_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "Bearer xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "eyJxxxxxxxxxxxx.yyyyyyyyyyyyyyyy.zzzzzzzzzzzzzzzz",
  "-----BEGIN " + "OPENSSH PRIVATE " + "KEY-----",
  "$123",
  "\u20ac12.34",
  "19 USD",
  "GBP 20",
  "12 dollars",
  "\u20b9123",
  "123 INR",
  "/home/synthetic-reader/project",
  "./project",
  "fly.io/apps",
  "fly-metrics.net",
  "secrets.example.invalid",
  "dashboard.clerk.com",
  "app.inngest.com",
];
export function assertSafeLinks(text: string) {
  for (const sentinel of forbidden)
    assert(!text.includes(sentinel), "links privacy_refused");
}
export async function privateLinkChunks() {
  let count = 0;
  async function scan(directory: string) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) await scan(file);
      else if (/\.(js|json|css|map)$/.test(entry.name)) {
        const text = await readFile(file, "utf8");
        for (const marker of previewMarkers)
          assert(!text.includes(marker), "links static_refused");
        count++;
      }
    }
  }
  await scan(path.resolve(".next/static"));
  assert(count > 0, "links static_missing");
}
export async function linkResponsePrivacy(
  page: Page,
  origin: string,
  dated: boolean,
) {
  for (const route of ["/stack", "/stack?_rsc=links", "/s/apps", "/s/stack"]) {
    const response = await page.evaluate(async (route) => {
      const result = await fetch(route, {
        headers: route.includes("_rsc") ? { RSC: "1" } : {},
      });
      return {
        status: result.status,
        text: await result.text(),
        headers: Object.fromEntries(result.headers),
      };
    }, route);
    assert.equal(response.status, 200, "links response_failed");
    if (route.includes("_rsc"))
      assert(
        response.headers["content-type"]?.includes("text/x-component"),
        "links rsc_type_refused",
      );
    assertSafeLinks(response.text);
    assertSafeLinks(JSON.stringify(response.headers));
    if (!dated && route.startsWith("/stack"))
      assert(
        response.text.includes("Foldercard"),
        `links ${route.includes("_rsc") ? "rsc" : "html"}_fixture_missing`,
      );
  }
  const context = await page.context().browser()!.newContext();
  try {
    for (const route of [
      "/s/apps",
      "/s/stack",
      "/s/open?to=/ops&back=/stack",
      "/_next/static/links.generated.json",
      "/brand/links.generated.json",
      "/fonts/links.generated.json",
    ]) {
      const response = await context.request.get(origin + route, {
        maxRedirects: 0,
      });
      assert(response.status() >= 300, "links anonymous_served");
      const text = await response.text();
      assertSafeLinks(text);
      for (const marker of previewMarkers)
        assert(!text.includes(marker), "links anonymous_preview");
    }
  } finally {
    await context.close();
  }
}
