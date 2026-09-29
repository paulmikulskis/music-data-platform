import assert from "node:assert/strict";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import { chromium, expect, type Browser, type Page } from "@playwright/test";
import { pinBrowserClock } from "./clock";
import { keys } from "./fixtures";
import { z } from "zod";
import { bucketFor } from "../../lib/request-bucket";

type Hit = {
  path: string;
  method: string;
  kind: string;
  bucket: string;
  at: number;
};
export async function rateWalk(
  browser: Browser,
  origin: string,
  out: string,
  signIn: () => Promise<string>,
  clock: string,
) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const page = await context.newPage();
  await pinBrowserClock(page, clock);
  page.setDefaultTimeout(20000);
  await page.goto(await signIn());
  await signedIn(page, origin);
  await page.waitForURL(origin + "/");
  await page.waitForTimeout(1200);
  const hits: Hit[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.origin !== origin ||
      /^\/(?:_next|fonts|brand)\//.test(url.pathname)
    )
      return;
    hits.push({
      path: url.pathname,
      method: request.method(),
      kind: request.resourceType(),
      bucket: bucketFor(url.pathname, request.method()),
      at: Date.now(),
    });
  });
  const measures: Record<string, Hit[]> = {};
  async function measure(name: string, action: () => Promise<unknown>) {
    await page.waitForTimeout(1200);
    hits.length = 0;
    await action();
    await page.waitForTimeout(900);
    measures[name] = [...hits];
  }
  await measure("home", async () => {
    await page.goto(origin);
    await page.locator(".night-tick").first().waitFor();
  });
  await measure("viewer", async () => {
    await page
      .getByRole("link", { name: /Where it came from/ })
      .first()
      .click();
    await page.locator(".trace-node").first().waitFor();
  });
  await measure("peek", () => openPeek(page));
  await measure("stack", async () => {
    await page.goto(origin + "/stack");
    await page.locator(".stack-grid").first().waitFor();
  });
  await writeFile(
    path.join(out, "request-measurements.json"),
    JSON.stringify(measures, null, 2) + "\n",
  );
  await context.close();
  if (!process.env.MDP_RATE_MEASURE_ONLY) {
    await sixTabs(origin, out, signIn, clock);
    await refusals(browser, origin, out, signIn, clock);
  }
  console.log("Request counts saved. Open request-measurements.json.");
}

async function openPeek(page: Page) {
  await page
    .locator('.trace-stage[data-stage="ready"] .trace-peek-button')
    .click();
  await page.locator(".trace-table tbody tr").first().waitFor();
}

async function sixTabs(
  origin: string,
  out: string,
  signIn: () => Promise<string>,
  clock: string,
) {
  const sessions = [];
  const browsers: Browser[] = [];
  const failures: string[] = [];
  const traffic: { path: string; status: number; tab: number }[] = [];
  try {
    for (let person = 0; person < 2; person++) {
      // Two people use separate browsers. Each has three tabs sharing a session.
      // This also keeps local HTTP/1 event streams out of one browser's six-socket pool.
      const browser = await chromium.launch({ headless: true });
      browsers.push(browser);
      const context = await browser.newContext({
        viewport: { width: 390, height: 844 },
      });
      sessions.push(context);
      const login = await context.newPage();
      await pinBrowserClock(login, clock);
      await login.goto(await signIn());
      await signedIn(login, origin);
      await login.waitForURL(origin + "/");
      // Finish sign-in hydration before closing its tab and opening the burst.
      // Closing during chunk downloads can cancel the context's shared requests.
      await login.locator(".night-tick").first().waitFor();
      await login.close();
    }
    const cookies = await Promise.all(
      sessions.map((context) => context.cookies()),
    );
    const ids = cookies.map(
      (values) =>
        values.find((cookie) => cookie.name === "__Host-mdp_showcase")?.value,
    );
    assert(
      ids.every(Boolean) && ids[0] !== ids[1],
      "Use two distinct signed-in sessions.",
    );
    const pages = [];
    for (const context of sessions) {
      for (let tab = 0; tab < 3; tab++) {
        const page = await context.newPage();
        const index = pages.length;
        pages.push(page);
        page.setDefaultTimeout(60000);
        page.setDefaultNavigationTimeout(60000);
        await pinBrowserClock(page, clock);
        page.on("pageerror", (error) => failures.push(error.message));
        page.on("requestfailed", (request) => {
          if (request.failure()?.errorText === "net::ERR_ABORTED") return;
          failures.push(
            `${index}: ${new URL(request.url()).pathname}: ${request.failure()?.errorText}`,
          );
        });
        page.on("response", (response) => {
          const url = new URL(response.url());
          if (
            url.origin !== origin ||
            /^\/(?:_next|fonts|brand)\//.test(url.pathname)
          )
            return;
          traffic.push({
            path: new URL(response.url()).pathname,
            status: response.status(),
            tab: index,
          });
          if (response.status() === 429)
            failures.push(`${index}: ${new URL(response.url()).pathname}`);
        });
      }
    }
    // No pacing between tabs: all share the proxy's default local IP.
    await Promise.all(
      pages.map(async (page, index) => {
        const response = await page.goto(
          `${origin}/?${new URLSearchParams({ trace: "home.follower_exposure_gain", song: keys[0], ranking: "fixture-ranking" })}`,
        );
        assert.equal(response?.status(), 200);
        await page.locator(".trace-node").first().waitFor();
        await openPeek(page);
        const bounds = await page.evaluate(() => [
          document.documentElement.scrollWidth,
          document.documentElement.clientWidth,
        ]);
        assert.equal(
          bounds[0],
          bounds[1],
          "The viewer fits at 390 px. Inspect its preview.",
        );
        await page.screenshot({
          path: path.join(out, `six-tabs-${index}.jpg`),
          type: "jpeg",
          quality: 60,
        });
      }),
    );
    await Promise.all(
      pages.map(async (page) => {
        const status = page
          .waitForResponse(
            (response) => new URL(response.url()).pathname === "/s/stack",
          )
          .then(
            (response) => ({ response }),
            (error: unknown) => ({ error }),
          );
        const response = await page.goto(origin + "/stack");
        assert.equal(response?.status(), 200);
        const result = await status;
        if ("error" in result) throw result.error;
        assert.equal(result.response.status(), 200);
        await page.locator(".stack-grid").first().waitFor();
        const bounds = await page.evaluate(() => [
          document.documentElement.scrollWidth,
          document.documentElement.clientWidth,
        ]);
        assert.equal(
          bounds[0],
          bounds[1],
          "Stack fits at 390 px. Inspect its cards.",
        );
      }),
    );
    assert.deepEqual(
      failures,
      [],
      "Inspect six-tab-traffic.json for refused requests.",
    );
  } catch (error) {
    for (const [index, page] of sessions
      .flatMap((context) => context.pages())
      .entries()) {
      await page
        .screenshot({
          path: path.join(out, `six-tabs-failure-${index}.jpg`),
          type: "jpeg",
          quality: 60,
        })
        .catch(() => {});
    }
    throw error;
  } finally {
    await writeFile(
      path.join(out, "six-tab-traffic.json"),
      JSON.stringify({ failures, traffic }, null, 2) + "\n",
    );
    await Promise.all(sessions.map((context) => context.close()));
    await Promise.all(browsers.map((browser) => browser.close()));
  }
}

async function refusals(
  browser: Browser,
  origin: string,
  out: string,
  signIn: () => Promise<string>,
  clock: string,
) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  try {
    // An invalid session still has a ceiling. No admitted request can read protected data.
    const burst = async (pathname: string) => {
      for (let attempt = 0; attempt < 5; attempt++) {
        const responses = await Promise.all(
          Array.from({ length: 200 }, () =>
            context.request.get(origin + pathname, {
              headers: { "fly-client-ip": "fixture-runaway" },
            }),
          ),
        );
        const refused = responses.find((response) => response.status() === 429);
        if (refused) return refused;
      }
      throw new Error("The loop has no refusal. Check the rate limit.");
    };
    const data = await burst("/s/trace");
    assert.equal(data.headers()["retry-after"], "1");
    const payload = z
      .object({
        error_class: z.literal("showcase_rate_limited"),
        next_step: z.string().min(1),
      })
      .parse(await data.json());
    const document = await burst("/sign-in");
    assert.match(document.headers()["content-type"], /text\/html/);
    const html = await document.text();
    const page = await context.newPage();
    await pinBrowserClock(page, clock);
    let documents = 0;
    await page.route("**/rate-refusal-check", async (route) => {
      documents++;
      await route.fulfill({
        status: 429,
        headers: document.headers(),
        body: html,
      });
    });
    await page.goto(origin + "/rate-refusal-check");
    await expect.poll(() => documents).toBe(2);
    await page.waitForTimeout(1600);
    assert.equal(
      documents,
      2,
      "A refused document retries only once. Use Reload after that.",
    );
    await page.screenshot({
      path: path.join(out, "document-refused.jpg"),
      type: "jpeg",
      quality: 65,
    });
    await page.getByRole("button", { name: "Reload", exact: true }).click();
    await expect.poll(() => documents).toBeGreaterThan(2);
    await page.unroute("**/rate-refusal-check");
    await page.goto(await signIn());
    await signedIn(page, origin);
    await page.waitForURL(origin + "/");
    await expect
      .poll(() =>
        page.evaluate(() => sessionStorage.getItem("showcase-rate-retry")),
      )
      .toBe(null);
    // Reuse the actual proxy JSON to exercise the existing calm states.
    const deny = async (route: import("@playwright/test").Route) =>
      route.fulfill({
        status: 429,
        headers: data.headers(),
        body: await data.text(),
      });
    await page.route("**/s/trace?*", deny);
    await page.goto(
      `${origin}/?${new URLSearchParams({ trace: "home.follower_exposure_gain", song: keys[0], ranking: "fixture-ranking" })}`,
    );
    await page
      .locator(".trace-viewer")
      .getByRole("button", { name: "Retry", exact: true })
      .waitFor();
    await page.screenshot({
      path: path.join(out, "viewer-refused.jpg"),
      type: "jpeg",
      quality: 60,
    });
    await page.unroute("**/s/trace?*");
    await page
      .locator(".trace-viewer")
      .getByRole("button", { name: "Retry", exact: true })
      .click();
    await page.locator(".trace-node").first().waitFor();
    await page.route("**/s/peek?*", deny);
    await page
      .locator('.trace-stage[data-stage="ready"] .trace-peek-button')
      .click();
    await page
      .locator(".trace-peek")
      .getByRole("button", { name: "Retry", exact: true })
      .waitFor();
    await page.screenshot({
      path: path.join(out, "peek-refused.jpg"),
      type: "jpeg",
      quality: 60,
    });
    await page.unroute("**/s/peek?*");
    await page
      .locator(".trace-peek")
      .getByRole("button", { name: "Retry", exact: true })
      .click();
    await page.locator(".trace-table tbody tr").first().waitFor();
    await page.route("**/s/stack", deny);
    await page.goto(origin + "/stack");
    await page.locator(".service-card .status-dot").first().click();
    await page
      .getByRole("dialog")
      .getByText(/Status not checked at/)
      .waitFor();
    await page
      .getByRole("button", { name: "Retry", exact: true })
      .first()
      .waitFor();
    await page.screenshot({
      path: path.join(out, "stack-refused.jpg"),
      type: "jpeg",
      quality: 60,
    });
    await page.route("**/s/night?*", deny);
    await page.goto(origin);
    await page
      .locator(".home-night")
      .getByRole("button", { name: "Night details" })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Retry", exact: true })
      .first()
      .waitFor();
    await page.screenshot({
      path: path.join(out, "home-refused.jpg"),
      type: "jpeg",
      quality: 60,
    });
    await writeFile(
      path.join(out, "refusals.json"),
      JSON.stringify(
        {
          status: 429,
          retry_after: data.headers()["retry-after"],
          payload,
          automatic_document_retries: 1,
          client_retry_states: ["viewer", "peek", "stack", "home"],
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await context.close();
  }
}

async function signedIn(page: Page, origin: string) {
  // ClearLink removes the token only after hydration. A click before that sends
  // the native form with Origin: null, which the app correctly refuses.
  await page.waitForFunction(
    () => !new URL(location.href).searchParams.has("t"),
  );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(origin + "/");
}
