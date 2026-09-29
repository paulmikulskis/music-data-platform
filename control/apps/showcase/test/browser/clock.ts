import assert from "node:assert/strict";
import type { Page } from "@playwright/test";

export async function pinBrowserClock(page: Page, time: string) {
  // Keep performance.now, animation frames and the browser animation timeline together.
  // Only calendar time changes; each document starts at the selected time and keeps ticking.
  await page.addInitScript((at: number) => {
    const LiveDate = Date;
    const started = LiveDate.now();
    const calendar = {
      now() {
        return at + LiveDate.now() - started;
      },
    };
    globalThis.Date = new Proxy(LiveDate, {
      construct(target, args) {
        return Reflect.construct(target, args.length ? args : [calendar.now()]);
      },
      apply() {
        return new LiveDate(calendar.now()).toString();
      },
      get(target, property, receiver) {
        return property === "now"
          ? calendar.now
          : Reflect.get(target, property, receiver);
      },
    });
  }, Date.parse(time));
}

export async function checkBrowserClock(page: Page, time: string) {
  const observed = await page.evaluate(() => ({
    now: Date.now(),
    constructed: new Date().getTime(),
    zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  }));
  const elapsed = observed.now - Date.parse(time);
  assert(
    elapsed >= 0 && elapsed < 60000,
    `The browser clock is ${new Date(observed.now).toISOString()}. Check the clock helper.`,
  );
  assert(Math.abs(observed.now - observed.constructed) < 1000);
  assert.equal(observed.zone, "UTC");
}

// A step that writes through the server (a pick, an undo) is judged against the time the server
// stamped it with, so the browser follows real time while the step runs, then returns to the
// pinned time. Each pin applies from the next document the page opens.
export async function withServerTime<T>(page: Page, step: () => Promise<T>) {
  const pinned = await page.evaluate(() => Date.now());
  await pinBrowserClock(page, new Date().toISOString());
  try {
    return await step();
  } finally {
    await pinBrowserClock(page, new Date(pinned).toISOString());
  }
}
