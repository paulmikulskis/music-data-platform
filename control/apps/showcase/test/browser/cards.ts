// Synthetic cards with deterministic identifiers and artwork responses.
import assert from "node:assert/strict";
import path from "node:path";
import type postgres from "postgres";
import type { Page } from "@playwright/test";
import { density, densityPolicy } from "./density.mjs";
import { arrivals, at, build, days, earlySignals, movers } from "./fixtures";

type Database = ReturnType<typeof postgres>;
const today = at.slice(0, 10);
const dayOffset = (offset: number) =>
  new Date(Date.parse(today + "T00:00:00Z") + offset * 86400000)
    .toISOString()
    .slice(0, 10);
// Every production card carries its table's whole upstream list, Bandcamp and Billboard included.
const tableWide = JSON.stringify([
  "am_playlist",
  "bc_daily_list",
  "bc_discover",
  "billboard_hot100",
  "mb_spine",
  "sp_playlist",
  "sz_chart",
]);
export const fixtureSong = "00000000-0000-4000-b000-000000000501";
const fixtureSpotifySong = "spotify:FixtureTrack0000000501";
const members = [fixtureSong, fixtureSpotifySong];
const methods = ["isrc_crosswalk", "title_artist_duration"];
const locator = (
  component: string,
  relation: string,
  row_key: Record<string, unknown>,
) => ({
  component,
  relation,
  row_key,
  window: { start: dayOffset(-1), end: today },
  cluster_key: fixtureSong,
  member_song_keys: members,
  cluster_methods: methods,
  cluster_confidence: 1,
  input_build: build(relation),
});
const add = {
  stream: "head",
  variant: "US",
  platform: "spotify",
  event_type: "add",
  playlist_id: "FixtureList00000000501",
};
const mover = {
  ...movers[0],
  song_key: fixtureSong,
  title_text: "Fixture Song Azure",
  artist_text: "Fixture Duo",
  reason_rule: "Rising: playlist entries and Shazam spread over 3 days.",
  source_keys: tableWide,
  score_parts: JSON.stringify([
    { component: "follower_exposure_gain", value: 1200, window_days: 3 },
    { component: "playlist_adds", value: 3, window_days: 3 },
    { component: "shazam_spread_gain", value: 2, window_days: 1 },
  ]),
  evidence: JSON.stringify([
    locator("follower_exposure_gain", "mart_playlist_events", add),
    locator("playlist_adds", "mart_playlist_events", add),
    locator("shazam_spread_gain", "mart_shazam_chart_daily", {
      chart: "shazam:top-200:mexico",
      position: 130,
      chart_date: today,
    }),
  ]),
};
// The Apple copy charts in two cities today; the Spotify copy took the editorial add.
const history = (key: string, title = "Fixture Song Azure") =>
  days.map((row, i) => ({
    ...row,
    song_key: key,
    title_text: title,
    artist_text: "Fixture Duo",
    editorial_adds: key === fixtureSpotifySong && i === days.length - 1 ? 1 : 0,
    shazam_cities: key === fixtureSong ? (i === days.length - 1 ? "2" : "0") : "0",
    playlist_followers: key === fixtureSpotifySong ? "1800" : "0",
  }));
// The data API answers for this mode: one mover, its matched group and each copy's days.
export function cardsDataApi(
  name: string,
  input: { filters?: Record<string, unknown> },
) {
  const filters = input.filters ?? {};
  if (name === "mart_top_movers_current")
    return !filters.song_key || members.includes(String(filters.song_key))
      ? [mover]
      : [];
  if (name === "mart_song_cluster_members") {
    const group = members.map((song_key) => ({
      song_key,
      cluster_key: fixtureSong,
      representative_song_key: fixtureSong,
      cluster_methods: JSON.stringify(methods),
      learning_eligible: false,
      resale_permitted: false,
      source_keys: tableWide,
    }));
    return group.filter(
      (row) =>
        row.song_key === filters.song_key ||
        row.representative_song_key === filters.representative_song_key,
    );
  }
  // Each card song's own days; any other key, such as one left percent-encoded, has none.
  if (name === "mart_song_day") {
    const key = String(filters.song_key);
    const card = [...catalogSongs, ...risingSongs].find(
      ([song]) => song === key,
    );
    return members.includes(key)
      ? history(key)
      : card
        ? history(key, card[1])
        : [];
  }
  return null;
}
const shazamSeen = (country: string) => ({
  component: "arrivals",
  relation: "mart_shazam_chart_daily",
  row_key: { chart: `shazam:top-200:${country}`, chart_date: today },
  window: { start: dayOffset(-1), end: today },
  input_build: build("mart_shazam_chart_daily"),
});
const catalogSongs: [string, string, string][] = [
  ["apple:9000000501", "Fixture Song Amber", "brazil"],
  ["apple:9000000502", "Fixture Song Blue", "germany"],
  ["apple:9000000503", "Fixture Song Coral", "united-kingdom"],
  ["apple:9000000504", "Fixture Song Dawn", "united-states"],
];
const risingSongs: [string, string, string, string][] = [
  [
    "00000000-0000-4000-b000-000000000502",
    "Fixture Song D",
    "apple",
    "9000000505",
  ],
  [
    "spotify:FixtureTrack0000000502",
    "Fixture Song Echo",
    "spotify",
    "FixtureTrack0000000502",
  ],
  [
    "spotify:FixtureTrack0000000503",
    "Fixture Song Fern",
    "spotify",
    "FixtureTrack0000000503",
  ],
];
// The second cover fails in the browser, to show the placeholder instead of a broken image.
const refused = `**/art/${encodeURIComponent("apple:9000000502")}**`;
export async function seedCards(wh: Database) {
  const catalog = arrivals.find(
    (row) => row.movement_list === "catalog_entries",
  );
  const early = earlySignals.find((row) => row.family === "playlists");
  assert(catalog && early);
  await wh`DELETE FROM marts.mart_arrivals_current WHERE movement_list IN ('established_entries','catalog_entries')`;
  await wh`DELETE FROM marts.mart_early_signals_current`;
  for (const [index, [song_key, title_text, country]] of catalogSongs.entries())
    await wh`INSERT INTO marts.mart_arrivals_current ${wh({
      ...catalog,
      song_key,
      rank: String(index + 1),
      title_text,
      artist_text: "Catalog artist",
      source_keys: tableWide,
      chart_spread_gain: "1",
      market_count: "1",
      markets: JSON.stringify([country]),
      evidence: JSON.stringify([shazamSeen(country)]),
    })}`;
  for (const [index, [song_key, title_text]] of risingSongs.entries())
    await wh`INSERT INTO marts.mart_early_signals_current ${wh({
      ...early,
      song_key,
      rank: String(index + 1),
      title_text,
      source_keys: tableWide,
      evidence: JSON.stringify([
        {
          ...shazamSeen("united-states"),
          component: "playlist_adds",
          relation: "mart_playlist_events",
          row_key: {
            platform: index === 0 ? "apple_music" : "spotify",
            playlist_id: `list-${index}`,
            event_type: "add",
          },
        },
      ]),
    })}`;
  const copies = [
    [fixtureSong, "apple", "9000000500"],
    [fixtureSpotifySong, "spotify", "FixtureTrack0000000501"],
    ...catalogSongs.map(([key]) => [key, "apple", key.slice(6)]),
    ...risingSongs.map(([key, , platform, id]) => [key, platform, id]),
  ];
  for (const [song_key, platform, platform_track_id] of copies)
    await wh`INSERT INTO intermediate.int_song_key__daily ${wh({
      song_key,
      platform,
      platform_track_id,
      resolved: platform === "apple",
      source_keys: tableWide,
    })}`;
  for (const song_key of members)
    await wh`INSERT INTO intermediate.int_song_cluster__daily ${wh({
      song_key,
      cluster_key: fixtureSong,
      cluster_methods: JSON.stringify(methods),
      cluster_confidence: 1,
      member_song_keys: JSON.stringify(members),
      source_keys: tableWide,
    })}`;
  await wh`INSERT INTO marts.mart_search_index ${wh({
    object_key: `song:${fixtureSong}`,
    kind: "song",
    display_text: "Fixture Song Azure",
    context: JSON.stringify({
      key: fixtureSong,
      subtitle: "Fixture Duo",
      art_song: fixtureSong,
    }),
    aliases: "[]",
    last_seen: at,
    learning_eligible: false,
    resale_permitted: false,
    source_keys: tableWide,
  })}`;
  // In this fixture: the copies table has no stamp yet, so no card offers Call it.
  await wh`DELETE FROM marts._build WHERE relation = 'intermediate.int_song_key__daily'`;
}
// Every cover on screen has settled, and none shows the browser's broken image.
async function coversSettled(page: Page, scope: string) {
  await page.locator(`${scope} .art`).first().waitFor();
  await page.waitForFunction(
    (scope) =>
      [...document.querySelectorAll(`${scope} .art`)].every((art) =>
        ["loaded", "failed"].includes(art.getAttribute("data-art") ?? ""),
      ),
    scope,
    { timeout: 30000 },
  );
  const broken = await page.evaluate(
    (scope) =>
      [...document.querySelectorAll<HTMLImageElement>(`${scope} img`)].filter(
        (img) =>
          img.complete &&
          img.naturalWidth === 0 &&
          Number(getComputedStyle(img).opacity) > 0,
      ).length,
    scope,
  );
  assert.equal(broken, 0, `${scope} shows no broken image`);
}
// The app admits ten requests a second per session; each load waits out the last one.
async function open(page: Page, url: string) {
  await page.waitForTimeout(1100);
  await page.goto(url);
}
async function reload(page: Page) {
  await page.waitForTimeout(1100);
  await page.reload();
}
// A cropped screen of one element, centered so the fixed view bar stays clear of it.
async function shot(page: Page, selector: string, file: string, nth = 0) {
  const target = page.locator(selector).nth(nth);
  await target.evaluate((element) =>
    element.scrollIntoView({ block: "center" }),
  );
  await page.waitForTimeout(250);
  await target.screenshot({ path: file });
}
export async function cardsWalk(
  page: Page,
  origin: string,
  out: string,
  size: string,
  measurements: Record<string, ReturnType<typeof density>>,
) {
  await page.route("**/art/**", route => route.fulfill({contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#23343d"/></svg>'}));
  const phone = size.startsWith("390");
  const file = (name: string) => path.join(out, `${name}-${size}.png`);
  // Home: a hard load. The cover is real, and the badges name only Spotify and Shazam.
  await open(page, origin + "/today");
  await coversSettled(page, ".mover");
  assert.equal(
    await page.locator(".mover .art").getAttribute("data-art"),
    "loaded",
  );
  assert.deepEqual(
    await page
      .locator(".mover-face .source-badge")
      .evaluateAll((badges) =>
        badges.map((badge) => badge.getAttribute("aria-label")),
      ),
    ["Spotify. Open source", "Shazam. Open source"],
  );
  if (phone)
    measurements["cards-home"] = await page.evaluate(density, densityPolicy);
  await shot(page, ".mover", file("home-mover"));
  await page.getByRole("button", { name: "Behind this card" }).click();
  await page
    .getByText("2 copies matched: same ISRC, same title and artist.")
    .waitFor();
  await page.waitForTimeout(900);
  await shot(page, ".mover", file("home-behind"));
  await page.getByRole("button", { name: "Back to the card" }).click();
  await page.getByRole("button", { name: /See why/ }).click();
  await page.getByText("Added to 1 playlist over 3 days.").waitFor();
  await shot(page, ".mover", file("home-why"));
  // The song page agrees with the card: one add, two cities, the same copy count.
  await open(page, origin + `/s/song/${fixtureSong}`);
  await coversSettled(page, ".song-room");
  await page
    .locator(".found-on")
    .getByText("Same song on 2 platforms")
    .waitFor();
  assert.equal(await page.getByText("one copy found").count(), 0);
  assert.equal(
    (await page.locator(".lane-value").allInnerTexts()).slice(0, 2).join(" | "),
    "1 playlist | 2 cities",
  );
  if (phone)
    measurements["cards-song"] = await page.evaluate(density, densityPolicy);
  // The copies settle into one cover in 0.9 seconds.
  await page.waitForTimeout(1200);
  await shot(page, ".song-room", file("song-header"));
  await page.getByRole("button", { name: "Open song identity" }).click();
  await page
    .locator("dialog .found-on")
    .getByText("2 copies matched: same ISRC, same title and artist.")
    .waitFor();
  await page.waitForTimeout(300);
  await shot(page, "dialog[open]", file("song-identity"));
  await page.getByRole("button", { name: "Close", exact: true }).click();
  // Places on a hard load and a reload: Apple-only songs get Apple covers.
  await open(page, origin + "/songs?view=places");
  await reload(page);
  await coversSettled(page, ".places-room");
  assert.equal(
    await page.locator('.places-room .art[data-art="loaded"]').count(),
    catalogSongs.length,
  );
  if (phone)
    measurements["cards-places"] = await page.evaluate(density, densityPolicy);
  await shot(page, ".places-room .song-card", file("places-card"));
  // A cover that fails before the page is interactive keeps the monogram, never a broken image.
  await page.route(refused, (route) =>
    route.fulfill({ status: 404, body: "Artwork unavailable. Open /rising." }),
  );
  await reload(page);
  await coversSettled(page, ".places-room");
  const missing = page.locator(".places-room .song-card").nth(1);
  assert.equal(
    await missing.locator(".art").getAttribute("data-art"),
    "failed",
  );
  await shot(page, ".places-room .song-card", file("places-missing-cover"), 1);
  await page.unroute(refused);
  // A catalog card's Apple key has a colon: its page reads that key's days and shows its cover.
  const [catalogKey, catalogTitle] = catalogSongs[0]!;
  await page.waitForTimeout(1100);
  await page
    .locator(".places-room h2 a", { hasText: catalogTitle })
    .first()
    .click();
  await page.waitForURL(
    (url) => url.pathname === `/s/song/${encodeURIComponent(catalogKey)}`,
  );
  await page
    .locator(".song-room h1")
    .filter({ hasText: catalogTitle })
    .waitFor();
  await coversSettled(page, ".song-room");
  assert.equal(
    await page.locator(".song-room .art").first().getAttribute("data-art"),
    "loaded",
    "A colon-key song page shows its cover.",
  );
  assert.equal(
    await page.getByText("song not found.", { exact: true }).count(),
    0,
  );
  await shot(page, ".song-room", file("song-colon-key"));
  // Rising's early cards: Apple and Spotify covers, badges from each card's own evidence.
  await open(page, origin + "/rising");
  await coversSettled(page, ".rising-room");
  assert.equal(
    await page.locator('.rising-room .art[data-art="loaded"]').count(),
    risingSongs.length + 1,
  );
  if (phone)
    measurements["cards-rising"] = await page.evaluate(density, densityPolicy);
  await shot(page, ".early-feed .song-card", file("rising-card-apple-copy"));
  await shot(page, ".early-feed .song-card", file("rising-card-spotify"), 1);
  // Search result tiles.
  await open(page, origin + "/today");
  await page
    .getByRole("button", { name: "Search the Library", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Library", exact: true });
  await dialog.getByRole("searchbox").fill("Fixture Song Azure");
  const tile = dialog.locator(".search-result").first();
  await tile.getByText("Fixture Song Azure").waitFor();
  await page.waitForFunction(() => {
    const face = document.querySelector(".search-result .search-face");
    return face?.getAttribute("data-art") === "loaded";
  });
  await shot(page, ".search-results", file("search"));
  await page.keyboard.press("Escape");
  // Picks: no card can be called yet, so the empty week says why and where calls open.
  await open(page, origin + "/picks");
  await page
    .getByText("Calls open on Home after the next daily read.", {
      exact: false,
    })
    .waitFor();
  if (phone)
    measurements["cards-picks"] = await page.evaluate(density, densityPolicy);
  await shot(page, ".calls-room .empty", file("picks-empty"));
}
