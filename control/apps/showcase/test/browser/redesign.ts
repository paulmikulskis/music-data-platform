import { teamLinksWalk } from "./team-links";
import assert from "node:assert/strict";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import { expect, type Page } from "@playwright/test";
import { density, overlay, densityPolicy } from "./density.mjs";
import { nightResponse } from "../../lib/night";
import { keys } from "./fixtures";
import { stackWalk } from "./stack";
import { closeDialog } from "./dialog";
import { withServerTime } from "./clock";
export async function redesignWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
  noData = false,
) {
  const readings: Record<string, unknown> = {};
  const size = `${width}x${width === 390 ? 844 : 900}`;
  const capture = async (name: string, sheet = false) => {
    await page.waitForTimeout(400);
    const result = sheet
      ? await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })
      : await page.evaluate(density, densityPolicy);
    readings[name] = result;
    if ("headlines" in result) {
      assert(
        result.headlines.every((words) => words <= 8),
        `${name}: long headline`,
      );
      assert.deepEqual(result.headlinePatterns, [], name);
      assert(
        result.cards.every((actions) => actions <= 1),
        `${name}: competing primary actions`,
      );
      assert.equal(result.tables, 0, `${name}: viewer table`);
    }
    if ("clockViolations" in result)
      assert.deepEqual(result.clockViolations, [], name);
    await page.screenshot({
      path: path.join(out, `${name}-${size}.jpg`),
      type: "jpeg",
      quality: 65,
    });
    assert(!result.banned.length, `${name}: ${result.banned}; ${result.text}`);
    assert(
      !result.jargonWarnings?.length,
      `${name}: ${result.jargonWarnings}; ${result.text}`,
    );
    if (sheet)
      assert(
        result.words <= 80 + result.detailWords,
        `${name}: ${result.words} words; ${result.text}`,
      );
    else if (width === 390) {
      assert(
        result.words <= 40 + result.detailWords,
        `${name}: ${result.words} words; ${result.text}`,
      );
      assert(
        result.numbers <= 3 + result.detailNumbers,
        `${name}: ${result.numbers} numbers; ${result.text}`,
      );
    }
  };
  const visit = async (route: string) => {
    assert.equal(
      await page.locator("dialog[open]").count(),
      0,
      "Close the current sheet before opening another page. Open the browser screenshot.",
    );
    const response = await page.goto(origin + route);
    assert.equal(response?.status(), 200, route);
    await page.locator('.top-tabs [aria-current="page"]').waitFor();
    assert.equal(
      await page.locator('.top-tabs [aria-current="page"]').count(),
      1,
    );
    await page.waitForTimeout(900);
  };
  await visit("/");
  await page.locator(".night-tick").first().waitFor();
  if (noData) {
    await page
      .getByRole("heading", { name: "the music is out of reach." })
      .waitFor();
    await capture("home-music-unavailable");
    await visit("/sources");
    await page
      .getByText("Live states unavailable.", { exact: false })
      .waitFor();
    await capture("sources-unavailable");
    await page.getByRole("link", { name: "Source details" }).first().click();
    await page.locator(".trace-node").first().waitFor();
    await capture("source-state-unavailable", true);
    await closeDialog(page, "Close viewer");
    await writeFile(
      path.join(out, `music-unavailable-density-${size}.json`),
      JSON.stringify(readings, null, 2),
    );
    return;
  }
  assert(
    (await page
      .locator('.home-sources [data-stage="Source"] .provider-mark')
      .count()) > 0,
    "Home shows observed provider marks.",
  );
  await capture("home");
  if (width === 1440) {
    await page
      .locator('.home-sources [data-stage="Source"] .provider-mark')
      .first()
      .hover();
    await page.locator("[data-popover]").waitFor();
    try {
      await expect
        .poll(() =>
          page.evaluate(overlay, { kind: "hover", policy: densityPolicy }),
        )
        .toMatchObject({ found: true });
    } catch (error) {
      console.log(
        await page.locator("[data-popover]").evaluateAll((cards) =>
          cards.map((card) => ({
            bounds: card.getBoundingClientRect().toJSON(),
            opacity: getComputedStyle(card).opacity,
            style: card.getAttribute("style"),
          })),
        ),
      );
      throw error;
    }
    const sourceHover = await page.evaluate(overlay, {
      kind: "hover",
      policy: densityPolicy,
    });
    readings["source-hover"] = sourceHover;
    assert(
      sourceHover.found && sourceHover.words <= 30 + sourceHover.detailWords,
    );
    assert(sourceHover.numbers <= 4 + sourceHover.detailNumbers);
    assert.deepEqual(sourceHover.banned, []);
    assert.deepEqual(sourceHover.jargonWarnings, []);
    await page.mouse.move(0, 0);
    await page.locator("[data-popover]").waitFor({ state: "hidden" });
  }
  await page.getByRole("button", { name: "See why", exact: true }).click();
  await page.getByRole("link", { name: "Trace this fact" }).first().click();
  await page.locator(".trace-node").first().waitFor();
  await closeDialog(page, "Close viewer");
  await page
    .getByRole("dialog", { name: "Data source viewer" })
    .waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Night details" }).click();
  const selectNightMoment = async (name: RegExp) => {
    const dialog = page.locator("dialog[open]");
    const ticks = dialog.locator(".night-lane").first().locator(".night-tick");
    await ticks.first().waitFor();
    // The same moments cluster differently in a short evening window and a full night.
    for (const tick of await ticks.all()) {
      await tick.click();
      const count = Number(await tick.getAttribute("data-count"));
      for (let index = 0; index < count; index++) {
        if (name.test(await dialog.locator(".night-detail").innerText()))
          return;
        if (index + 1 < count)
          await dialog
            .getByRole("button", { name: "Next", exact: true })
            .click();
      }
    }
    assert.fail("Reading missing from its cluster. Open the night fixture.");
  };
  await selectNightMoment(/Spotify[\s\S]*Reading failed/);
  await capture("night-zero-output", true);
  assert.match(
    await page.getByRole("dialog").innerText(),
    /0 of 48 playlists read · Start not recorded/,
  );
  await selectNightMoment(/Apple[\s\S]*Only part delivered/);
  await capture("night-partial", true);
  assert.match(
    await page.getByRole("dialog").innerText(),
    /31 of 48 playlists read · Started on its own/,
  );
  await page
    .getByRole("dialog")
    .getByRole("link", { name: "See details" })
    .click();
  await page.locator("[data-showcase-navigation]").waitFor();
  await page
    .getByRole("link", { name: "Back to showcase", exact: false })
    .click();
  await page
    .getByRole("dialog")
    .getByText(/31 of 48 playlists read/)
    .waitFor();
  assert.match(
    await page.getByRole("dialog").innerText(),
    /31 of 48 playlists read/,
  );
  await closeDialog(page);
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  for (const [name, route] of [
    ["sources", "/sources"],
    ["stack", "/stack"],
    ["team", "/team"],
    ["songs", "/songs?view=rising"],
    ["places", "/songs?view=places"],
    ["waking", "/songs?view=waking"],
    ["picks", "/songs?view=picks"],
    ["friday", "/songs?view=friday"],
  ]) {
    await visit(route);
    await capture(name);
  }
  await visit("/sources");
  if (width === 390) {
    const firstSource = page.locator(".reviewed-source").first();
    const name = await firstSource
      .locator('[data-stage="Source"] .provider-mark')
      .boundingBox();
    const state = await firstSource.locator("p").boundingBox();
    const details = await firstSource
      .getByRole("link", { name: "Trace" })
      .boundingBox();
    const card = await firstSource.boundingBox();
    assert(name && state && details && card);
    assert(name.height >= 44 && details.height >= 44 && details.width >= 44);
    assert(state.y > name.y, "Reading state follows the function explanation.");
    assert(details.y - state.y - state.height <= 8);
    assert(
      card.height <= 640,
      "Keep each explained source readable on a phone.",
    );
    await page.getByRole("button", { name: "Show all sources" }).click();
    assert(
      await page.locator(".reviewed-source").last().isVisible(),
      "Show all sources reveals the remaining readers.",
    );
  }
  assert.equal(
    await page
      .locator(
        '[data-source-family="sp_playlist"] [data-stage="Source"] .provider-mark',
      )
      .getAttribute("aria-label"),
    "Open Spotify",
  );
  await page
    .locator(".reviewed-source")
    .filter({ hasText: "Spotify" })
    .first()
    .getByRole("link", { name: "Trace" })
    .click();
  await page.locator(".trace-node").first().waitFor();
  await capture("source-details", true);
  await closeDialog(page, "Close viewer");
  await visit("/stack");
  await page.getByRole("button", { name: "Open Music Data Platform stack" }).click();
  await page
    .getByRole("dialog")
    .locator('[data-link-id="avatar-open-console"]')
    .waitFor();
  assert(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth ===
        document.documentElement.clientWidth,
    ),
    "Avatar links overflow. Check the preview width.",
  );
  await capture("stack-sheet", true);
  await closeDialog(page);
  // The pick is stamped by the server's real clock, and Undo is offered for ten minutes after it.
  await withServerTime(page, async () => {
    await visit("/songs?view=rising");
    const pick = page
      .getByRole("button", { name: "Pick this song", exact: true })
      .first();
    if (await pick.count()) {
      const submitted = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/calls" &&
          response.request().method() === "POST",
      );
      await pick.click();
      assert.equal(
        (await submitted).status(),
        200,
        "POST /calls saves a pick.",
      );
      await page
        .getByRole("link", { name: /^picked/ })
        .first()
        .waitFor();
      await page
        .getByRole("button", { name: "Undo", exact: true })
        .first()
        .click();
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Undo", exact: true })
        .click();
      await page.locator("dialog[open]").waitFor({ state: "hidden" });
    }
  });
  for (const [old, destination] of [
    ["/today", "/"],
    ["/rising", "/songs?view=rising"],
    ["/picks", "/songs?view=picks"],
    ["/calls", "/songs?view=picks"],
    ["/draft", "/songs?view=friday"],
    ["/holdings", "/stack"],
    ["/holdings?view=sources", "/sources?view=sources"],
    ["/holdings?view=rights", "/sources?view=rights"],
    ["/library", "/search"],
  ]) {
    const redirect = await page.request.get(origin + old, {
      maxRedirects: 0,
    });
    assert.equal(redirect.status(), 301, old);
    assert.equal(
      new URL(redirect.headers().location, origin).pathname +
        new URL(redirect.headers().location, origin).search,
      destination,
    );
    await visit(old);
    assert.equal(
      new URL(page.url()).pathname,
      new URL(destination, origin).pathname,
    );
    if (await page.locator("dialog[open]").count()) await closeDialog(page);
  }
  for (const suffix of ["", "/proof"]) {
    const path = `/picks/${keys[0]}${suffix}`;
    const response = await page.request.get(origin + path, {
      maxRedirects: 0,
    });
    assert.equal(response.status(), 301, path);
    assert.equal(
      new URL(response.headers().location, origin).pathname,
      `/songs/picks/${keys[0]}${suffix}`,
    );
  }
  await visit("/search?q=quasar");
  assert.equal(await page.locator("#library-query").inputValue(), "quasar");
  await page
    .getByRole("dialog")
    .getByText(/No matches/)
    .waitFor();
  await capture("search-empty", true);
  await closeDialog(page);
  await visit(`/s/song/${keys[0]}`);
  assert.equal(
    await page.locator('.top-tabs [aria-current="page"]').innerText(),
    "Songs",
  );
  for (const state of ["unavailable", "cached", "empty"]) {
    await page.route("**/s/night?*", async (route) => {
      const response = await route.fetch();
      const payload = nightResponse.parse(await response.json());
      if (state === "cached") payload.state = "cached";
      if (state === "unavailable") {
        payload.value.ready_state = "unavailable";
        payload.value.ready = [];
      }
      if (state === "empty") {
        payload.value.runs = [];
        payload.value.ready = [];
      }
      await route.fulfill({ response, json: payload });
    });
    await visit("/");
    await capture(`home-night-${state}`);
    await page.getByRole("button", { name: "Open last night" }).click();
    await capture(`night-${state}`, true);
    await closeDialog(page);
    await page.unroute("**/s/night?*");
  }
  for (const status of [429, 503]) {
    await page.route("**/s/night?*", (route) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify({ message: "Retry shortly." }),
      }),
    );
    await visit("/");
    await page
      .getByText("Logbook unavailable.", { exact: false })
      .waitFor({ timeout: 40000 });
    await capture(`home-night-${status}`);
    await page.getByRole("button", { name: "Open last night" }).click();
    await capture(`night-${status}`, true);
    await closeDialog(page);
    await page.unroute("**/s/night?*");
  }
  await stackWalk(
    page,
    origin,
    out,
    width,
    process.env.BROWSER_STACK_DATED === "1",
  );
  await teamLinksWalk(
    page,
    origin,
    out,
    width,
    process.env.BROWSER_STACK_DATED === "1",
  );
  await page.route("**/art/**", (route) =>
    route.fulfill({
      status: 404,
      body: "Artwork unavailable. Open the song.",
    }),
  );
  await visit("/");
  await page.waitForTimeout(7000);
  await capture("home-art-unavailable");
  await page.unroute("**/art/**");
  await writeFile(
    path.join(out, `redesign-density-${size}.json`),
    JSON.stringify(readings, null, 2),
  );
}
