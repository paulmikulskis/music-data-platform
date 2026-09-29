import { execFileSync } from "node:child_process";
import { closeDialog } from "./dialog";
import assert from "node:assert/strict";
import path from "node:path";
import { stat, writeFile } from "node:fs/promises";
import { expect, type Page } from "@playwright/test";
import {
  syntheticNightResponse,
  syntheticSources,
} from "../synthetic-fixture";
import { sourceFamilies } from "../../lib/source-families";
import { density, overlay, densityPolicy } from "./density.mjs";

export async function productionWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
) {
  const size = `${width}x${width === 390 ? 844 : 900}`;
  const readings: Record<string, unknown> = {};
  const capture = async (name: string) => {
    const measurement = await page.evaluate(density, densityPolicy);
    readings[name] = measurement;
    assert.deepEqual(measurement.banned, []);
    assert.deepEqual(measurement.jargonWarnings, []);
    if (width === 390) {
      assert(
        measurement.words <= 40 + measurement.detailWords,
        `${name}: ${measurement.words} words; ${measurement.text}`,
      );
      assert(
        measurement.numbers <= 3 + measurement.detailNumbers,
        `${name}: ${measurement.numbers} numbers`,
      );
    }
    const file = path.join(out, `production-${name}-${size}.jpg`);
    await page.screenshot({
      path: file,
      type: "jpeg",
      quality: 65,
      fullPage: true,
    });
    assert(
      (await stat(file)).size < 300_000,
      "Screenshot exceeds 300 KB. Lower its JPEG quality.",
    );
  };
  const fits = async () => {
    const bounds = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    assert.equal(
      bounds.scroll,
      bounds.client,
      "The page must fit its viewport. Inspect the timeline width.",
    );
    return bounds;
  };
  await page.route("**/s/night?*", (route) =>
    route.fulfill({ json: syntheticNightResponse }),
  );
  await page.goto(origin + "/");
  await page.locator(".night-tick").first().waitFor();
  await page.waitForTimeout(900);
  await page.evaluate(() => window.scrollTo(0, 0));
  readings.homeBounds = await fits();
  const readerLines = await page
    .locator(".night-summary > span")
    .allTextContents();
  const readerNames = readerLines.map((line) => line.split(" · ")[0]);
  assert.equal(
    new Set(readerNames).size,
    readerNames.length,
    "Each reader appears once. Inspect the Home night summary.",
  );
  readings.readerLines = readerLines;
  const brands = await page
    .locator('.home-sources [data-stage="Source"] .provider-mark')
    .evaluateAll((links) =>
      links.map((link) => link.getAttribute("aria-label")),
    );
  assert.deepEqual(brands.slice(0, 4), [
    "Open Spotify",
    "Open Apple Music",
    "Open Shazam",
    "Open Billboard",
  ]);
  assert(brands.includes("Open MusicBrainz"));
  for (const mark of await page
    .locator(".home-sources .provider-mark img")
    .all()) {
    assert(
      await mark.evaluate(async (element) => {
        if (!(element instanceof HTMLImageElement)) return false;
        await element.decode();
        const canvas = document.createElement("canvas");
        canvas.width = 64;
        canvas.height = 64;
        const context = canvas.getContext("2d");
        if (!context) return false;
        context.drawImage(element, 0, 0, 64, 64);
        return context
          .getImageData(0, 0, 64, 64)
          .data.some((value, index) => index % 4 === 3 && value > 0);
      }),
      "A provider mark is blank. Replace it with a visible provider asset.",
    );
  }
  if (width === 1440) {
    assert(
      await page.locator(".source-strip").evaluate((strip) => {
        const bounds = strip.getBoundingClientRect();
        return Array.from(strip.querySelectorAll(".provider-mark")).every(
          (mark) => {
            const box = mark.getBoundingClientRect();
            return (
              box.left >= bounds.left &&
              box.right <= bounds.right &&
              box.bottom <= bounds.bottom
            );
          },
        );
      }),
      "Every live provider fits on desktop. Wrap the source tiles.",
    );
  }

  assert.equal(
    await page
      .locator(".source-off a")
      .filter({ hasText: "Bandcamp best-sellers" })
      .count(),
    1,
  );
  await capture("home");
  const homeCluster = page.locator(".home-night .night-tick.clustered").first();
  const clusterCount = await homeCluster.getAttribute("data-count");
  await homeCluster.click();
  await page.getByRole("dialog").waitFor();
  await page.locator(".night-cluster-controls").waitFor();
  assert.match(
    await page.locator(".night-cluster-controls").innerText(),
    new RegExp(`of ${clusterCount} moments`),
  );
  const firstDetails = await page.locator(".night-detail").innerText();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  assert.notEqual(
    await page.locator(".night-detail").innerText(),
    firstDetails,
  );
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  assert.equal(await page.locator(".night-detail").innerText(), firstDetails);
  const sheet = await page.evaluate(overlay, {
    kind: "sheet",
    policy: densityPolicy,
  });
  readings.cluster = sheet;
  assert(sheet.words <= 80, `Cluster: ${sheet.words} words; ${sheet.text}`);
  assert.deepEqual(sheet.banned, []);
  await fits();
  await closeDialog(page);
  await page.goto(origin + "/sources");
  await page.locator(".reviewed-source").first().waitFor();
  await page.waitForTimeout(400);
  const keys = await page
    .locator("[data-source-family]")
    .evaluateAll((cards) =>
      cards.map((card) => card.getAttribute("data-source-family")),
    );
  const families = sourceFamilies(syntheticSources.sources);
  assert.deepEqual(
    keys,
    families.filter((family) => family.enabled).map((family) => family.key),
  );
  for (const key of [
    "sp_playlist",
    "am_playlist",
    "sz_chart",
    "billboard_hot100",
  ])
    assert(await page.locator(`[data-source-family="${key}"]`).isVisible());
  readings.sourceBounds = await fits();
  await capture("sources");
  await page.getByRole("button", { name: "Show all sources" }).click();
  assert.equal(
    await page.locator("[data-source-family]").count(),
    families.length,
  );
  assert.equal(
    await page.locator('[data-source-family="bc_daily_list"]').count(),
    1,
  );
  assert.equal(
    await page.locator('[data-source-family="sc_curator_playlists"]').count(),
    1,
  );
  assert.equal(
    await page.locator('[data-source-family="cycle_close"]').count(),
    0,
  );
  await page
    .locator('[data-source-family="sp_playlist"]')
    .getByRole("link", { name: "Trace" })
    .click();
  await page.locator(".trace-node").first().waitFor();
  assert.equal(
    new URL(page.url()).searchParams.get("trace"),
    "source.sp_playlist",
  );
  await closeDialog(page, "Close viewer");
  await page.unroute("**/s/night?*");
  // The counter component has no public route after the navigation redirects.
  // Render its real markup with the app stylesheet, including an enabled hub reader.
  const fixture = execFileSync(
    "pnpm",
    [
      "exec",
      "tsx",
      "--tsconfig",
      "tsconfig.json",
      "test/browser/counter-fixture.tsx",
    ],
    { encoding: "utf8" },
  );
  await page
    .locator("main")
    .first()
    .evaluate((node, html) => {
      node.innerHTML = html;
    }, fixture);
  const chartCounter = page.locator('[data-counter="charts"]');
  await chartCounter.scrollIntoViewIfNeeded();
  await expect(chartCounter).not.toContainText("not measured yet");
  await expect(page.locator('[data-counter="playlists"]')).not.toContainText(
    "not measured yet",
  );
  await expect(page.getByText(/Not counted: Billboard Hot 100/)).toBeVisible();
  readings.chartBounds = await fits();
  await page.screenshot({
    path: path.join(out, `production-chart-count-${size}.jpg`),
    type: "jpeg",
    quality: 65,
  });
  let requests = 0;
  const saved = structuredClone(syntheticNightResponse);
  saved.value.ready_state = "busy";
  saved.value.ready_saved_at = saved.savedAt;
  saved.value.ready = [
    {
      relation: "marts.mart_top_movers_current",
      cycle_id: "00000000-0000-4000-8000-000000000041",
      close_no: "41",
      built_at: saved.value.window.since,
      build_key: "saved-stamp-fixture",
    },
  ];
  await page.route("**/s/night?*", (route) => {
    requests++;
    return route.fulfill({ json: saved });
  });
  await page.goto(origin + "/");
  await expect(page.locator(".home-night .night-tick.ready")).not.toHaveCount(
    0,
  );
  assert.equal(requests, 1);
  assert.equal(await page.locator(".night-ready-note").count(), 0);
  await expect(page.locator(".night-ready-note")).toContainText(
    "Updates as of",
    { timeout: 40000 },
  );
  assert.equal(requests, 2);
  assert.equal(
    await page
      .locator(".home-night")
      .getByRole("button", { name: "Retry", exact: true })
      .count(),
    0,
  );
  readings.savedStampBounds = await fits();
  await page
    .locator(".home-night")
    .getByRole("button", { name: "Night details" })
    .click();
  await expect(page.getByRole("dialog")).not.toContainText("Cached · as of");
  await expect(
    page
      .getByRole("dialog")
      .getByRole("button", { name: "Retry", exact: true })
      .first(),
  ).toBeVisible();
  await closeDialog(page);

  // An empty saved read is unavailable, even when it has a successful read time.
  saved.value.ready = [];
  saved.value.ready_state = "unavailable";
  saved.value.ready_saved_at = null;
  await page.goto(origin + "/");
  await expect(page.locator(".home-night .night-tick").first()).toBeVisible();
  await expect(page.locator(".home-night .night-tick.ready")).toHaveCount(0);
  await expect(page.locator(".night-ready-note")).toContainText(
    "Update times unavailable. Open night details.",
    { timeout: 40000 },
  );
  await expect(page.locator(".home-night")).not.toContainText("Updates as of");
  await expect(page.locator(".home-night .night-tick.ready")).toHaveCount(0);
  readings.emptyStampBounds = await fits();
  await page.unroute("**/s/night?*");

  await writeFile(
    path.join(out, `production-density-${size}.json`),
    JSON.stringify(readings, null, 2) + "\n",
  );
}
