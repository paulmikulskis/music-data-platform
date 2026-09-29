import assert from "node:assert/strict";
import path from "node:path";
import type { Page } from "@playwright/test";
import { density, densityPolicy } from "./density.mjs";
import { navigation } from "./navigation";

// A proof link opens a plain summary inside the showcase first: the exact entries, when and
// where they were read, Back to the screen it came from, and the operator console one quiet link away.
// The operator console's Back bar then returns to that summary.
export async function proofSummary(
  page: Page,
  origin: string,
  out: string,
  name: string,
  size: string,
  expect: { headline: RegExp; back: string; item?: string },
  measurements?: Record<string, ReturnType<typeof density>>,
) {
  await page.waitForURL((url) => url.pathname.startsWith("/s/proof/"));
  const heading = page.locator(".proof-room h1");
  await heading.waitFor();
  assert.match(await heading.innerText(), expect.headline, `${name} headline`);
  if (expect.item)
    await page
      .locator(".proof-items li")
      .filter({ hasText: expect.item })
      .first()
      .waitFor();
  const back = page.locator(".proof-actions").getByRole("link", {
    name: "Back",
    exact: true,
  });
  assert.equal(await back.getAttribute("href"), expect.back, `${name} Back`);
  const engine = page.getByRole("link", {
    name: "Open in Console ↗",
    exact: true,
  });
  const onward = await engine.getAttribute("href");
  assert(
    onward?.includes("/engine?") || onward?.startsWith("/functions/"),
    `${name} offers the operator console as its onward link: ${onward}`,
  );
  assert.equal(
    await page
      .locator(
        ".proof-room a[href^='/runs/'], .proof-room a[href^='/explorer']",
      )
      .count(),
    0,
    `${name} never links straight into a run or the Explorer.`,
  );
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, `${name}-${size}.png`) });
  if (size === "390x844" && measurements)
    measurements[name] = await page.evaluate(density, densityPolicy);
  // Refresh keeps the proof's facts: the same summary reloads, never "unavailable".
  await page.waitForTimeout(1100);
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("link", { name: "Refresh ↻", exact: true }).click(),
  ]);
  await heading.waitFor();
  assert.match(
    await heading.innerText(),
    expect.headline,
    `${name} headline after Refresh`,
  );
  const summary = new URL(page.url());
  summary.searchParams.delete("showcase_proof");
  // Each click stays within the app's per-second request limit.
  await page.waitForTimeout(1100);
  await engine.click();
  await page.waitForURL(
    (url) =>
      !url.pathname.startsWith("/s/proof/") ||
      url.searchParams.has("showcase_proof"),
  );
  if (new URL(page.url()).pathname.startsWith("/s/proof/")) {
    // No contributing run: the summary says so and keeps its Back.
    await page
      .getByText("The operator console has no run for these entries yet.", {
        exact: true,
      })
      .waitFor();
  } else {
    await navigation(page, summary.toString());
    await page.screenshot({
      path: path.join(out, `${name}-engine-${size}.png`),
    });
  }
  await page.goto(summary.toString());
  await page.waitForTimeout(1100);
  await page
    .locator(".proof-actions")
    .getByRole("link", { name: "Back", exact: true })
    .click();
  await page.waitForURL((url) => url.pathname + url.search === expect.back);
}
