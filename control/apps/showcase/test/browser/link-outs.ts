import assert from "node:assert/strict";
import path from "node:path";
import { stat, writeFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { densityPolicy, overlay } from "./density.mjs";
import { closeDialog } from "./dialog";
import { withServerTime } from "./clock";
import { assertSafeLinks } from "./link-privacy";

export async function linkOutWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
  dated: boolean,
) {
  const size = `${width}x${width === 390 ? 844 : 900}`;
  const readings: Record<string, unknown> = {};
  async function fits() {
    assert(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth ===
          document.documentElement.clientWidth,
      ),
      "The page overflows. Check the link preview width.",
    );
    assert(
      await page
        .locator("dialog[open]")
        .evaluateAll((dialogs) =>
          dialogs.every((dialog) => dialog.scrollWidth === dialog.clientWidth),
        ),
      "The sheet overflows. Check the link preview width.",
    );
  }
  async function measure(name: string) {
    const value = await page.evaluate(overlay, {
      kind: "sheet",
      policy: densityPolicy,
    });
    assert(value.found);
    assert(value.words <= 80, `${name}: ${value.words} words; ${value.text}`);
    assert.deepEqual(value.banned, [], `${name}: ${value.text}`);
    assert.deepEqual(value.jargonWarnings ?? [], [], `${name}: ${value.text}`);
    readings[name] = value;
    await fits();
  }
  async function shot(name: string) {
    const file = path.join(out, `links-${name}-${size}.jpg`);
    await page.screenshot({ path: file, type: "jpeg", quality: 65 });
    assert(
      (await stat(file)).size < 300_000,
      "Screenshot exceeds 300 KB. Lower its quality.",
    );
  }
  async function cards() {
    const results = await page
      .locator(".link-preview:visible")
      .evaluateAll((elements) =>
        elements.map((element) => {
          const body = element.querySelector("[data-preview-body]");
          const words =
            [...(body?.querySelectorAll("text") ?? [])]
              .map((text) => text.textContent)
              .join(" ")
              .match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? [];
          const svg = element.querySelector("svg");
          const frame = element.querySelector("[data-link-frame]");
          const bounds = svg?.getBoundingClientRect();
          return {
            words: words.length,
            numbers: words.filter((word) => /\d/.test(word)).length,
            width: bounds?.width ?? 0,
            frame: frame?.textContent,
            viewBox: svg?.getAttribute("viewBox"),
            monospace: [...element.querySelectorAll("*")].some((child) =>
              /Geist|monospace/i.test(getComputedStyle(child).fontFamily),
            ),
            overflow: [...element.querySelectorAll("text")].some((text) => {
              const box = text.getBBox();
              return (
                box.x < 0 ||
                box.x + box.width > 320 ||
                box.y < 0 ||
                box.y + box.height > 152
              );
            }),
          };
        }),
      );
    for (const result of results) {
      assert(
        result.words <= 12 && result.numbers <= 2,
        "A preview exceeds its word budget. Shorten its manifest copy.",
      );
      assert(result.width <= 320 && !result.overflow);
      assert(result.frame);
      assert.equal(result.viewBox, "0 0 320 152");
      assert(!result.monospace);
    }
  }
  await page.goto(origin + "/stack");
  const serviceIds = await page
    .locator(".service-card")
    .evaluateAll((cards) => cards.map((card) => card.id));
  for (const id of serviceIds) {
    const card = page.locator(`.service-card#${id}`);
    await card.getByRole("button", { name: "See more", exact: true }).click();
    await page.getByRole("dialog").waitFor();
    await page.waitForTimeout(250);
    await measure(`${id}-closed`);
    for (const disclosure of await page
      .getByRole("dialog")
      .locator("details")
      .all()) {
      await disclosure.locator("summary").click();
      const name = (await disclosure.getAttribute("data-link-id")) ?? "facts";
      await measure(`${id}-${name}`);
      await cards();
      if (id === "warehouse" && name === "more-served-doc") await shot("sheet");
      await disclosure.locator("summary").click();
    }
    await closeDialog(page);
  }
  if (!dated) {
    const code = page.locator("#warehouse > .card-actions .code-link");
    await code.locator("summary").click();
    await code.scrollIntoViewIfNeeded();
    await cards();
    await fits();
    await shot("code");
    await code.locator("summary").click();
    const fallback = page.locator("#musicbrainz > .card-actions .code-link");
    await fallback.locator("summary").click();
    await fallback.getByText("Preview not recorded at this deploy").waitFor();
    assert.equal(await fallback.locator("svg").count(), 0);
    await fallback.scrollIntoViewIfNeeded();
    await shot("fallback");
    await fallback.locator("summary").click();
  }

  // The hand-off is signed by the server. Console Back keeps the service anchor.
  await withServerTime(page, async () => {
    const link = page
      .locator("#console > .card-actions")
      .getByRole("link", { name: "Open Console →" });
    const href = await link.getAttribute("href");
    assert(href?.startsWith("/s/open?"));
    const cookie = (await page.context().cookies())
      .map(({ name, value }) => `${name}=${value}`)
      .join("; ");
    const response = await page.request.get(origin + href, {
      headers: { cookie },
      maxRedirects: 0,
    });
    assert.equal(response.status(), 303);
    assertSafeLinks(JSON.stringify(response.headers()));
    const location = new URL(response.headers().location!, origin);
    assert(location.searchParams.has("showcase_proof"));
    await link.click();
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL(origin + "/stack#console");
  });
  await page.getByRole("button", { name: "Open Music Data Platform stack" }).click();
  await page
    .getByRole("dialog")
    .locator('[data-link-id="avatar-open-console"]')
    .waitFor();
  await measure("avatar-closed");
  await shot("avatar");
  for (const disclosure of await page
    .getByRole("dialog")
    .locator("details")
    .all()) {
    await disclosure.locator("summary").click();
    await measure(`avatar-${await disclosure.getAttribute("data-link-id")}`);
    await cards();
    await disclosure.locator("summary").click();
  }
  await withServerTime(page, async () => {
    await page
      .getByRole("dialog")
      .getByRole("link", { name: "Open Console →" })
      .click();
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL(
      (url) => url.pathname === "/stack" && url.hash === "#console",
    );
  });
  // Returning may restore the avatar's sheet parameter. Close it before the next walk.
  if (await page.getByRole("dialog").count()) await closeDialog(page);
  await writeFile(
    path.join(out, `links-density-${size}.json`),
    JSON.stringify(readings, null, 2) + "\n",
  );
}
