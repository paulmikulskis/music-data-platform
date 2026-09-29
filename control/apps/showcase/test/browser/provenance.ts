import assert from "node:assert/strict";
import path from "node:path";
import type { Browser, BrowserContext, Page } from "@playwright/test";
import { overlay, densityPolicy } from "./density.mjs";
import { cycle } from "./fixtures";
export type Overlay = ReturnType<typeof overlay> & {
  kind: "popover" | "sheet";
};
// Artist photos come from Wikimedia in production; the browser check answers with a marked test image.
export async function routeArtistPhotos(page: Page) {
  await page.route("**/artist-photo/**", (route) =>
    route.request().url().endsWith("/credit")
      ? route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            credit: {
              author: "Test photographer",
              license: "CC BY-SA 4.0",
              license_url: "https://creativecommons.org/licenses/by-sa/4.0/",
              page: "https://commons.wikimedia.org/wiki/File:Test.jpg",
            },
          }),
        })
      : route.fulfill({
          contentType: "image/svg+xml",
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><rect width="240" height="240" fill="#354341"/><circle cx="120" cy="95" r="48" fill="#68b9c2" opacity=".7"/><rect x="50" y="160" width="140" height="80" rx="40" fill="#68b9c2" opacity=".5"/><text x="18" y="30" fill="#edf4f6" font-family="sans-serif" font-size="14" letter-spacing="3">TEST PHOTO</text></svg>',
        }),
  );
}
// Every hover card and sheet from the "where it came from" layer, with its screen and word counts.
export async function provenanceWalk(
  page: Page,
  origin: string,
  out: string,
  size: string,
  song: string,
  overlays: Record<string, Overlay>,
) {
  const shot = (name: string) =>
    page.screenshot({ path: path.join(out, `${name}-${size}.png`) });
  const measure = async (name: string, kind: "popover" | "sheet") => {
    overlays[`${name}-${size}`] = {
      kind,
      ...(await page.evaluate(overlay, { kind: kind, policy: densityPolicy })),
    };
    assert(overlays[`${name}-${size}`]!.found, `${name} opens its ${kind}.`);
  };
  const away = () => page.mouse.move(2, 2);
  const close = () =>
    page.getByRole("button", { name: "Close", exact: true }).last().click();
  await routeArtistPhotos(page);
  // Home: a source badge, its card, the artist card and the path behind the card.
  await page.goto(origin + "/today");
  await page.waitForTimeout(1200);
  const card = page.locator(".mover").first();
  await card.locator(".source-badge").first().hover();
  await page.locator(".hover-card .hover-line").waitFor();
  await page.waitForTimeout(250);
  await shot("hover-source");
  await measure("hover-source", "popover");
  await card.locator(".source-badge").first().click();
  await page.getByText("See source details →", { exact: true }).waitFor();
  await page.waitForTimeout(300);
  await shot("source-card");
  await measure("source-card", "sheet");
  await close();
  await page.goto(origin + "/s/proof/source/sp_playlist_weekly?from=%2Ftoday");
  await page.getByText(/Overdue · see details/).waitFor();
  await page.getByText(/20 playlists tracked at/).waitFor();
  await shot("source-weekly-overdue");
  await page.goto(origin + "/today");
  await card.waitFor();
  await away();
  await card.locator(".artist-trigger").hover();
  await page.locator(".hover-card .credit").waitFor();
  await page.waitForTimeout(300);
  await shot("hover-artist");
  await measure("hover-artist", "popover");
  await away();
  await card.getByRole("button", { name: "Behind this card" }).click();
  await page.locator(".behind li.here").waitFor();
  await page.waitForTimeout(900);
  await shot("behind-card");
  await measure("behind-card", "popover");
  await card.getByRole("button", { name: "Back to the card" }).click();
  // Keyboard only: Tab to the artist, Enter opens its card as a sheet, Tab reaches the photo credit
  // and Open artist, Escape closes it and focus returns to the artist.
  await page.goto(origin + "/today");
  await page.waitForTimeout(1200);
  const focused = () =>
    page.evaluate(() => ({
      text: document.activeElement?.textContent?.trim() ?? "",
      artist:
        !!document.activeElement?.closest(".artist-trigger") &&
        document.activeElement?.tagName === "BUTTON",
    }));
  let presses = 0;
  while (!(await focused()).artist) {
    assert(++presses < 40, "Tab reaches the artist.");
    await page.keyboard.press("Tab");
  }
  await page.keyboard.press("Enter");
  await page.locator("dialog[open] .artist-card .credit").waitFor();
  const reach = async (text: RegExp) => {
    for (let n = 0; n < 12; n++) {
      if (text.test((await focused()).text)) return;
      await page.keyboard.press("Tab");
    }
    assert.fail(`Tab reaches ${text}.`);
  };
  await reach(/^Photo: /);
  await page.waitForTimeout(250);
  await shot("keyboard-artist");
  await measure("artist-sheet", "sheet");
  await reach(/^Open artist/);
  await page.keyboard.press("Escape");
  await page
    .locator("dialog[open]")
    .waitFor({ state: "detached" })
    .catch(() => undefined);
  assert.equal(
    await page.locator("dialog[open]").count(),
    0,
    "Escape closes the artist sheet.",
  );
  assert((await focused()).artist, "Focus returns to the artist.");
  // Song: found-on badges, the city map on the Cities lane, and How we know on a lane value.
  await page.goto(origin + `/s/song/${encodeURIComponent(song)}`);
  await page.waitForTimeout(1300);
  await page
    .locator(".song-room > .found-on")
    .getByText("Same song on 3 platforms")
    .waitFor();
  await shot("song-found-on");
  await page
    .getByRole("button", { name: "Open song identity", exact: true })
    .click();
  const identityProof = page.getByRole("link", {
    name: "See the entries →",
    exact: true,
  });
  const songPath = `/s/song/${encodeURIComponent(song)}`;
  assert.equal(
    await identityProof.getAttribute("href"),
    `/s/proof/cycle/${cycle}?relation=marts.mart_song_day&song=${encodeURIComponent(song)}&from=${encodeURIComponent(songPath)}`,
    "Song identity keeps the displayed song's captured cycle and names the song.",
  );
  await shot("song-identity");
  await measure("song-identity", "sheet");
  await identityProof.click();
  // The summary counts the song's days read and returns to the song.
  await page
    .locator(".proof-room h1")
    .filter({ hasText: /days? read\.$/ })
    .waitFor();
  assert.equal(
    await page
      .locator(".proof-actions")
      .getByRole("link", { name: "Back", exact: true })
      .getAttribute("href"),
    songPath,
  );
  await shot("proof-song-days");
  await page.goto(origin + `/s/song/${encodeURIComponent(song)}`);
  // The Places sheet carries the Shazam map; the Cities lane counts one day.
  await page.getByRole("button", { name: "Places →", exact: true }).click();
  await page.locator("dialog[open] .city-map").waitFor();
  await page.waitForTimeout(600);
  await shot("places-map");
  await measure("places-map", "sheet");
  await close();
  await page.getByRole("slider", { name: "Scrub day" }).fill("12");
  await page.getByRole("button", { name: "Open Shazam cities mark" }).click();
  await page.locator("dialog[open] .metric-button").hover();
  await page.locator("dialog[open] .hover-card .how-card").waitFor();
  await page.waitForTimeout(250);
  await shot("hover-how-song");
  await measure("hover-how-song", "popover");
  await page.locator("dialog[open] .metric-button").click();
  await page
    .getByRole("link", { name: "See the entries →", exact: true })
    .waitFor();
  await page.waitForTimeout(700);
  await shot("how-sheet-song");
  await measure("how-sheet-song", "sheet");
  await close();
  await close();
  // Holdings: How we know on the week's collection, then the tending counters.
  await page.goto(origin + "/holdings");
  await page.waitForTimeout(1200);
  await page.locator(".holdings-numbers .metric-button").first().hover();
  await page.locator(".hover-card .how-card").waitFor();
  await page.waitForTimeout(250);
  await shot("hover-how");
  await measure("hover-how", "popover");
  await page.locator(".holdings-numbers .metric-button").first().click();
  await page
    .getByRole("link", { name: "Collection by day →", exact: true })
    .waitFor();
  await page.waitForTimeout(300);
  await shot("how-sheet");
  await measure("how-sheet", "sheet");
  await close();
  await away();
  await page.locator(".tending").scrollIntoViewIfNeeded();
  await page.waitForTimeout(900);
  await page.locator(".tending-counter").first().hover();
  await page.locator(".hover-card .hover-line").waitFor();
  await page.waitForTimeout(250);
  await shot("tending");
  await measure("tending", "popover");
  await away();
  // Places: the markets a catalog song reached, on a small map.
  await page.goto(origin + "/songs?view=places");
  await page.waitForTimeout(1200);
  await page
    .getByRole("button", { name: "Reached 3 new markets.", exact: true })
    .hover();
  await page.locator(".hover-card .city-map").waitFor();
  await page.waitForTimeout(500);
  await shot("hover-markets");
  await measure("hover-markets", "popover");
  await away();
  await page
    .getByRole("button", { name: "Reached 3 new markets.", exact: true })
    .click();
  await page.getByText("Where this came from", { exact: true }).waitFor();
  await page.waitForTimeout(300);
  await shot("places-card-source");
  await measure("places-card-source", "sheet");
  await close();
}
// A short recording of a hover pass across one card: badge, artist, the path behind it.
export async function recordHover(
  browser: Browser,
  storageState: Awaited<ReturnType<BrowserContext["storageState"]>>,
  origin: string,
  out: string,
) {
  const viewport = { width: 1440, height: 900 };
  const recording = await browser.newContext({
    viewport,
    storageState,
    recordVideo: { dir: path.join(out, "video"), size: viewport },
  });
  const page = await recording.newPage();
  await routeArtistPhotos(page);
  await page.route("**/art/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><rect width="800" height="800" fill="#a9c885"/><circle cx="350" cy="300" r="190" fill="#23343d"/><text x="50" y="65" fill="#edf4f6">TEST ART</text></svg>',
    }),
  );
  await page.goto(origin + "/today");
  await page.waitForTimeout(1400);
  const card = page.locator(".mover").first();
  for (const badge of await card.locator(".source-badge").all()) {
    await badge.hover();
    await page.waitForTimeout(900);
  }
  await card.locator(".artist-trigger").hover();
  await page.waitForTimeout(1300);
  await page.mouse.move(2, 2, { steps: 8 });
  await card.getByRole("button", { name: "Behind this card" }).click();
  await page.waitForTimeout(1800);
  const video = page.video();
  await recording.close();
  await video?.saveAs(path.join(out, "hover-card.webm"));
}
// On a phone, press and hold opens the hover card as a bottom sheet, and the tap after it does nothing.
export async function pressAndHold(
  browser: Browser,
  storageState: Awaited<ReturnType<BrowserContext["storageState"]>>,
  origin: string,
  out: string,
  overlays: Record<string, Overlay>,
) {
  const phone = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    storageState,
  });
  const page = await phone.newPage();
  await routeArtistPhotos(page);
  await page.goto(origin + "/today");
  await page.waitForTimeout(1400);
  // A touch pointer held past the long-press delay, then lifted.
  const badge = page.locator(".mover .source-badge").first();
  const box = await badge.boundingBox();
  assert(box);
  const at = {
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
    pointerType: "touch",
    isPrimary: true,
    pointerId: 7,
    bubbles: true,
  };
  await badge.dispatchEvent("pointerdown", at);
  await page.waitForTimeout(700);
  await badge.dispatchEvent("pointerup", at);
  await badge.dispatchEvent("click", at);
  await page
    .locator("dialog[open] [data-popover] .hover-line")
    .waitFor({ timeout: 5000 })
    .catch(async (error: unknown) => {
      await page.screenshot({ path: path.join(out, "press-hold-failure.png") });
      throw error;
    });
  assert.equal(
    await page.getByText("See source details →", { exact: true }).count(),
    0,
    "A long press opens the hover card, not the source card.",
  );
  await page.screenshot({ path: path.join(out, "press-hold-390x844.png") });
  overlays["press-hold-390x844"] = {
    kind: "popover",
    ...(await page.evaluate(overlay, {
      kind: "popover",
      policy: densityPolicy,
    })),
  };
  await phone.close();
}
