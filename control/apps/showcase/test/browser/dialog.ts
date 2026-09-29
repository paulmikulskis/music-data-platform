import assert from "node:assert/strict";
import type { Page } from "@playwright/test";

export async function closeDialog(page: Page, name = "Close") {
  const dialog = page.locator("dialog[open]").last();
  const element = await dialog.elementHandle();
  assert(element, "An open sheet is required. Open the browser screenshot.");
  await dialog.getByRole("button", { name, exact: true }).click();
  await page.waitForFunction(
    (node) =>
      !node?.isConnected || !(node instanceof HTMLDialogElement) || !node.open,
    element,
  );
}

export async function closeDialogs(page: Page) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const dialog = page.locator("dialog[open]").last();
    if (!(await dialog.count())) return;
    const viewer =
      (await dialog.getAttribute("aria-label")) === "Data source viewer";
    await closeDialog(page, viewer ? "Close viewer" : "Close");
  }
  assert.equal(
    await page.locator("dialog[open]").count(),
    0,
    "A sheet stays open after Close. Open the browser screenshot.",
  );
}
