import assert from "node:assert/strict";
import path from "node:path";
import { stat, writeFile } from "node:fs/promises";
import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { densityPolicy, overlay } from "./density.mjs";
import { closeDialogs } from "./dialog";
import { withServerTime } from "./clock";
import { assertSafeLinks } from "./link-privacy";
import { keys } from "./fixtures";

export async function teamLinksWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
  dated = false,
) {
  const readings: Record<string, unknown> = {};
  const size = `${width}x${width === 390 ? 844 : 900}`;
  async function measure(name: string, selector: string) {
    const value = await page.evaluate(overlay, {
      kind: "sheet",
      policy: densityPolicy,
      selector,
    });
    assert(value.found);
    // Reviewed function sentences have a separate 12–45 word check.
    assert(
      value.words <= 80 + value.detailWords,
      `${name}: ${value.words} words; ${value.text}`,
    );
    assert.deepEqual(value.banned, [], `${name}: ${value.text}`);
    assert.deepEqual(value.jargonWarnings, [], `${name}: ${value.text}`);
    readings[name] = value;
    assert(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth ===
          document.documentElement.clientWidth,
      ),
    );
    assert(
      await page
        .locator(selector)
        .evaluate((element) => element.scrollWidth === element.clientWidth),
    );
    for (const card of await page
      .locator(`${selector} .link-preview:visible`)
      .all()) {
      const value = await card.evaluate((element) => {
        const svg = element.querySelector("svg");
        const words =
          [...element.querySelectorAll("[data-preview-body] text")]
            .map((text) => text.textContent)
            .join(" ")
            .match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? [];
        return {
          words: words.length,
          numbers: words.filter((word) => /\d/.test(word)).length,
          frame: element.querySelector("[data-link-frame]")?.textContent,
          small: [...element.querySelectorAll("text")].some(
            (text) =>
              (parseFloat(getComputedStyle(text).fontSize) *
                (svg?.getBoundingClientRect().width ?? 320)) /
                320 <
              12,
          ),
          overflow: [...element.querySelectorAll("text")].some((text) => {
            const box = text.getBBox();
            return (
              box.x < 0 || box.x + box.width > 320 || box.y + box.height > 152
            );
          }),
        };
      });
      assert(
        value.frame && !value.small && !value.overflow,
        `${name}: preview text does not fit. Check its wrapping.`,
      );
      assert(value.words <= 12 && value.numbers <= 2, name);
    }
  }
  async function shot(name: string) {
    const file = path.join(out, `team-links-${name}-${size}.jpg`);
    await page.screenshot({ path: file, type: "jpeg", quality: 65 });
    assert((await stat(file)).size < 300_000);
  }
  async function disclosures(selector: string, name: string) {
    const root = page.locator(selector);
    await root.evaluate((element) => {
      if (element instanceof HTMLDetailsElement) element.open = true;
    });
    await root
      .locator("details")
      .evaluateAll((items) =>
        items.forEach((item) => item.removeAttribute("open")),
      );
    await measure(`${name}-closed`, selector);
    for (const item of await root.locator("details").all()) {
      await item.evaluate((element) => {
        for (
          let parent = element.parentElement;
          parent;
          parent = parent.parentElement
        ) {
          if (parent instanceof HTMLDetailsElement) parent.open = true;
        }
      });
      await item.locator(":scope > summary").click();
      const id =
        (await item.getAttribute("data-link-id")) ??
        (await item.locator(":scope > summary").innerText());
      const more = await item.evaluate((element) =>
        Boolean(element.closest(".analyst-more")),
      );
      await measure(
        `${name}-${id}`,
        more && selector.startsWith("#")
          ? `${selector} .analyst-more`
          : selector,
      );
      await item.locator(":scope > summary").click();
      await root
        .locator("details")
        .evaluateAll((items) =>
          items.forEach((item) => item.removeAttribute("open")),
        );
    }
  }
  await closeDialogs(page);
  await page.goto(origin + "/team");
  await page.locator("#try .question-results").waitFor();
  for (const id of [
    "invite",
    "practice",
    "first-question",
    "workbench",
    "screen",
  ]) {
    await disclosures(`#${id}`, `team-${id}`);
  }
  for (let i = 0; i < 5; i++)
    await disclosures(`.sql-layers li:nth-child(${i + 1})`, `layer-${i}`);
  await disclosures(".sql-layers > details", "sql-help");
  assert.match(
    await page.locator("#screen").innerText(),
    /Checks run on every proposal; the data team merges/,
  );
  assert.match(
    await page.locator("#screen").innerText(),
    /Opens as a change to review/,
  );
  await page.locator("#screen").scrollIntoViewIfNeeded();
  await shot("screen");
  const open = page.locator(".team-open");
  const copy = open.getByRole("button", { name: "Copy request", exact: true });
  await expect(copy).toBeVisible();
  await copy.click();
  await expect(open.getByRole("status")).toHaveText(
    /Copied\.|Copy is unavailable\./,
  );
  const email = open.getByRole("link", { name: "Email it" });
  await expect(email).toHaveAttribute("href", /^mailto:\?/);
  assert(
    await open.evaluate((element) => element.scrollWidth <= element.clientWidth),
  );
  for (const control of [copy, email]) {
    const box = await control.boundingBox();
    assert(box && box.height >= 44);
  }
  await page
    .locator(".team-open")
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await shot("request");
  await measure("login-request", ".login-request");
  if (!dated) {
    assert.equal(
      await page
        .locator('#invite [data-link-id="team-1-contributing"]')
        .count(),
      1,
    );
    await page
      .locator('[data-link-id="team-1-contributing"] > summary')
      .click();
    await page.locator("#invite").scrollIntoViewIfNeeded();
    await shot("start");
    await page
      .locator('[data-link-id="team-1-contributing"] > summary')
      .click();
    await page
      .locator("#sql-layers")
      .evaluate((element) => element.scrollIntoView({ block: "start" }));
    await shot("sql-folders");
    await page.locator("#first-question .analyst-more > summary").click();
    await page.locator('[data-link-id="team-3-notebook"] > summary').click();
    await page.locator("#first-question").scrollIntoViewIfNeeded();
    await shot("steps");
  }
  for (const route of [
    "/team",
    "/team?_rsc=links",
    "/sources",
    "/sources?_rsc=links",
    "/s/trace?entry=source.sz_chart",
    "/s/proof/source/sz_chart?from=%2Fsources",
  ]) {
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
    assert.equal(response.status, 200, route);
    if (route.includes("_rsc"))
      assert(response.headers["content-type"]?.includes("text/x-component"));
    assertSafeLinks(response.text);
    assertSafeLinks(JSON.stringify(response.headers));
  }
  const github = await page
    .locator('a[href*="github.com"]')
    .evaluateAll((links) =>
      links.map((link) => ({
        href: link.getAttribute("href"),
        note: link.previousElementSibling?.textContent,
      })),
    );
  if (dated) assert.equal(github.length, 0);
  for (const link of github) {
    assert.match(link.href ?? "", /\/(blob|tree)\/[a-f0-9]{40}\//);
    assert.equal(
      link.note,
      "Public source. GitHub shows contributor handles.",
    );
  }
  await withServerTime(page, async () => {
    await page.locator('#workbench a[href^="/s/open"]').click();
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL(origin + "/team#workbench");
    await page.locator('#try a[href^="/workbench?intent=query"]').click();
    await page.locator("#prefill-sql").waitFor();
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL(origin + "/team#try");
  });
  await page.goto(origin + "/sources");
  await page.locator(".reviewed-source").first().waitFor();
  const detail = page
    .locator(".reviewed-source")
    .getByRole("button", { name: /details/i })
    .first();
  await detail.click();
  await page.getByRole("dialog").waitFor();
  await disclosures("dialog[open]", "sources");
  if (!dated)
    await page
      .locator('[data-link-id="sources-reader-code"] > summary')
      .click();
  await shot("sources");
  await closeDialogs(page);
  const tracePath = `/sources?${new URLSearchParams({
    trace: "home.shazam_spread_gain",
    song: keys[0],
    ranking: "fixture-ranking",
  })}`;
  await page.goto(origin + tracePath);
  await page.locator('[data-stage="ready"] .trace-peek-button').first().click();
  await page.locator(".trace-table").waitFor();
  await disclosures(".trace-peek", "peek");
  if (!dated)
    await page.locator('[data-link-id="viewer-node-code"] > summary').click();
  await page.locator(".trace-peek").evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await shot("peek");
  await withServerTime(page, async () => {
    await page.locator(".peek-explorer > summary").click();
    const explorer = page.locator('[data-link-id="viewer-open-explorer"] a');
    await explorer.click();
    assert.equal(new URL(page.url()).pathname, "/explorer");
    assert.equal(
      new URL(page.url()).searchParams.get("q"),
      "marts.mart_shazam_chart_daily",
    );
    assert(new URL(page.url()).searchParams.has("showcase_proof"));
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL(origin + tracePath);
    await page.locator(".trace-node").first().waitFor();
  });
  await closeDialogs(page);
  await page.waitForURL(origin + "/sources");
  await page.goto(origin + "/sources?credits=1");
  await page.getByRole("dialog").waitFor();
  for (const link of await page
    .locator('dialog[open] a[href^="https:"]')
    .all()) {
    assert.equal(await link.getAttribute("target"), "_blank");
    assert.equal(await link.getAttribute("rel"), "noopener noreferrer");
  }
  await closeDialogs(page);
  await withServerTime(page, async () => {
    await page.goto(origin + "/s/proof/source/sz_chart?from=%2Fsources");
    await page.locator('[data-link-id="proof-open-console"] a').click();
    assert.equal(new URL(page.url()).pathname, "/functions/sz_chart");
    assert(new URL(page.url()).searchParams.has("showcase_proof"));
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL(origin + "/s/proof/source/sz_chart?from=%2Fsources");
    await page.getByRole("link", { name: "Back", exact: true }).click();
    await page.waitForURL(origin + "/sources");
  });
  await writeFile(
    path.join(out, `team-links-density-${size}.json`),
    JSON.stringify(readings, null, 2) + "\n",
  );
}
