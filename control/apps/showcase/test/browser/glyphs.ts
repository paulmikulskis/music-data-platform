import { density, densityPolicy } from "./density.mjs";
import { readFileSync } from "node:fs";
import { platformNight } from "@mdp/contracts/platform";
import { marks } from "@mdp/contracts/marks";
import assert from "node:assert/strict";
import path from "node:path";
import { stat, writeFile } from "node:fs/promises";
import { expect, type Locator, type Page } from "@playwright/test";
import { brandName, brandTint, type Brand } from "../../lib/brands";
import { keys } from "./fixtures";
import { closeDialog } from "./dialog";

export async function glyphsWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
) {
  const size = `${width}x${width === 390 ? 844 : 900}`;
  const readings: Record<string, unknown> = {};
  const fits = async () => {
    const bounds = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    assert.equal(
      bounds.scroll,
      bounds.client,
      "Page overflows. Inspect the badge row.",
    );
    return bounds;
  };
  const check = async (
    link: Locator,
    brand: Brand,
    ring: boolean,
    neutral?: Locator,
  ) => {
    await link.scrollIntoViewIfNeeded();
    if (neutral) await neutral.hover();
    else await page.mouse.move(0, 0);
    await link.evaluate((element) => {
      if (element instanceof HTMLElement) element.blur();
    });
    const glyph = link.locator("svg.glyph");
    await expect(glyph).toHaveCSS("color", "rgb(237, 244, 246)");
    await expect(glyph.locator("path")).toHaveCSS("fill", "rgb(237, 244, 246)");
    assert.equal(await link.locator("img").count(), 0);
    await expect.poll(async () => (await glyph.boundingBox())?.width).toBe(14);
    const box = await link.boundingBox();
    const icon = await glyph.boundingBox();
    assert(box && icon);
    assert.equal(icon.width, 14);
    assert.equal(icon.height, 14);
    assert(icon.x >= box.x && icon.y >= box.y);
    assert(icon.x + icon.width <= box.x + box.width);
    assert(icon.y + icon.height <= box.y + box.height);
    if (ring) {
      assert.equal(box.width, 22);
      assert.equal(box.height, 22);
      await expect(link).toHaveCSS("border-top-width", "1px");
      await expect(link).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
      await expect(link).toHaveCSS("box-shadow", "none");
    }
    const tint = brandTint(brand);
    const expected = `rgb(${[1, 3, 5].map((i) => parseInt(tint.slice(i, i + 2), 16)).join(", ")})`;
    await link.hover();
    await expect(glyph).toHaveCSS("color", expected);
    await expect(glyph.locator("path")).toHaveCSS("fill", expected);
    if (ring) await expect(link).toHaveCSS("border-top-color", expected);
    if (neutral) await neutral.hover();
    else await page.mouse.move(0, 0);
    await page.keyboard.press("Shift");
    await link.focus();
    await expect(link).toBeFocused();
    await expect(glyph).toHaveCSS("color", expected);
    if (ring) await expect(link).toHaveCSS("border-top-color", expected);
    await link.evaluate((element) => {
      if (element instanceof HTMLElement) element.blur();
    });
    return {
      rest: "rgb(237, 244, 246)",
      hover: expected,
      focus: expected,
      box,
      icon,
    };
  };
  if (process.env.MDP_SHOWCASE_BROWSER_NIGHT) {
    const captured = platformNight.parse(
      JSON.parse(readFileSync(process.env.MDP_SHOWCASE_BROWSER_NIGHT, "utf8")),
    );
    await page.route("**/s/night?*", (route) =>
      route.fulfill({
        json: {
          state: "live",
          savedAt: captured.window.queried_at,
          value: {
            ...captured,
            ready: [],
            ready_saved_at: null,
            ready_state: "unavailable",
          },
        },
      }),
    );
  }
  await page.goto(origin + "/");
  await page.locator(".cover-sources .source-badge").first().waitFor();
  for (const brand of ["spotify", "apple_music", "shazam"] as const) {
    const badge = page
      .locator(".cover-sources")
      .getByRole("link", { name: `Open ${brandName(brand)}`, exact: true });
    // The standard mover has Spotify and Shazam evidence; the dedicated pass adds an Apple observation.
    if (
      brand === "apple_music" &&
      !process.env.MDP_SHOWCASE_BROWSER_GLYPHS_ONLY
    )
      continue;
    readings[`home-${brand}`] = await check(badge, brand, true);
  }
  const billboard = page
    .locator(".home-sources .provider-mark")
    .getByRole("img", { name: marks.billboard.label, exact: true });
  if (await billboard.count()) {
    // Full logos follow the registry's plate; round badges stay transparent.
    const background =
      marks.billboard.plate === "light"
        ? "rgb(255, 255, 255)"
        : marks.billboard.plate === "dark"
          ? "rgb(8, 9, 13)"
          : "rgba(0, 0, 0, 0)";
    await expect(billboard).toHaveCSS("background-color", background);
  }
  readings.homeBounds = await fits();
  await page.mouse.move(0, 0);
  await page.waitForTimeout(250);
  const homeDensity = await page.evaluate(density, densityPolicy);
  readings.homeDensity = homeDensity;
  if (width === 390) {
    assert(
      homeDensity.words <= 40,
      "Home exceeds 40 words. Inspect the phone screenshot.",
    );
    assert(
      homeDensity.numbers <= 3,
      "Home exceeds three numbers. Inspect the phone screenshot.",
    );
  }
  for (const state of ["rest", "spotify-hover"] as const) {
    if (state === "spotify-hover") {
      await page
        .locator('.cover-sources .source-badge[aria-label="Open Spotify"]')
        .hover();
      await page.waitForTimeout(250);
    }
    const file = path.join(out, `hero-glyphs-${state}-${size}.jpg`);
    await page.screenshot({ path: file, type: "jpeg", quality: 65 });
    assert(
      (await stat(file)).size < 300_000,
      "Screenshot exceeds 300 KB. Lower JPEG quality.",
    );
  }
  await page.goto(origin + `/s/song/${keys[0]}`);
  await page.locator(".found-on .glyph-link").first().waitFor();
  for (const brand of ["spotify", "apple_music", "deezer"] as const) {
    readings[`song-${brand}`] = await check(
      page
        .locator(".found-on")
        .getByRole("link", { name: `Open ${brandName(brand)}`, exact: true }),
      brand,
      false,
    );
  }
  await page
    .getByRole("button", { name: "Open Shazam cities mark", exact: true })
    .click();
  const metric = page.getByRole("dialog").locator(".metric-button");
  await metric.hover();
  const shazam = page.locator('[data-popover] .source-badge[title="Shazam"]');
  await shazam.waitFor();
  // Keep the parent hover card open while checking the link's resting state.
  readings["song-shazam"] = await check(
    shazam,
    "shazam",
    true,
    page.locator("[data-popover] .how-card p").first(),
  );
  readings.songBounds = await fits();
  await closeDialog(page);
  if (process.env.MDP_SHOWCASE_BROWSER_NIGHT)
    await page.unroute("**/s/night?*");
  await writeFile(
    path.join(out, `glyphs-${size}.json`),
    JSON.stringify(readings, null, 2) + "\n",
  );
}
