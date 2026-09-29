import { withServerTime } from "./clock";
import { closeDialog, closeDialogs } from "./dialog";
import assert from "node:assert/strict";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import { expect, type Page } from "@playwright/test";
import { traceView, peekView } from "../../lib/trace";
import { keys } from "./fixtures";
import { overlay, densityPolicy } from "./density.mjs";
export async function lineageWalk(
  page: Page,
  origin: string,
  out: string,
  initialWidth: number,
) {
  const capture = async (name: string) =>
    page.screenshot({
      path: path.join(out, `${name}-${initialWidth}.jpg`),
      type: "jpeg",
      quality: 60,
    });
  const open = async (entry: string, ranking = "fixture-ranking") => {
    await closeDialogs(page);
    await page.waitForTimeout(1100);
    await page.goto(
      `${origin}/?${new URLSearchParams({ trace: entry, song: keys[0], ranking })}`,
    );
    await page.locator(".trace-node").first().waitFor();
  };
  await closeDialogs(page);
  await page.goto(origin);
  const scripts = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((name) => /\.js(?:\?|$)/.test(name)),
  );
  for (const script of scripts) {
    const response = await page.request.get(script);
    assert(
      !(await response.text()).includes("Choose a source path"),
      "The graph loads only when the viewer opens.",
    );
  }
  const paths: string[][] = [];
  for (const component of ["playlist_adds", "follower_exposure_gain"]) {
    await open(`home.${component}`);
    paths.push(
      await page
        .locator(".trace-node.is-lit")
        .evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute("data-node") ?? ""),
        ),
    );
    const copy = await page.evaluate(overlay, {
      kind: "sheet",
      policy: densityPolicy,
    });
    assert(
      copy.words <= 80 + copy.detailWords && !copy.banned.length,
      JSON.stringify(copy),
    );
    await capture(`viewer-${component}`);
    assert(
      await page.locator(".trace-node .provider-mark").count(),
      "The provider mark sits inside its node.",
    );
    const reader = await page
      .locator('[data-stage="readers"] .trace-node')
      .innerText();
    assert.match(reader, /[Rr]eader/);
    assert.match(reader, /after .* (?:UTC|EDT|EST|GMT)/);
    if (initialWidth === 390) {
      await page
        .locator('[data-stage="screen"] .trace-node')
        .scrollIntoViewIfNeeded();
      await capture(`viewer-${component}-number`);
    }
  }
  assert.notDeepEqual(paths[0], paths[1], "Two facts take their own paths.");
  const node = page.locator('.trace-stage[data-stage="ready"] .trace-node');
  await node.scrollIntoViewIfNeeded();
  if (initialWidth === 1440) {
    await node.hover();
    await page.locator("[data-popover]").waitFor();
    await expect
      .poll(() =>
        page.evaluate(overlay, { kind: "hover", policy: densityPolicy }),
      )
      .toMatchObject({ found: true });
    const hover = await page.evaluate(overlay, {
      kind: "hover",
      policy: densityPolicy,
    });
    assert(
      hover.words <= 30 + hover.detailWords &&
        hover.numbers <= 4 + hover.detailNumbers,
      JSON.stringify(hover),
    );
    await capture("viewer-hover");
    await page.mouse.move(0, 0);
  } else {
    await node.click();
    await page.locator("dialog[open]").last().waitFor();
    await capture("viewer-tap");
    await closeDialog(page);
  }
  await page
    .locator('.trace-stage[data-stage="ready"] .trace-peek-button')
    .click();
  await page.locator(".trace-table tbody tr").waitFor();
  await capture("viewer-peek");
  const previewCopy = await page.locator(".trace-peek").innerText();
  assert(previewCopy.split(/\s+/).filter(Boolean).length <= 80, previewCopy);
  const body = await page.locator(".trace-viewer").innerText();
  const html = await page
    .locator(".trace-viewer")
    .evaluate((element) => element.outerHTML);
  assert(
    !/personal-text-sentinel|creator-sentinel|commenter-sentinel|tenant-sentinel/.test(
      html,
    ),
  );
  assert(
    !/personal-text-sentinel|creator-sentinel|commenter-sentinel|tenant-sentinel/.test(
      body,
    ),
  );
  assert(
    !/1 of 1/.test(body),
    "A filtered peek has no full-table denominator.",
  );
  await page.locator(".peek-workbench > summary").click();
  const workbench = await page
    .getByRole("link", { name: "Open in Workbench" })
    .getAttribute("href");
  assert(workbench);
  if (await page.getByText("Code", { exact: true }).count()) {
    await page.getByText("Code", { exact: true }).click();
    assert.match(
      (await page
        .getByRole("link", { name: "Open code" })
        .getAttribute("href")) ?? "",
      /\/blob\/[a-f0-9]{40}\//,
    );
    await capture("viewer-private-code");
  }
  await page.locator(".peek-workbench").evaluate((element) => {
    if (element instanceof HTMLDetailsElement) element.open = true;
  });
  await page.getByRole("link", { name: "Open in Workbench" }).click();
  await page.locator("#prefill-sql").waitFor();
  assert.match(
    await page.locator("#prefill-sql").inputValue(),
    /mart_playlist_profile/,
  );
  await capture("viewer-workbench-landing");
  await withServerTime(page, async () => {
    await page
      .getByRole("button", { name: "Start session with this query" })
      .click();
  });
  await page.locator("#wb-sql").waitFor();
  assert.match(await page.locator("#wb-sql").inputValue(), /trace-snapshot/);
  await page.getByRole("link", { name: "Back to showcase" }).click();
  await page.locator(".trace-node").first().waitFor();
  assert.equal(
    new URL(page.url()).searchParams.get("trace"),
    "home.follower_exposure_gain",
  );
  await page.waitForTimeout(1100);
  await closeDialogs(page);
  await page.goto(origin + workbench + "&test_outage=1");
  await page
    .getByRole("heading", { name: "Console is unavailable." })
    .waitFor();
  assert.match(
    await page.getByRole("textbox", { name: "Query for later" }).inputValue(),
    /mart_playlist_profile/,
  );
  await capture("viewer-workbench-unavailable");
  await open("home.shazam_spread_gain");
  const chartRead = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/s/peek",
  );
  await page.locator('[data-stage="ready"] .trace-peek-button').click();
  const chartResult = peekView.parse(await (await chartRead).json());
  assert.equal(chartResult.rows.length, 1);
  assert.equal(chartResult.rows[0]?.title_text, "Night tide");
  assert.equal(chartResult.rows[0]?.position, 1);
  await page.locator(".trace-table").waitFor();
  await capture("viewer-shazam-row");
  await page.route("**/s/peek?**", async (route) => {
    const response = await route.fetch();
    const value = peekView.parse(await response.json());
    await route.fulfill({
      json: {
        ...value,
        cache_state: "cached",
        saved_at: "2026-09-27T06:12:00.000Z",
        workbench: null,
      },
    });
  });
  await open("home.follower_exposure_gain");
  await page.locator('[data-stage="ready"] .trace-peek-button').click();
  await page.getByText("Cached · as of", { exact: false }).waitFor();
  await capture("viewer-peek-cached");
  await page.unroute("**/s/peek?**");
  await open("home.playlist_adds", "gone");
  assert.equal(await page.locator(".trace-node.is-lit").count(), 0);
  await capture("viewer-missing-evidence");
  await expect(
    page.locator('[data-stage="ready"] .trace-peek-button'),
  ).toHaveCount(0);
  await page.locator('[data-stage="ready"] .trace-node-action').click();
  await page
    .getByText("Records are not previewed here.", { exact: true })
    .waitFor();
  await capture("viewer-no-preview");
  await closeDialogs(page);
  await page.goto(origin + "/?trace=unknown");
  await page.getByText("No path for that link.", { exact: false }).waitFor();
  await capture("viewer-unknown");
  await page.route("**/s/peek?**", (route) =>
    route.fulfill({ status: 503, body: "{}" }),
  );
  await open("home.follower_exposure_gain");
  await page
    .locator('.trace-stage[data-stage="ready"] .trace-peek-button')
    .click();
  await page.getByText("Peek is taking too long.", { exact: false }).waitFor();
  await capture("viewer-peek-timeout");
  await page.unroute("**/s/peek?**");
  await page.route("**/s/peek?**", (route) =>
    route.fulfill({
      json: {
        state: "changed",
        cache_state: "live",
        saved_at: null,
        columns: [],
        rows: [],
        total: null,
        captured_at: null,
        sql: null,
        workbench: null,
      },
    }),
  );
  await open("home.follower_exposure_gain");
  await page
    .locator('.trace-stage[data-stage="ready"] .trace-peek-button')
    .click();
  await page.getByText("This reading changed.", { exact: false }).waitFor();
  await capture("viewer-reading-changed");
  await page.unroute("**/s/peek?**");
  await page.route("**/s/trace?**", (route) =>
    route.fulfill({ status: 503, body: "{}" }),
  );
  await closeDialogs(page);
  await page.goto(origin + "/?trace=source.sp_playlist");
  await page
    .getByText("Saved details are unavailable.", { exact: false })
    .waitFor();
  await capture("viewer-read-unavailable");
  await page.unroute("**/s/trace?**");
  await page.route("**/s/trace?**", async (route) => {
    const result = await route.fetch();
    const data = traceView.parse(await result.json());
    await route.fulfill({
      json: {
        ...data,
        nodes: data.nodes.map((node) => ({ ...node, count: null })),
      },
    });
  });
  await open("home.follower_exposure_gain");
  await page.locator('.trace-stage[data-stage="ready"] .trace-node').click();
  await page
    .locator("dialog[open]")
    .last()
    .getByText("Count not checked.")
    .waitFor();
  await capture("viewer-count-unavailable");
  await closeDialog(page);
  await page.unroute("**/s/trace?**");
  await closeDialogs(page);
  if (initialWidth !== 1440) return;
  // Capture real fixture responses once. Layout measurements reuse those same
  // facts; the integration checks above and the load harness use live requests.
  const savedTraces = new Map<string, ReturnType<typeof traceView.parse>>();
  await page.route("**/s/trace?**", async (route) => {
    const key = new URL(route.request().url()).search;
    let data = savedTraces.get(key);
    if (!data) {
      const result = await route.fetch();
      assert(result.ok(), "Capture the fixture trace before measuring layout.");
      data = traceView.parse(await result.json());
      savedTraces.set(key, data);
    }
    await route.fulfill({ json: data });
  });
  const measurements = [];
  for (const width of [768, 360, 390, 430, 1024, 1280, 1440, 1920])
    for (const zoom of [1, 1.25, 1.5]) {
      await page.setViewportSize({ width, height: 900 });
      for (const entry of [
        "home.playlist_adds",
        "home.follower_exposure_gain",
        "home.shazam_spread_gain",
        "home.stream_rate_gain",
        "source.sp_playlist",
      ]) {
        await closeDialogs(page);
        const loaded = page.waitForResponse((response) => {
          const url = new URL(response.url());
          return (
            url.pathname === "/s/trace" &&
            url.searchParams.get("entry") === entry
          );
        });
        // Next observes native history changes. Keep this document and its loaded
        // fonts while mounting each trace, instead of reloading Home 120 times.
        await page.evaluate(
          ({ entry, song }) => {
            history.pushState(
              null,
              "",
              `/?${new URLSearchParams({ trace: entry, song, ranking: "fixture-ranking" })}`,
            );
          },
          { entry, song: keys[0] },
        );
        await loaded;
        await page.locator(".trace-node").first().waitFor();
        await page.evaluate((zoom) => {
          document.documentElement.style.zoom = String(zoom);
        }, zoom);
        const sheet = await page.locator(".trace-viewer").boundingBox();
        assert(
          sheet && sheet.x >= 0 && sheet.x + sheet.width <= width + 1,
          JSON.stringify({ width, zoom, entry, sheet }),
        );
        const result = await page.locator(".trace-node").evaluateAll((nodes) =>
          nodes.map((node) => {
            const rect = node.getBoundingClientRect();
            return {
              id: node.getAttribute("data-node"),
              fits: node.scrollWidth <= node.clientWidth,
              font: parseFloat(getComputedStyle(node).fontSize),
              width: rect.width,
              height: rect.height,
              left: rect.left,
              right: rect.right,
              top: rect.top,
              bottom: rect.bottom,
            };
          }),
        );
        assert(result.length > 0);
        for (const node of result)
          assert(
            node.fits &&
              node.font >= 12 &&
              node.width >= 44 &&
              node.height >= 44,
            JSON.stringify({ width, zoom, entry, node }),
          );
        const vertical = await page
          .locator(".trace-graph")
          .evaluate(
            (graph) => getComputedStyle(graph).flexDirection === "column",
          );
        for (let i = 1; i < result.length; i++)
          assert(
            vertical
              ? result[i - 1].bottom <= result[i].top
              : result[i - 1].right <= result[i].left,
            "Node boxes do not overlap.",
          );
        if (vertical) {
          for (const node of result)
            assert(
              node.left >= 0 && node.right <= width,
              "Every phone step fits without sideways scrolling.",
            );
        }
        for (const node of await page.locator(".trace-node").all()) {
          await node.scrollIntoViewIfNeeded();
          await node.hover();
          const card = page.locator(".trace-viewer .hover-card");
          await card.waitFor();
          await expect
            .poll(() =>
              page.evaluate(overlay, { kind: "hover", policy: densityPolicy }),
            )
            .toMatchObject({ found: true });
          const box = await card.boundingBox();
          const trigger = await node.boundingBox();
          assert(box && trigger);
          assert(
            box.x >= 11 &&
              box.y >= 11 &&
              box.x + box.width <= width - 11 &&
              box.y + box.height <= 889,
            JSON.stringify({ width, zoom, entry, box }),
          );
          assert(
            box.y + box.height <= trigger.y ||
              box.y >= trigger.y + trigger.height,
            "A hover keeps its node visible.",
          );
          const words = await page.evaluate(overlay, {
            kind: "hover",
            policy: densityPolicy,
          });
          assert(
            words.words <= 30 + words.detailWords &&
              words.numbers <= 4 + words.detailNumbers,
            JSON.stringify({ entry, words }),
          );
          await page.mouse.move(0, 0);
          await card.waitFor({ state: "hidden" });
        }
        measurements.push({ width, zoom, entry, nodes: result.length });
        if (entry === "source.sp_playlist")
          console.log(`Viewer layout ${width}px at ${zoom * 100}% passes.`);
      }
    }
  await writeFile(
    path.join(out, "lineage-layout.json"),
    JSON.stringify(measurements),
  );
  await page.unroute("**/s/trace?**");
  await page.evaluate(() => {
    document.documentElement.style.zoom = "1";
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  const headers = {
    cookie: (await page.context().cookies())
      .map((item) => `${item.name}=${item.value}`)
      .join("; "),
  };
  const json = await page.request.get(
    origin +
      `/s/trace?entry=home.playlist_adds&song=${keys[0]}&ranking=fixture-ranking`,
    { headers },
  );
  const tracePayload: unknown = await json.json();
  assert(
    !/personal-text-sentinel|creator-sentinel|commenter-sentinel|tenant-sentinel/.test(
      JSON.stringify(tracePayload),
    ),
  );
  traceView.parse(tracePayload);
  const preview = await page.request.get(
    origin + "/s/peek?relation=marts.mart_playlist_profile",
    { headers },
  );
  const peekPayload: unknown = await preview.json();
  assert(
    !/personal-text-sentinel|creator-sentinel|commenter-sentinel|tenant-sentinel/.test(
      JSON.stringify(peekPayload),
    ),
  );
  const safe = peekView.parse(peekPayload);
  const invalid = await page.request.get(
    origin + "/s/peek?relation=tenant_sentinel_marts.private",
    { headers },
  );
  assert.equal(peekView.parse(await invalid.json()).state, "unavailable");
  assert(safe.rows.length <= 5);
  assert(
    !/personal-text-sentinel|creator-sentinel|commenter-sentinel/.test(
      JSON.stringify(safe),
    ),
  );
  await closeDialogs(page);
}
