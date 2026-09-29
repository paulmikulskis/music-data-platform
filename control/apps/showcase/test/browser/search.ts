import assert from "node:assert/strict";
import path from "node:path";
import type postgres from "postgres";
import type { Page } from "@playwright/test";
import { overlay, densityPolicy } from "./density.mjs";
import { routeArtistPhotos, type Overlay } from "./provenance";
import { searchResponse } from "../../lib/search";
import { callSnapshot } from "../../lib/calls";
import { signCall } from "../../server/call-token";
import { keys, at, build, artistIdentity } from "./fixtures";

// A chart song known only by its Apple copy: its key has a colon, which every link encodes.
const colonSong = {
  id: "9000000504",
  key: "apple:9000000504",
  title: "Tokyo song 2",
};

type Database = ReturnType<typeof postgres>;
async function photoReady(page: Page, selector: string) {
  await page.locator(selector).waitFor();
  await page.waitForFunction((selector) => {
    const image = document.querySelector(selector);
    return (
      image instanceof HTMLImageElement &&
      image.complete &&
      image.naturalWidth > 0
    );
  }, selector);
}
export async function seedSearch(wh: Database) {
  await wh.unsafe(`CREATE EXTENSION IF NOT EXISTS pg_trgm;
    DROP TABLE IF EXISTS marts.mart_search_index CASCADE;
    CREATE TABLE marts.mart_search_index(object_key text primary key,kind text,display_text text,context text,aliases text,last_seen timestamp,learning_eligible boolean,resale_permitted boolean,source_keys text);
    CREATE INDEX ON marts.mart_search_index USING gin(lower(display_text) gin_trgm_ops);
    ALTER TABLE marts.mart_search_index OWNER TO dbt_transform;`);
  const rows = [
    {
      object_key: `song:${keys[0]}`,
      kind: "song",
      display_text: "Night tide",
      context: JSON.stringify({
        key: keys[0],
        subtitle: "Test recording",
        art_song: keys[0],
      }),
    },
    {
      object_key: "artist:fixture-artist",
      kind: "artist",
      display_text: "Night artist",
      context: JSON.stringify({
        key: "fixture-artist",
        platform: "spotify",
        artist_id: artistIdentity.platform_artist_id,
        wikidata_qid: artistIdentity.wikidata_qid,
      }),
    },
    {
      object_key: "playlist:fixture-list",
      kind: "playlist",
      display_text: "Night playlist",
      context: JSON.stringify({
        key: "spotify:fixture-list",
        platform: "spotify",
        playlist_id: "fixture-list",
      }),
    },
    {
      object_key: "chart:fixture-chart",
      kind: "chart",
      display_text: "Night city chart",
      context: JSON.stringify({
        key: "shazam:city:US:fixture",
        city: "Test city",
        country: "US",
      }),
    },
  ];
  for (let index = 0; index < 16; index++) {
    rows.push({
      object_key: `playlist:collection-${index}`,
      kind: "playlist",
      display_text: "Night collection",
      context: JSON.stringify({
        key: `spotify:collection-${index}`,
        platform: "spotify",
        playlist_id: `collection-${index}`,
      }),
    });
  }
  // Synthetic near-matches exercise exact and fuzzy search ordering.
  const song = (key: string, title: string, artist: string) => ({
    object_key: `song:${key}`,
    kind: "song",
    display_text: title,
    context: JSON.stringify({ key, subtitle: artist, art_song: key }),
  });
  rows.push(
    song("fixture-tokyo-quiet", "tokyo", "Fixture Artist A"),
    song("fixture-tokyo-drift", "TOKYO FIXTURE", "Fixture Artist B"),
    song("fixture-tokyo-live", "Fixture Tokyo", "Fixture Artist C"),
    song("fixture-artist-a", "Fixture Song A", "fixture_artist"),
    song("fixture-tool-song", "Toko", "Toko"),
    song("fixture-tko-song", "tko", "tko"),
    {
      object_key: "artist:fixture-tool",
      kind: "artist",
      display_text: "Tolo",
      context: JSON.stringify({
        key: "fixture-tool",
        platform: "spotify",
        artist_id: "fixture-tool",
      }),
    },
    {
      object_key: "artist:fixture-tokyo-artist",
      kind: "artist",
      display_text: "Tokyo Fixture Ensemble",
      context: JSON.stringify({
        key: "fixture-tokyo-artist",
        platform: "spotify",
        artist_id: "fixture-tokyo-artist",
      }),
    },
    {
      object_key: "chart:shazam:top-50:japan:tokyo",
      kind: "chart",
      display_text: "tokyo Shazam top-50",
      context: JSON.stringify({
        key: "shazam:top-50:japan:tokyo",
        country: "JP",
        city: "tokyo",
      }),
    },
    {
      object_key: "playlist:spotify:37i9dQZF1DX4JAvHpjipBk",
      kind: "playlist",
      display_text: "New Music Friday",
      context: JSON.stringify({
        key: "spotify:37i9dQZF1DX4JAvHpjipBk",
        platform: "spotify",
        playlist_id: "37i9dQZF1DX4JAvHpjipBk",
      }),
    },
  );
  for (const row of rows) {
    await wh`INSERT INTO marts.mart_search_index ${wh({ ...row, aliases: "[]", last_seen: at, learning_eligible: false, resale_permitted: false, source_keys: '["sp_playlist"]' })}`;
  }
  // Songs with something on their page in the last 28 days rank before quiet ones.
  await wh.unsafe(`INSERT INTO marts.mart_song_day(song_key, day, title_text, artist_text, stream_rate, shazam_cities)
    VALUES ('fixture-tokyo-live', CURRENT_DATE, 'Fixture Tokyo', 'Fixture Artist C', 1200, 3),
      ('fixture-artist-a', CURRENT_DATE, 'Fixture Song A', 'fixture_artist', 12500, 15),
      ('fixture-tokyo-quiet', CURRENT_DATE, 'tokyo', 'Fixture Artist A', NULL, 0)`);
  // Library sheets read the playlist's profile and newest entries, and the chart's top songs.
  await wh.unsafe(`INSERT INTO intermediate.int_song_key__daily(platform, platform_track_id, resolved, song_key, source_keys, title_text, artist_text)
    VALUES ('spotify', '1FixtureTrack000000001A', true, 'fixture-artist-a', '["sp_playlist"]', 'Fixture Song A', 'fixture_artist'),
      ('spotify', '1tokyolivefixture0000a', true, 'fixture-tokyo-live', '["sp_playlist"]', 'Fixture Tokyo', 'Fixture Artist C'),
      ('apple', '9990001', true, 'fixture-tokyo-live', '["sz_chart"]', 'Fixture Tokyo', 'Fixture Artist C'),
      -- An Apple copy with no matched song keeps its colon key, as most chart songs in production do.
      ('apple', '${colonSong.id}', false, '${colonSong.key}', '["sz_chart"]', '${colonSong.title}', 'Fixture artist');
    UPDATE intermediate.int_song_key__daily SET title_text = CASE song_key WHEN '${keys[0]}' THEN 'Night tide'
      WHEN '${keys[1]}' THEN 'Slow return' WHEN '${keys[2]}' THEN 'After the rain'
      ELSE initcap(replace(song_key, '-', ' ')) END, artist_text = 'Test recording' WHERE title_text IS NULL;
    DROP TABLE IF EXISTS marts.mart_playlist_profile, marts.mart_playlist_events CASCADE;
    CREATE TABLE marts.mart_playlist_profile(platform text, playlist_id text, variant text, stream text, owner_class text, observed_at timestamp,
      snapshot_id text, title text, description text, owner_id text, owner_name text, followers bigint, follower_change bigint, track_count_reported bigint,
      learning_eligible boolean, resale_permitted boolean, source_keys text);
    CREATE TABLE marts.mart_playlist_events(platform text, playlist_id text, variant text, stream text, occurrence_key text, interval_id text,
      event_type text, is_baseline boolean, platform_track_id text, observed_at timestamp, position bigint, owner_class text,
      learning_eligible boolean, resale_permitted boolean, source_keys text);
    INSERT INTO marts.mart_playlist_profile(platform, playlist_id, variant, stream, owner_class, observed_at, title, owner_name, followers, track_count_reported)
    VALUES ('spotify', '37i9dQZF1DX4JAvHpjipBk', 'US', 'head', 'editorial', now() - interval '3 hours', 'New Music Friday', 'Spotify', 4636710, 100),
      ('spotify', 'fixture-list', 'US', 'head', 'editorial', now() - interval '3 hours', 'Night playlist', 'Spotify', 812000, 50);
    INSERT INTO marts.mart_playlist_events(platform, playlist_id, variant, stream, occurrence_key, interval_id, event_type, is_baseline, platform_track_id, observed_at, position, owner_class)
    VALUES ('spotify', '37i9dQZF1DX4JAvHpjipBk', 'US', 'head', 'a', 'i', 'entered_head', false, '1FixtureTrack000000001A', now() - interval '3 hours', 1, 'editorial'),
      ('spotify', '37i9dQZF1DX4JAvHpjipBk', 'US', 'head', 'b', 'i', 'entered_head', false, '1tokyolivefixture0000a', now() - interval '3 hours', 2, 'editorial'),
      ('spotify', '37i9dQZF1DX4JAvHpjipBk', 'US', 'head', 'c', 'i', 'left_head', false, '1tokyolivefixture0000b', now() - interval '3 hours', 3, 'editorial');
    INSERT INTO marts.mart_shazam_chart_daily(chart, chart_date, chart_type, country, city, position, apple_song_id, source_keys, title_text, artist_text)
    SELECT 'shazam:top-50:japan:tokyo', CURRENT_DATE, 'top-50', 'JP', 'tokyo', n,
      CASE n WHEN 1 THEN '9990001' WHEN 2 THEN '${colonSong.id}' ELSE 'fixture-' || n END, '["sz_chart"]',
      CASE WHEN n = 1 THEN 'Fixture Tokyo' ELSE 'Tokyo song ' || n END, 'Fixture artist'
    FROM generate_series(1, 50) n;
    ALTER TABLE marts.mart_playlist_profile OWNER TO dbt_transform;
    ALTER TABLE marts.mart_playlist_events OWNER TO dbt_transform;
    GRANT SELECT ON marts.mart_playlist_profile, marts.mart_playlist_events TO showcase_wh;`);
  await wh`INSERT INTO marts._build(relation,cycle_id,close_no,built_at) SELECT relation, ${build("x").cycle_id}, 41, ${at} FROM unnest(ARRAY['marts.mart_playlist_profile','marts.mart_playlist_events']) relation ON CONFLICT (relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,close_no=EXCLUDED.close_no,built_at=EXCLUDED.built_at`;
  await wh`INSERT INTO marts._build(relation,cycle_id,close_no,built_at) VALUES ('marts.mart_search_index',${build("x").cycle_id},41,${at}) ON CONFLICT (relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,close_no=EXCLUDED.close_no,built_at=EXCLUDED.built_at`;
}
// No Library result, sheet or song list sends a viewer to the operator console.
async function noEngineRoom(page: Page) {
  const hrefs = await page
    .locator("dialog[open] a")
    .evaluateAll((links) =>
      links.map((link) => link.getAttribute("href") ?? ""),
    );
  assert(
    hrefs.every((href) => !/^\/(explorer|functions|runs|ops)\b/.test(href)),
    `Library links stay in the showcase: ${hrefs.join(", ")}`,
  );
}
async function results(
  page: Page,
  input: ReturnType<Page["getByRole"]>,
  text: string,
) {
  const response = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/library/search" &&
      new URL(r.url()).searchParams.get("q") === text,
  );
  await input.fill(text);
  return searchResponse.parse(await (await response).json()).rows;
}
// Playlist, chart and ranking checks against production-shaped names.
async function libraryViews(
  page: Page,
  origin: string,
  out: string,
  size: string,
  overlays: Record<string, Overlay>,
) {
  await page.goto(origin + "/library");
  const dialog = page.getByRole("dialog", { name: "Library", exact: true });
  const input = dialog.getByRole("searchbox");
  await input.waitFor();
  const tokyo = (await results(page, input, "tokyo")).map(
    (row) => row.display_text,
  );
  for (const noise of ["Tolo", "Toko", "tko"])
    assert(
      !tokyo.includes(noise),
      `"tokyo" leaves out ${noise}: ${tokyo.join(", ")}`,
    );
  assert(
    tokyo.indexOf("Fixture Tokyo") < tokyo.indexOf("tokyo") &&
      tokyo.indexOf("tokyo") >= 0,
    `A song with something on its page ranks before a quiet exact title: ${tokyo.join(", ")}`,
  );
  await dialog.locator(".search-result").first().waitFor();
  await page.screenshot({ path: path.join(out, `library-tokyo-${size}.png`) });
  await dialog
    .getByRole("button", { name: "Open Tokyo · Shazam top 50", exact: true })
    .click();
  const chart = page.getByRole("dialog", { name: "Chart", exact: true });
  await chart.getByRole("heading", { name: "Tokyo · Shazam top 50" }).waitFor();
  await chart.getByText("Top of the chart", { exact: true }).waitFor();
  await chart.getByRole("link", { name: "Fixture Tokyo", exact: true }).waitFor();
  await noEngineRoom(page);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(out, `library-chart-${size}.png`) });
  overlays[`library-chart-${size}`] = {
    kind: "sheet",
    ...(await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })),
  };
  await chart.getByRole("button", { name: "Close", exact: true }).click();
  const typo = await results(page, input, "my body isnt ready");
  assert.equal(
    typo[0]?.display_text,
    "Fixture Song A",
    "Typos still find real titles.",
  );
  const lists = await results(page, input, "new music friday");
  assert.equal(lists[0]?.display_text, "New Music Friday");
  await dialog
    .getByRole("button", { name: "Open New Music Friday", exact: true })
    .click();
  const playlist = page.getByRole("dialog", { name: "Playlist", exact: true });
  await playlist.getByText("4,636,710", { exact: true }).waitFor();
  await playlist.getByText("Spotify · by Spotify", { exact: true }).waitFor();
  const newest = playlist.getByRole("link", {
    name: "Fixture Song A",
    exact: true,
  });
  assert.equal(await newest.getAttribute("href"), "/s/song/fixture-artist-a");
  await noEngineRoom(page);
  await page.screenshot({
    path: path.join(out, `library-playlist-${size}.png`),
  });
  overlays[`library-playlist-${size}`] = {
    kind: "sheet",
    ...(await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })),
  };
  await playlist.getByRole("button", { name: "Close", exact: true }).click();
  // A chart song with a colon key opens its own page: the key is decoded once, so the page reads
  // that key's days and asks for its cover, never a doubly encoded one.
  await results(page, input, "tokyo");
  await dialog
    .getByRole("button", { name: "Open Tokyo · Shazam top 50", exact: true })
    .click();
  const colon = page
    .getByRole("dialog", { name: "Chart", exact: true })
    .getByRole("link", { name: colonSong.title, exact: true });
  const href = `/s/song/${encodeURIComponent(colonSong.key)}`;
  assert.equal(await colon.getAttribute("href"), href);
  await colon.click();
  await page.waitForURL((url) => url.pathname === href);
  await page
    .locator(".song-room h1")
    .filter({ hasText: colonSong.title })
    .waitFor();
  assert.equal(
    await page.getByText("song not found.", { exact: true }).count(),
    0,
  );
  assert.equal(
    await page
      .locator('.song-room .art img[alt^="Cover for"]')
      .first()
      .getAttribute("src"),
    `/art/${encodeURIComponent(colonSong.key)}`,
  );
  await page.screenshot({
    path: path.join(out, `library-colon-song-${size}.png`),
  });
}
export async function searchWalk(
  page: Page,
  db: Database,
  wh: Database,
  origin: string,
  out: string,
  size: string,
  overlays: Record<string, Overlay>,
) {
  await routeArtistPhotos(page);
  await page.goto(origin + "/today");
  const launch = page.getByRole("button", {
    name: "Search the Library",
    exact: true,
  });
  await launch.click();
  const dialog = page.getByRole("dialog", { name: "Library", exact: true });
  const input = dialog.getByRole("searchbox");
  const reads: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/library/search")
      reads.push(request.url());
  });
  await input.fill("n");
  await page.waitForTimeout(200);
  assert.equal(reads.length, 0);
  const responsePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/library/search",
  );
  await input.fill("ni");
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  await dialog.locator(".search-result").first().waitFor();
  assert.equal(await dialog.locator(".search-result").count(), 4);
  assert(
    response.headers()["cache-control"]?.split(/,\s*/).includes("no-store"),
  );
  const body = searchResponse.parse(await response.json());
  assert.equal(body.rows.length, 20);
  const offer = body.offers[keys[0]];
  assert(
    offer,
    "Search carries its signed song offer. Open the search response.",
  );
  const snapshot = callSnapshot.parse(JSON.parse(offer.signed.body));
  assert.equal(snapshot.card, "search");
  assert.equal(snapshot.places_shown, null);
  await photoReady(page, '.search-face img[src^="/artist-photo/"]');
  await page.screenshot({
    path: path.join(out, `library-populated-${size}.png`),
  });
  overlays[`library-populated-${size}`] = {
    kind: "sheet",
    ...(await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })),
  };
  assert(
    await dialog
      .locator("[data-card]")
      .evaluateAll((cards) =>
        cards.every(
          (card) => card.querySelectorAll("[data-primary]").length === 1,
        ),
      ),
  );
  await dialog.getByRole("button", { name: "More", exact: true }).click();
  assert.equal(await dialog.locator(".search-result").count(), 4);
  await dialog.getByRole("button", { name: "Back", exact: true }).click();
  await input.focus();
  await input.press("ArrowDown");
  assert(await page.locator("[data-search-open]:focus").count());
  for (let index = 0; index < 4; index++) {
    await page.keyboard.press("ArrowDown");
  }
  assert.equal(
    await dialog
      .getByRole("button", { name: "Back", exact: true })
      .isDisabled(),
    false,
  );
  await page.keyboard.press("ArrowUp");
  assert.equal(
    await dialog
      .getByRole("button", { name: "Back", exact: true })
      .isDisabled(),
    true,
  );
  await page.keyboard.press("Escape");
  assert.equal(await dialog.count(), 0);
  assert(
    await launch.evaluate((element) => document.activeElement === element),
  );
  await page.keyboard.press("Meta+k");
  let cancelled = false;
  page.on("requestfailed", (request) => {
    if (new URL(request.url()).searchParams.get("q") === "late melody")
      cancelled = true;
  });
  await page.route("**/library/search?q=late%20melody", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route
      .fulfill({
        json: {
          rows: [],
          offers: {},
          sources_unavailable: false,
          calls_unavailable: false,
        },
      })
      .catch(() => {});
  });
  await input.fill("late melody");
  await page.waitForRequest("**/library/search?q=late%20melody");
  await input.fill("night");
  await dialog.locator(".search-result").first().waitFor();
  await page.waitForTimeout(800);
  assert(
    cancelled,
    "Changing the text cancels the older request. Open the browser log.",
  );
  assert.equal(await dialog.locator(".search-result").count(), 4);
  await page.unroute("**/library/search?q=late%20melody");
  await input.fill("no such melody");
  await dialog.getByText("No matches.", { exact: false }).waitFor();
  await page.screenshot({ path: path.join(out, `library-empty-${size}.png`) });
  overlays[`library-empty-${size}`] = {
    kind: "sheet",
    ...(await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })),
  };
  // A database failure exercises the real route and its next step.
  await wh.unsafe(
    "ALTER TABLE marts.mart_search_index RENAME TO search_unavailable",
  );
  try {
    await input.fill("night");
    await dialog
      .getByText("Search is out of reach.", { exact: false })
      .waitFor();
    await page.screenshot({
      path: path.join(out, `library-failed-${size}.png`),
    });
    overlays[`library-failed-${size}`] = {
      kind: "sheet",
      ...(await page.evaluate(overlay, {
        kind: "sheet",
        policy: densityPolicy,
      })),
    };
  } finally {
    await wh.unsafe(
      "ALTER TABLE marts.search_unavailable RENAME TO mart_search_index",
    );
  }
  await dialog.getByRole("button", { name: "Retry", exact: true }).click();
  await dialog.locator(".search-result").first().waitFor();
  const artist = dialog.getByRole("button", {
    name: "Open Night artist",
    exact: true,
  });
  await artist.click();
  const artistSheet = page.getByRole("dialog", { name: "Artist", exact: true });
  await artistSheet
    .getByRole("link", { name: "Night tide", exact: true })
    .waitFor();
  // Night tide's two keys share one matched song: the sheet lists and counts it once, by its
  // cluster key.
  const led = await wh<
    { keys: string }[]
  >`SELECT count(DISTINCT song_key)::text AS keys
    FROM intermediate.int_song_key__daily
    WHERE platform = 'spotify' AND primary_artist_id = ${artistIdentity.platform_artist_id}`;
  const songs = BigInt(led[0]?.keys ?? "0") - 1n;
  await artistSheet
    .locator(".library-facts")
    .getByText(songs.toLocaleString("en-US"), { exact: true })
    .waitFor();
  assert.equal(
    await artistSheet.locator(`a[href="/s/song/${keys[0]}"]`).count(),
    1,
  );
  assert.equal(
    await artistSheet.locator(`a[href="/s/song/${keys[1]}"]`).count(),
    0,
  );
  await photoReady(page, '.artist-face img[src^="/artist-photo/"]');
  await noEngineRoom(page);
  await page.screenshot({ path: path.join(out, `library-artist-${size}.png`) });
  overlays[`library-artist-${size}`] = {
    kind: "sheet",
    ...(await page.evaluate(overlay, { kind: "sheet", policy: densityPolicy })),
  };
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await libraryViews(page, origin, out, size, overlays);
  await page.goto(origin + "/library");
  await input.fill("night tide");
  await dialog
    .getByRole("link", { name: "Open Night tide", exact: true })
    .waitFor();
  await input.press("Enter");
  await page.waitForURL("**/s/song/**");
  assert.equal(await dialog.count(), 0);
  await expiredSearchCall(page, db, origin, out, size, overlays);
}

async function expiredSearchCall(
  page: Page,
  db: Database,
  origin: string,
  out: string,
  size: string,
  overlays: Record<string, Overlay>,
) {
  await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
  let reads = 0;
  let expiredKey = "";
  await page.route("**/library/search?*", async (route) => {
    reads++;
    // A failed automatic refresh leaves a useful manual retry.
    if (reads === 2) {
      await route.fulfill({ status: 503, json: { next_step: "Retry." } });
      return;
    }
    const response = await route.fetch();
    const body = searchResponse.parse(await response.json());
    if (reads === 1) {
      const offer = body.offers[keys[0]];
      assert(offer, "Open the Library offer fixture.");
      const facts = callSnapshot.parse(JSON.parse(offer.signed.body));
      offer.signed = signCall({ ...facts, exp: Date.now() - 1000 });
      expiredKey = offer.signed.idempotency_key;
    }
    await route.fulfill({ response, json: body });
  });
  try {
    await page.goto(origin + "/library");
    const library = page.getByRole("dialog", { name: "Library", exact: true });
    await library.getByRole("searchbox").fill("night tide");
    await library.getByRole("button", { name: "Call it", exact: true }).click();
    const call = page.getByRole("dialog", { name: "Call", exact: true });
    const refusal = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/calls",
    );
    const refresh = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/library/search",
      { timeout: 5000 },
    );
    await call.getByRole("button", { name: "Call it", exact: true }).click();
    assert.equal((await refusal).status(), 409);
    assert.equal((await refresh).status(), 503);
    await call
      .getByRole("button", { name: "Refresh card", exact: true })
      .click();
    await call
      .getByRole("heading", { name: "Call the refreshed card?", exact: true })
      .waitFor();
    assert.equal(reads, 3);
    assert.equal(
      (
        await db<
          { n: number }[]
        >`SELECT count(*)::int AS n FROM control.showcase_call WHERE author='fixture'`
      )[0]?.n,
      0,
    );
    await page.screenshot({
      path: path.join(out, `library-refreshed-call-${size}.png`),
    });
    overlays[`library-refreshed-call-${size}`] = {
      kind: "sheet",
      ...(await page.evaluate(overlay, {
        kind: "sheet",
        policy: densityPolicy,
      })),
    };
    await call.getByRole("button", { name: "Call it", exact: true }).click();
    await library.getByText(/^called ·/).waitFor();
    const saved = await db<
      { idempotency_key: string; snapshot: string }[]
    >`SELECT idempotency_key,snapshot FROM control.showcase_call WHERE author='fixture'`;
    assert.equal(saved.length, 1);
    assert(saved[0]);
    assert.notEqual(saved[0].idempotency_key, expiredKey);
    const facts = callSnapshot.parse(JSON.parse(saved[0].snapshot));
    assert.equal(facts.card, "search");
    assert(facts.exp > Date.now());
  } finally {
    await page.unroute("**/library/search?*");
    await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
  }
}
