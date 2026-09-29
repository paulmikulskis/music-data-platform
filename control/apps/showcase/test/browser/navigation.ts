import assert from "node:assert/strict";
import type { Page } from "@playwright/test";
export async function navigation(page: Page, expected: string, proof?: string) {
  const navigation = page.getByRole("navigation", {
    name: "Showcase",
    exact: true,
  });
  const back = navigation.getByRole("link", { name: "Back to showcase" });
  await back.waitFor();
  assert.equal(await back.getAttribute("href"), expected);
  if (proof) assert((await navigation.innerText()).includes(proof));
  await back.focus();
  assert(
    await back.evaluate((link) => {
      const root = link.getRootNode();
      return (
        (root instanceof ShadowRoot || root instanceof Document) &&
        root.activeElement === link
      );
    }),
  );
  assert(
    (await page.locator("body").ariaSnapshot()).includes("Back to showcase"),
  );
}
