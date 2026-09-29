import { appsPayload } from "../../lib/apps";
import { stackStatus } from "../../lib/stack";
import { linkResponsePrivacy } from "./link-privacy";
import { linkOutWalk } from "./link-outs";
import assert from "node:assert/strict";
import path from "node:path";
import { stat, writeFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { density, overlay, densityPolicy } from "./density.mjs";
// The Stack and Team pages at one width: the picture, the cards, a status card, a See more
// sheet, the code access note, the analyst question, and each failure state. Every capture
// checks the viewer word limits and, on a phone, that nothing scrolls sideways.
export async function stackWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
  dated: boolean,
) {
  const size = `${width}x${width === 390 ? 844 : 900}`;
  const readings: Record<string, unknown> = {};
  const shot = async (name: string, fullPage = false) => {
    const file = path.join(out, `${name}-${size}.jpg`);
    await page.screenshot({
      path: file,
      type: "jpeg",
      quality: fullPage ? 40 : 60,
      fullPage,
    });
    assert(
      (await stat(file)).size < 300_000,
      `${name}: screenshot exceeds 300 KB. Lower its JPEG quality.`,
    );
  };
  const fits = async (name: string) => {
    const bounds = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    assert.equal(
      bounds.scroll,
      bounds.client,
      `${name}: the page must fit its viewport at ${width}px.`,
    );
  };
  const capture = async (name: string, sheet = false, fullPage = false) => {
    await page.waitForTimeout(350);
    const result = sheet
      ? await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })
      : await page.evaluate(density, densityPolicy);
    readings[name] = result;
    assert.deepEqual(result.banned, [], `${name}: ${result.text}`);
    assert.deepEqual(
      result.jargonWarnings ?? [],
      [],
      `${name}: ${result.text}`,
    );
    if ("headlinePatterns" in result)
      assert.deepEqual(result.headlinePatterns, [], name);
    if ("clockViolations" in result)
      assert.deepEqual(result.clockViolations, [], name);
    if ("monospace" in result)
      assert.equal(result.monospace, 0, `${name}: monospace text`);
    if (sheet)
      assert(
        result.words <= 80,
        `${name}: ${result.words} words; ${result.text}`,
      );
    else if (width === 390) {
      assert(
        result.words <= 40,
        `${name}: ${result.words} words; ${result.text}`,
      );
      assert(
        result.numbers <= 3,
        `${name}: ${result.numbers} numbers; ${result.text}`,
      );
    }
    await fits(name);
    await shot(name, fullPage);
  };
  const visit = async (route: string) => {
    const response = await page.goto(origin + route);
    assert.equal(response?.status(), 200, route);
    await page.locator('.top-tabs [aria-current="page"]').waitFor();
    await page.waitForTimeout(700);
  };
  const hoverCheck = async (name: string) => {
    await page.locator("[data-popover]").waitFor();
    // The card fades in over 120 ms; the reader counts only text at full opacity.
    await page.waitForFunction(() => {
      const card = document.querySelector("[data-popover]");
      return (
        card instanceof HTMLElement && getComputedStyle(card).opacity === "1"
      );
    });
    const card = await page.evaluate(overlay, {
      kind: "hover",
      policy: densityPolicy,
    });
    readings[name] = card;
    assert(card.found && card.words <= 30, `${name}: ${card.words} words`);
    assert(card.numbers <= 4, `${name}: ${card.numbers} numbers`);
    assert.deepEqual(card.banned, [], name);
    assert.deepEqual(card.jargonWarnings ?? [], [], name);
  };

  // Screenshot probes use a fixed successful check, never a live status.
  await page.route("**/s/stack", async (route) => {
    const response = await route.fetch();
    const payload = stackStatus.parse(await response.json());
    await route.fulfill({
      json: {
        ...payload,
        checked_at: "2026-09-28T12:00:00Z",
        services: payload.services.map((service) => ({
          ...service,
          note:
            service.state === "not_checked"
              ? "No check runs from this app."
              : "Checks the service, not its jobs.",
          state: service.state === "not_checked" ? "not_checked" : "answered",
          checked_at:
            service.state === "not_checked" ? null : "2026-09-28T12:00:00Z",
        })),
      },
    });
  });
  await page.route("**/s/apps", async (route) => {
    const response = await route.fetch();
    const payload = appsPayload.parse(await response.json());
    await route.fulfill({
      json: {
        ...payload,
        apps: payload.apps.map((app) => ({
          ...app,
          state: "live",
          checked_at: "2026-09-28T12:00:00Z",
        })),
      },
    });
  });
  // Stack: the picture, then the cards with their checks.
  await visit("/stack");
  await page.locator(".service-card").first().waitFor();
  const svg = page.locator(
    width === 390 ? "svg.overview-tall" : "svg.overview-wide",
  );
  await svg.waitFor();
  await page.waitForTimeout(1600);
  assert.equal(await svg.getAttribute("data-state"), "drawn");
  // Every label sits inside its box, measured in the picture's own units at this width.
  const overflow = await svg.evaluate((element) => {
    const out: string[] = [];
    for (const node of element.querySelectorAll("a.overview-node")) {
      const rect = node.querySelector("rect");
      if (!(rect instanceof SVGGraphicsElement)) continue;
      const box = rect.getBBox();
      for (const text of node.querySelectorAll("text")) {
        const bounds = text.getBBox();
        const inside =
          bounds.x >= box.x + 2 &&
          bounds.x + bounds.width <= box.x + box.width - 2 &&
          bounds.y >= box.y &&
          bounds.y + bounds.height <= box.y + box.height;
        if (!inside)
          out.push(
            `${text.textContent ?? ""}: ${Math.round(bounds.x + bounds.width)} past ${Math.round(box.x + box.width)}`,
          );
      }
    }
    return out;
  });
  assert.deepEqual(
    overflow,
    [],
    `stack-top: a label runs past its box at ${width}px.`,
  );
  // The hosting mark links to its provider like every other mark.
  assert.equal(await svg.locator('a[aria-label="Open Fly.io"]').count(), 1);
  await page.evaluate(() => window.scrollTo(0, 0));
  await capture("stack-top");
  await page
    .locator(".status-dot.answered")
    .first()
    .waitFor({ timeout: 8000 })
    .catch(() => {});
  const dots = await page.locator(".service-card .status-dot").count();
  assert.equal(dots, 9, "One status dot per deployed service.");
  const notice = await page
    .getByText("Counts not refreshed at this deploy.")
    .count();
  assert.equal(
    notice > 0,
    dated,
    dated
      ? "Dated mode says counts are not refreshed."
      : "Generated mode shows no refresh notice.",
  );
  await capture(dated ? "stack-dated-full" : "stack-full", false, true);
  // A status card names its probe and time.
  const warehouse = page.getByRole("button", {
    name: "Warehouse status",
    exact: true,
  });
  await warehouse.scrollIntoViewIfNeeded();
  if (width === 1440) {
    await warehouse.hover();
    await hoverCheck("stack-status-hover");
    assert.match(
      await page.locator("[data-popover]").innerText(),
      /Warehouse SQL check|not checked/i,
    );
    await shot("stack-status-hover");
    await page.mouse.move(0, 0);
    await page.locator("[data-popover]").waitFor({ state: "hidden" });
  } else {
    await warehouse.click();
    await capture("stack-status-sheet", true);
    assert.match(
      await page.getByRole("dialog").innerText(),
      /Warehouse SQL check|not checked/i,
    );
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
  }
  // See more opens the fuller facts in a sheet with the dates kept.
  const card = page.locator("#warehouse");
  await card.getByRole("button", { name: "See more", exact: true }).click();
  await capture("stack-see-more", true);
  const sheetText = await page.getByRole("dialog").innerText();
  assert.match(sheetText, /PostgreSQL 17/);
  assert.match(sheetText, /27 Sept/);
  if (!dated) assert.match(sheetText, /Code/);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  // Code links pin the page's revision and explain public access in the same disclosure.
  const code = card.locator("details.code-link");
  if (dated) {
    assert.equal(
      await page.locator('a[href*="github.com/paulmikulskis/"]').count(),
      0,
    );
  } else {
    await code.locator("summary").click();
    await code
      .getByText(
        "Public source. GitHub shows contributor handles.",
      )
      .waitFor();
    const href = await code
      .getByRole("link", { name: "Open code ↗" })
      .getAttribute("href");
    assert.match(href ?? "", /\/tree\/[a-f0-9]{40}\/ops\/fly\/postgres$/);
    await code.getByText(/Code at this page's build/).waitFor();

    await shot("stack-code");
    await code.locator("summary").click();
  }
  // Leftover machines and unlisted hosts never appear.
  const pageText = await page.locator("[data-viewer-screen]").innerText();
  assert.doesNotMatch(pageText, /mdp-rebuild|-analyst-a|-analyst-b|\$\d|\bcost\b/i);
  assert.match(pageText, /Installed, no tables yet/);
  // The avatar sheet links into this page.
  await page.getByRole("button", { name: "Open Music Data Platform stack" }).click();
  await capture("stack-avatar-sheet", true);
  const link = page
    .getByRole("dialog")
    .getByRole("link", { name: "Open Console →" });
  assert((await link.getAttribute("href"))?.startsWith("/s/open?"));
  if (!dated)
    await page.getByRole("dialog").locator(".app-counts summary").click();
  const avatar = await page.getByRole("dialog").innerText();
  if (dated) assert.doesNotMatch(avatar, /on a timer/);
  else
    assert.match(
      avatar,
      /2 databases · 6 servers · 3 on a timer · counted 27 Sept/,
    );
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await linkResponsePrivacy(page, origin, dated);
  await linkOutWalk(page, origin, out, width, dated);
  // Failure: the status read fails, the cards stay, each dot says it was not checked.
  await page.route("**/s/stack", (route) =>
    route.fulfill({ status: 503, body: "Status unavailable. Retry." }),
  );
  await visit("/stack");
  await page.locator(".service-card").first().waitFor();
  await page.waitForTimeout(600);
  assert.equal(await page.locator(".status-dot.answered").count(), 0);
  await warehouse.scrollIntoViewIfNeeded();
  await warehouse.click();
  await page
    .getByRole("dialog")
    .getByText(/Status not checked at/)
    .waitFor();
  await capture("stack-status-unavailable", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.unroute("**/s/stack");

  // Team: the desk, the steps, one question with up to five results.
  await visit("/team");
  await page.locator("#try").waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await capture("team-top");
  await page
    .locator("#try [role=status], #try .question-results")
    .first()
    .waitFor({ timeout: 8000 });
  await page
    .locator("#try .question-results li")
    .first()
    .waitFor({ timeout: 8000 })
    .catch(() => {});
  const results = await page.locator("#try .question-results li").count();
  assert(
    results >= 1 && results <= 5,
    `Team question returned ${results} results.`,
  );
  await capture("team-full", false, true);
  const desk = page.locator(width === 390 ? "svg.desk-tall" : "svg.desk-wide");
  await desk.scrollIntoViewIfNeeded();
  await desk.screenshot({
    path: path.join(out, `team-desk-${size}.jpg`),
    type: "jpeg",
    quality: 70,
  });
  const steps = await page.locator(".analyst-steps li").count();
  assert.equal(steps, 5);
  await page.locator("#try").scrollIntoViewIfNeeded();
  await page.getByRole("button", { name: "Copy SQL", exact: true }).click();
  await page.locator("#try [role=status]").last().waitFor();
  await shot("team-question");
  // The same SQL opens in the Workbench, shown before any session runs it, and Back returns here.
  const prefill = page.getByRole("link", { name: "Open in Workbench →" });
  const workbenchHref = (await prefill.getAttribute("href")) ?? "";
  assert(workbenchHref.startsWith("/workbench?intent=query&sql="));
  assert(workbenchHref.includes("showcase_proof="));
  await prefill.click();
  await page.locator("#prefill-sql").waitFor();
  assert.match(
    await page.locator("#prefill-sql").inputValue(),
    /mart_shazam_chart_daily/,
  );
  await shot("team-workbench-landing");
  await page.getByRole("link", { name: "Back to showcase" }).click();
  await page.locator("#try").waitFor();
  assert.equal(new URL(page.url()).pathname, "/team");
  // Failure: the question read fails; the SQL stays available.
  await page.route("**/s/team-question?*", (route) =>
    route.fulfill({
      status: 503,
      body: "Taking too long. Retry, or copy the SQL.",
    }),
  );
  await visit("/team");
  await page.locator("#try").scrollIntoViewIfNeeded();
  await page.getByText("Taking too long.", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Retry", exact: true }).waitFor();
  await shot("team-question-unavailable");
  await page.unroute("**/s/team-question?*");
  await writeFile(
    path.join(out, `stack-density-${size}.json`),
    JSON.stringify(readings, null, 2),
  );
  await page.unroute("**/s/apps");
}
