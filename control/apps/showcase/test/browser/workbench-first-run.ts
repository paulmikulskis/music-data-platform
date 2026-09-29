import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const out = path.resolve("../../../ops/evidence/h-workbench-first-run");
const origin = "http://127.0.0.1:18490";
await mkdir(out, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({
    viewport: { width: 1100, height: 850 },
  });
  page.setDefaultTimeout(60000);
  await page.goto(origin + "/workbench");
  await page.locator("#wb-sql").fill("SELECT * FROM staging.stg_billboard__chart_entries LIMIT 100");
  await page.locator('button[name="action"][value="query"]').first().click();
  await page
    .getByRole("heading", { name: "Query results", exact: true })
    .waitFor();
  assert.match(await page.locator("#wb-sql").inputValue(), /explore_staging/);
  await page
    .locator(".result-card")
    .screenshot({ path: path.join(out, "workbench-first-run.png") });
  const sql = (await page.locator("#wb-sql").inputValue()).replace(
    "explore_staging.",
    "staging.",
  );
  await page.locator("#wb-sql").fill(sql);
  await page.locator('button[name="action"][value="query"]').first().click();
  await page
    .getByRole("button", { name: "Use safe copy and rerun", exact: true })
    .waitFor();
  await page
    .locator("#build")
    .screenshot({ path: path.join(out, "workbench-safe-copy.png") });
  await page
    .getByRole("button", { name: "Use safe copy and rerun", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Query results", exact: true })
    .waitFor();
  assert.match(await page.locator("#wb-sql").inputValue(), /explore_staging/);
  console.log(
    "First query succeeds; the refusal names its safe copy; one click rewrites and reruns successfully. Open the evidence PNGs.",
  );
} finally {
  await browser.close();
}
