import assert from "node:assert/strict";
import path from "node:path";
import type { Page, Request } from "@playwright/test";
import { peekView } from "../../lib/trace";

export function observeViewer(
  page: Page,
  tab: number,
  directory: string,
  label: string,
  write: (event: Record<string, unknown>) => void,
) {
  const log = (event: Record<string, unknown>) =>
    write({ at: Date.now(), tab, viewport: page.viewportSize(), ...event });
  const requests = new Map<Request, number>();
  let nextRequest = 0;
  let savedFailure = false;
  const target = page
    .getByRole("dialog", { name: "Data source viewer", exact: true })
    .locator('.trace-stage[data-stage="ready"]')
    .getByRole("button", { name: "See rows", exact: true });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["/s/trace", "/s/peek"].includes(url.pathname)) return;
    const id = ++nextRequest;
    requests.set(request, id);
    log({
      event: "request",
      request: id,
      path: url.pathname,
      entry: url.searchParams.get("entry"),
      relation: url.searchParams.get("relation"),
    });
  });
  page.on("response", (response) => {
    const id = requests.get(response.request());
    if (id === undefined) return;
    log({ event: "response", request: id, status: response.status() });
  });
  page.on("requestfailed", (request) => {
    const id = requests.get(request);
    if (id === undefined) return;
    log({ event: "request-failed", request: id, error: request.failure() });
  });
  page.on("pageerror", (error) => {
    log({ event: "page-error", error: error.message });
  });
  async function inspectTarget() {
    return target.evaluateAll((buttons) =>
      buttons.map((button) => {
        const box = button.getBoundingClientRect();
        const top = document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        );
        return {
          tag: button.tagName,
          text: button.textContent,
          class: button.className,
          node: button
            .closest("section")
            ?.querySelector("[data-node]")
            ?.getAttribute("data-node"),
          box: { x: box.x, y: box.y, width: box.width, height: box.height },
          top: top
            ? {
                tag: top.tagName,
                class: top.className,
                text: top.textContent?.slice(0, 160),
              }
            : null,
          receives_pointer: !!top && (button === top || button.contains(top)),
        };
      }),
    );
  }
  async function failed(error: unknown) {
    log({ event: "failure", error: String(error) });
    if (savedFailure) return;
    savedFailure = true;
    log({ event: "failure-target", targets: await inspectTarget() });
    const filename = `${label}-tab-${tab}-failure.jpg`;
    await page.screenshot({
      path: path.join(directory, filename),
      type: "jpeg",
      quality: 60,
    });
    log({ event: "failure-screenshot", file: filename });
  }
  async function open(origin: string) {
    const navigation = await page.goto(origin + "/sources?trace=source.sp_playlist");
    log({
      event: "navigation",
      path: "/sources?trace=source.sp_playlist",
      status: navigation?.status(),
    });
    assert.equal(
      navigation?.status(),
      200,
      "The source page must load. Open the tab interaction log.",
    );
    log({
      event: "target-search",
      role: "button",
      name: "See rows",
      stage: "ready",
    });
    await target.waitFor({ state: "visible" });
    await target.scrollIntoViewIfNeeded();
    log({ event: "click-target", targets: await inspectTarget() });
    // Handle rejection immediately, but await the click first so its own failure is visible.
    const pending = page
      .waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/s/peek" &&
          response.status() === 200,
      )
      .then(
        (response) => ({ response }),
        (error: unknown) => ({ error }),
      );
    try {
      await target.click();
      log({ event: "click-completed" });
    } catch (error) {
      log({ event: "click-failed", error: String(error) });
      throw error;
    }
    const received = await pending;
    if ("error" in received) throw received.error;
    const result = peekView.parse(await received.response.json());
    assert.equal(
      result.state,
      "ready",
      "The preview must be ready. Open the tab interaction log.",
    );
    assert(
      result.rows.length <= 5,
      "The preview holds at most five results. Open the tab interaction log.",
    );
    await page
      .getByRole("complementary", { name: "Table preview" })
      .locator(".trace-table")
      .waitFor();
    log({ event: "peek-visible", results: result.rows.length });
    return result;
  }
  return { open, failed };
}
