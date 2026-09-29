import assert from "node:assert/strict";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import { expect, type Page, type Request } from "@playwright/test";
import { syntheticSources } from "../synthetic-fixture";
import { traceView, type TraceView } from "../../lib/trace";
import { localTime } from "../../lib/local-time";
import { chains } from "../../components/lineage/layout";
import { closeDialog, closeDialogs } from "./dialog";
import { overlay, densityPolicy } from "./density.mjs";
import { keys } from "./fixtures";

export async function viewerReadersWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
) {
  const readings: Record<string, unknown> = {};
  const checkCountedDetails = async (
    node: TraceView["nodes"][number],
    name: string,
  ) => {
    assert(node.count, "Use a step with a dated exact count.");
    const peekRequests: string[] = [];
    const record = (request: Request) => {
      if (new URL(request.url()).pathname === "/s/peek")
        peekRequests.push(request.url());
    };
    page.on("request", record);
    await page
      .locator("dialog[open]")
      .last()
      .getByRole("button", { name: "Counted at Ready to use" })
      .click();
    const details = page.getByRole("dialog", { name: node.label, exact: true });
    const zone = await page.evaluate(
      () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    );
    const now = await page.evaluate(() => new Date().toISOString());
    await expect(
      details.getByText(
        `${BigInt(node.count.value).toLocaleString("en-US")} stored ${localTime(node.count.captured_at, true, new Date(now), zone)}`,
        { exact: true },
      ),
    ).toBeVisible();
    await expect(details.locator("time")).toHaveAttribute(
      "datetime",
      node.count.captured_at,
    );
    await expect(
      page.getByRole("complementary", { name: "Table preview" }),
    ).toHaveCount(0);
    assert.equal(
      peekRequests.length,
      0,
      "Opening the count uses the saved details without a preview read.",
    );
    page.off("request", record);
    const measured = await page.evaluate(overlay, {
      kind: "sheet",
      policy: densityPolicy,
    });
    assert(
      measured.words <= 80 + measured.detailWords && !measured.banned.length,
      JSON.stringify(measured),
    );
    const bounds = await details.evaluate((element) => ({
      scroll: element.scrollWidth,
      client: element.clientWidth,
    }));
    assert.equal(bounds.scroll, bounds.client);
    readings[name] = {
      ...measured,
      ...bounds,
      count: node.count.value,
      captured_at: node.count.captured_at,
      preview: node.preview,
    };
    if (node.preview) {
      await expect(
        details.getByRole("button", { name: "See rows" }),
      ).toBeVisible();
    } else {
      await expect(
        details.getByRole("button", { name: "See rows" }),
      ).toHaveCount(0);
      await expect(
        details.getByText("Records are not previewed here.", { exact: true }),
      ).toBeVisible();
      await expect(
        details.getByRole("link", { name: "Source details" }),
      ).toHaveAttribute("href", "/sources");
      await expect(details.getByRole("link")).toHaveCount(1);
      await expect(details.getByRole("button")).toHaveCount(1); // Close is the only button.
    }
    await page.screenshot({
      path: path.join(
        out,
        `viewer-counted-${name}-${width}x${width === 390 ? 844 : 900}.jpg`,
      ),
      type: "jpeg",
      quality: 65,
    });
    if (node.preview) {
      await details.getByRole("button", { name: "See rows" }).click();
      await expect(
        page.getByRole("complementary", { name: "Table preview" }),
      ).toBeVisible();
      await expect(page.locator(".trace-table tbody tr").first()).toBeVisible();
      await page.getByRole("button", { name: "Close preview" }).click();
    } else {
      await closeDialog(page);
      await expect(
        page.getByRole("dialog", { name: "Data source viewer", exact: true }),
      ).toBeVisible();
    }
  };
  for (const source of ["am_playlist", "bc_daily_list", "am_playlist_weekly"]) {
    await closeDialogs(page);
    await page.waitForTimeout(1100);
    const response = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/s/trace",
    );
    await page.goto(`${origin}/?trace=source.${source}`);
    const view = traceView.parse(await (await response).json());
    const reader = view.nodes.find((node) => node.id === `fn:${source}`)!;
    const recorded = syntheticSources.sources.find(
      (item) => item.source_key === source,
    )!;
    assert.equal(reader.enabled, recorded.enabled);
    assert.equal(!!reader.last_read, !!recorded.evidence?.last_success);
    await page.locator(".trace-node").first().waitFor();
    const countedPath = chains(view).findIndex((path) =>
      path.includes("rel:marts.mart_playlist_profile"),
    );
    assert(countedPath >= 0, "Open the path to the counted playlist step.");
    await page
      .getByRole("combobox", { name: "Choose a source path" })
      .selectOption(String(countedPath));
    for (const stage of ["source", "readers", "collected"]) {
      if (width === 1440) {
        await page.locator(`[data-stage="${stage}"] .trace-node`).hover();
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
            hover.numbers <= 4 + hover.detailNumbers &&
            !hover.banned.length,
          JSON.stringify(hover),
        );
        readings[`${source}-${stage}-hover`] = hover;
      }
      await page.locator(`[data-stage="${stage}"] .trace-node-action`).click();
      const card = page.locator("dialog[open]").last();
      if (reader.last_read) {
        const zone = await page.evaluate(
          () => Intl.DateTimeFormat().resolvedOptions().timeZone,
        );
        const now = await page.evaluate(() => new Date().toISOString());
        await expect(
          card.getByText(
            `Last read ${localTime(reader.last_read, false, new Date(now), zone)}`,
            { exact: false },
          ),
        ).toBeVisible();
        await expect(
          card.locator("time").filter({ hasText: /./ }).first(),
        ).toHaveAttribute("datetime", reader.last_read);
      } else {
        await expect(
          card.getByText("Last read: not checked", { exact: false }),
        ).toBeVisible();
      }
      if (!recorded.enabled)
        await expect(
          card.getByText("Switched off", { exact: false }),
        ).toBeVisible();
      const measured = await page.evaluate(overlay, {
        kind: "sheet",
        policy: densityPolicy,
      });
      assert(
        measured.words <= 80 + measured.detailWords && !measured.banned.length,
        JSON.stringify(measured),
      );
      const bounds = await card.evaluate((element) => ({
        scroll: element.scrollWidth,
        client: element.clientWidth,
      }));
      assert.equal(bounds.scroll, bounds.client);
      readings[`${source}-${stage}`] = { ...measured, ...bounds };
      if (stage === "collected") {
        await expect(
          card.getByRole("button", { name: "Counted at Ready to use" }),
        ).toBeVisible();
        await page.screenshot({
          path: path.join(
            out,
            `viewer-${source}-${width}x${width === 390 ? 844 : 900}.jpg`,
          ),
          type: "jpeg",
          quality: 65,
        });
        const counted = view.nodes.find(
          (node) => node.id === "rel:marts.mart_playlist_profile",
        )!;
        await checkCountedDetails(counted, `${source}-profile`);
      } else {
        await closeDialog(page);
      }
    }
    await page.mouse.move(0, 0);
    await expect(page.locator(".hover-card")).toHaveCount(0);
    const bounds = await page.locator(".trace-viewer").evaluate((element) => ({
      scroll: element.scrollWidth,
      client: element.clientWidth,
    }));
    assert.equal(bounds.scroll, bounds.client);
    const documentBounds = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    assert.equal(documentBounds.scroll, documentBounds.client);
    const copy = await page.evaluate(overlay, {
      kind: "sheet",
      policy: densityPolicy,
    });
    assert(
      copy.words <= 80 + copy.detailWords && !copy.banned.length,
      JSON.stringify(copy),
    );
    readings[`${source}-viewer`] = {
      ...bounds,
      document: documentBounds,
      copy,
    };
    await page.screenshot({
      path: path.join(
        out,
        `viewer-path-${source}-${width}x${width === 390 ? 844 : 900}.jpg`,
      ),
      type: "jpeg",
      quality: 65,
    });
  }
  await closeDialogs(page);
  const response = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/s/trace",
  );
  await page.goto(
    `${origin}/?${new URLSearchParams({ trace: "home.playlist_adds", song: keys[0], ranking: "fixture-ranking" })}`,
  );
  const view = traceView.parse(await (await response).json());
  const counted = view.nodes.find(
    (node) => node.id === "rel:marts.mart_playlist_events",
  )!;
  assert(counted?.count, "The Home playlist path has a counted event step.");
  assert.equal(counted.preview, false);
  await page
    .locator(
      '[data-stage="ready"] [data-node="rel:marts.mart_playlist_events"]',
    )
    .waitFor();
  await expect(
    page.locator('[data-stage="ready"] .trace-peek-button'),
  ).toHaveCount(0);
  await page.locator('[data-stage="collected"] .trace-node-action').click();
  await checkCountedDetails(counted, "playlist-events");
  await closeDialogs(page);
  await writeFile(
    path.join(out, `viewer-readers-${width}.json`),
    JSON.stringify(readings, null, 2) + "\n",
  );
}
