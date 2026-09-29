import assert from "node:assert/strict";
import path from "node:path";
import { expect, type Page } from "@playwright/test";
import { marks } from "@mdp/contracts/marks";
import { functionFlow } from "@mdp/contracts/flows";
import { closeDialogs } from "./dialog";
import { syntheticSources } from "../synthetic-fixture";
import { traceView } from "../../lib/trace";

// run.ts supplies the saved production-shaped sources and seedLineage's local tables.
export async function stageMarksWalk(
  page: Page,
  origin: string,
  out: string,
  width: number,
) {
  const capture = async (name: string) => {
    await page.screenshot({
      path: path.join(out, `stage-${name}-${width}.jpg`),
      type: "jpeg",
      quality: 65,
    });
  };
  const fits = async () => {
    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    assert.equal(
      dimensions.scroll,
      dimensions.client,
      "Keep the page within the viewport. Open the stage screenshot.",
    );
    for (const dialog of await page.locator("dialog[open]").all()) {
      const size = await dialog.evaluate((node) => ({
        scroll: node.scrollWidth,
        client: node.clientWidth,
      }));
      assert.equal(
        size.scroll,
        size.client,
        "Keep the card within the viewport. Open the stage screenshot.",
      );
    }
  };
  const marksAndSentences = async (selector: string) => {
    const nodes = page.locator(selector);
    assert(await nodes.count(), "Show collection steps. Open Sources.");
    for (const node of await nodes.all()) {
      assert(
        await node.locator("img,.monogram").count(),
        "Give each step a mark. Open the registry.",
      );
      assert(
        (await node.getAttribute("data-hover-sentence"))?.trim(),
        "Give each step a sentence. Open the vocabulary.",
      );
      for (const img of await node.locator("img").all()) {
        await expect(img).toHaveJSProperty("complete", true);
        assert(
          await img.evaluate(
            (image) =>
              image instanceof HTMLImageElement && image.naturalWidth > 0,
          ),
        );
      }
    }
    for (const [key, mark] of Object.entries(marks)) {
      if (key === mark.label) continue;
      await expect(
        page
          .locator(".provider-mark > span")
          .filter({ hasText: new RegExp(`^${key}$`) }),
      ).toHaveCount(0);
    }
  };
  const reviewedSentences = async () => {
    for (const name of await page.locator(".function-name").all()) {
      const key = await name.getAttribute("data-function");
      const flow = key ? functionFlow(key) : null;
      assert(flow, "Use a reviewed function. Open docs/sources/flows.csv.");
      const sentence = await name.locator(":scope > span").innerText();
      const normalized =
        sentence.split(/(?:When enabled|Once ready): /).at(-1) ?? sentence;
      const reviewed = flow.card.what
        .split("{count} ")
        .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
        .join("(?:[\\d,]+ )?");
      assert.match(
        normalized,
        new RegExp(`^${reviewed}$`),
        "Show the reviewed sentence. Open the function card.",
      );
      assert(flow.card.what.split(/\s+/).length <= 45);
    }
  };
  const cardLinks = async () => {
    const card = page.locator("[data-function-card]:visible").last();
    assert.deepEqual(await card.locator("dt").allTextContents(), [
      "How often",
      "What it reads",
      "Why it matters",
      "Ideas",
    ]);
    await expect(card.getByText("Ideas", { exact: true })).toBeVisible();
    await expect(card.locator("a[href]").first()).toBeVisible();
    await expect(card.locator(".link-preview")).toHaveCount(0);
    assert(await card.locator("li").count());
    for (const idea of await card.locator("li").all())
      assert((await idea.innerText()).startsWith("Could"));
  };
  await page.goto(origin + "/sources");
  await page.locator(".flow-node").first().waitFor();
  await fits();
  await marksAndSentences(".flow-node");
  await reviewedSentences();
  for (const mark of await page
    .locator(".reviewed-source .flow-node .provider-mark")
    .all()) {
    const aligned = await mark.evaluate((node) => {
      const picture = node
        .querySelector("img,.monogram")
        ?.getBoundingClientRect();
      const label = node
        .querySelector(":scope > span:last-child")
        ?.getBoundingClientRect();
      return (
        !!picture &&
        !!label &&
        node.getBoundingClientRect().height >= 44 &&
        label.left >= picture.right &&
        Math.abs(
          label.top + label.height / 2 - picture.top - picture.height / 2,
        ) < 1
      );
    });
    assert(
      aligned,
      "Keep each flow label beside its mark and its link at least 44 px tall. Open the Sources screenshot.",
    );
  }
  await capture("sources");
  const more = page.getByRole("button", { name: "Show all sources" });
  if (await more.count()) await more.click();
  await page
    .getByRole("button", { name: "About Spotify playlists and charts", exact: true })
    .click();
  await cardLinks();
  await fits();
  await capture("playlist-card");
  await closeDialogs(page);
  await page.goto(origin + "/sources?credits=1");
  await expect(
    page.getByRole("dialog", { name: "Credits", exact: true }),
  ).toBeVisible();
  await fits();
  for (const owner of ["MetaBrainz", "R Foundation", "Wikimedia Foundation"])
    await expect(
      page.getByRole("dialog").getByText(owner, { exact: false }).first(),
    ).toBeVisible();
  await capture("credits");
  await closeDialogs(page);
  await page.goto(origin + "/");
  await page.locator(".home-sources .flow-node").first().waitFor();
  await fits();
  await marksAndSentences(".home-sources .flow-node");
  await reviewedSentences();
  await page
    .getByRole("button", { name: "Spotify. Open source", exact: true })
    .first()
    .click();
  const spotify = syntheticSources.sources.find(
    (source) => source.source_key === "sp_playlist",
  );
  const spotifyFlow = functionFlow("sp_playlist");
  assert(spotify?.tracked && spotifyFlow);
  await expect(page.locator("[data-function-description]")).toHaveText(
    spotifyFlow.card.what.replace("{count}", String(spotify.tracked.count)),
  );
  await fits();
  await capture("spotify-sheet");
  await closeDialogs(page);
  await page.goto(origin + "/?trace=source.sp_playlist");
  await page.locator(".trace-node").first().waitFor();
  await fits();
  await marksAndSentences(".trace-node");
  await capture("lineage");
  await page.locator('[data-stage="readers"] .trace-node-action').click();
  await cardLinks();
  await fits();
  await expect(page.locator("[data-function-card]:visible")).toHaveCount(1);
  await closeDialogs(page);
  await page.waitForURL((url) => !url.searchParams.has("trace"));
  for (const key of [
    "bc_discover",
    "bc_daily_list",
    "bc_radio",
  ]) {
    await page.goto(origin + `/?trace=source.${key}`);
    const reader = page.locator('[data-stage="readers"] .trace-node');
    await reader.waitFor();
    await expect(reader.locator(".function-name > span")).toContainText(
      "Switched off. Waiting on",
    );
    await expect(reader.locator(".function-name > span")).toContainText(
      "When enabled:",
    );
    await expect(reader.locator(".function-name small")).toContainText(
      "Last read",
    );
    await expect(
      reader.getByText("Runs on the platform's servers", { exact: true }),
    ).toHaveCount(0);
    await fits();
    await capture(`disabled-${key}`);
    await closeDialogs(page);
    await page.waitForURL((url) => !url.searchParams.has("trace"));
  }
  await page.route("**/s/trace?*", async (route) => {
    const response = await route.fetch();
    const view = traceView.parse(await response.json());
    await route.fulfill({
      response,
      json: {
        ...view,
        nodes: view.nodes.map((node) =>
          node.source === "bc_discover"
            ? { ...node, enabled: false, paused: true }
            : node,
        ),
      },
    });
  });
  await page.goto(origin + "/?trace=source.bc_discover");
  const paused = page.locator('[data-stage="readers"] .trace-node');
  await paused.waitFor();
  await expect(paused.locator(".function-name > span")).toContainText(
    "Paused. Waiting on",
  );
  await expect(paused.locator(".function-name small")).toContainText(
    "Last read",
  );
  await expect(
    paused.getByText("Runs on the platform's servers", { exact: true }),
  ).toHaveCount(0);
  await fits();
  await capture("paused-reader");
  await closeDialogs(page);
  await page.waitForURL((url) => !url.searchParams.has("trace"));
  await page.unroute("**/s/trace?*");
}
