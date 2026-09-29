// Synthetic fixtures for Rising, Holdings, Rights and Song on phone and laptop.
import assert from "node:assert/strict";
import { stat, writeFile } from "node:fs/promises";
import path from "node:path";
import type { Locator, Page } from "@playwright/test";
import type { Sql } from "postgres";
import { sourceWording } from "@mdp/contracts/source-wording";
import { density, overlay, densityPolicy } from "./density.mjs";
import type { Overlay } from "./provenance";
import { copies } from "./fixtures";

const today = new Date().toISOString().slice(0, 10);
const midnight = Date.parse(`${today}T00:00:00Z`);
const dayOffset = (offset: number) =>
  new Date(midnight + offset * 86400000).toISOString().slice(0, 10);
const weekStart = dayOffset(-((new Date(midnight).getUTCDay() + 6) % 7));
const rights = {
  learning_eligible: false,
  resale_permitted: false,
  source_keys: '["sz_chart"]',
};

// Four collection days ending today, with the sources that brought most of each.
const collected: [number, [string, number][]][] = [
  [
    -3,
    [
      ["wiki_pageviews", 40],
      ["bc_discover", 36],
      ["sp_playlist", 33],
      ["am_playlist", 97],
    ],
  ],
  [
    -2,
    [
      ["mb_spine", 99],
      ["mb_resolve", 84],
      ["sp_playlist", 84],
    ],
  ],
  [
    -1,
    [
      ["mb_resolve", 80],
      ["sp_playlist", 70],
      ["am_playlist", 40],
      ["sz_chart", 80],
    ],
  ],
  [
    0,
    [
      ["mb_resolve", 40],
      ["am_playlist", 80],
      ["sz_chart", 80],
      ["sp_playlist", 88],
    ],
  ],
];
const week = collected.filter(([offset]) => dayOffset(offset) >= weekStart);
const total = (entries: [string, number][]) =>
  entries.reduce((sum, [, n]) => sum + n, 0);
export const shapedHoldings = {
  queried_at: new Date().toISOString(),
  since: `${weekStart}T00:00:00.000Z`,
  warehouse: "production-shaped",
  ingestion: {
    summary: {
      rows_inserted: String(week.reduce((sum, [, e]) => sum + total(e), 0)),
      sources_live: "18",
      today_rows: String(total(collected[3]![1])),
      today_sources: "12",
      today_measured: true,
      days: week.map(([offset, entries]) => ({
        day: dayOffset(offset),
        rows_inserted: String(total(entries)),
      })),
    },
    rows: week.flatMap(([offset, entries]) =>
      entries.map(([source_key, n]) => ({
        day: dayOffset(offset),
        source_key,
        rows_inserted: String(n),
      })),
    ),
    next_step: "Open /runs to inspect load receipts.",
  },
  inventory_history: {
    first_snapshot_day: null,
    unavailable_before: null,
    days: Array.from(
      {
        length:
          (midnight - Date.parse(`${weekStart}T00:00:00Z`)) / 86400000 + 1,
      },
      (_, i) => ({
        day: dayOffset(
          i - (midnight - Date.parse(`${weekStart}T00:00:00Z`)) / 86400000,
        ),
        state: "unavailable",
        layers: [],
      }),
    ),
    next_step:
      "Open /runbooks/showcase-inventory-failed to check missing snapshots.",
  },
  vendor_cost: {
    cost_cents: "0",
    has_current_rows: true,
    label: "estimate",
    days: [],
    infrastructure_included: false,
    next_step:
      "Infrastructure cost is excluded. Open /ops to inspect vendor usage.",
  },
};

// What platform.sources answers: switched-on sources with this week's entries, a sample-row
// adapter, and switched-off sources whose last read was three days ago.
const source = (
  source_key: string,
  cadence: string,
  enabled = true,
  targets: number | null = null,
) => {
  const wording = sourceWording[source_key];
  assert(wording, source_key);
  const days = Array.from({ length: 14 }, (_, i) => {
    const day = dayOffset(i - 13);
    const n = collected
      .filter(([offset]) => dayOffset(offset) === day)
      .flatMap(([, entries]) => entries)
      .find(([key]) => key === source_key)?.[1];
    return {
      day,
      entries: String(n ?? (enabled && i >= 10 ? 2 : 0)),
    };
  });
  const read = days.filter((day) => day.entries !== "0").at(-1)?.day;
  return {
    source_key,
    display_name: wording.name,
    brand: wording.brand,
    family: wording.family,
    description: wording.plain,
    cadence,
    enabled,
    first_collected: `${dayOffset(-3)}T02:54:00.000Z`,
    last_read: read ? `${read}T02:58:00.000Z` : null,
    entries_today: days.at(-1)!.entries,
    targets,
    tracked:
      targets === null
        ? null
        : {
            count: targets,
            unit:
              wording.family === "playlists"
                ? "playlists"
                : wording.family === "charts"
                  ? "charts"
                  : "accounts",
            as_of: dayOffset(0) + "T02:54:00.000Z",
          },
    evidence: {
      declared_at: dayOffset(-30) + "T00:00:00.000Z",
      configured_at: dayOffset(-3) + "T00:00:00.000Z",
      checked_at: new Date().toISOString(),
      tenant_bound: false,
      last_success: read ? `${read}T02:58:00.000Z` : null,
      attempt: null,
      incident: null,
    },
    days,
  };
};
export const shapedSources = [
  source("sz_chart", "daily", true, 58),
  source("sp_playlist", "daily", true, 34),
  source("am_playlist", "daily", true, 46),
  source("sp_playlist_weekly", "daily", true, 20),
  source("mb_spine", "weekly"),
  source("mb_resolve", "hourly"),
  source("billboard_hot100", "weekly"),
  source("track_isrc_crosswalk", "hourly"),
  source("sp_track_artists", "daily"),
  source("sp_playlist_embed", "daily", false),
  source("bc_discover", "daily", false),
  source("wiki_sitelinks", "daily", false),
  source("wiki_pageviews", "daily"),
].map((row) =>
  ["sp_playlist_embed", "bc_discover", "wiki_sitelinks"].includes(
    row.source_key,
  )
    ? {
        ...row,
        days: row.days.map((day) =>
          day.day === dayOffset(-3)
            ? { ...day, entries: "1824" }
            : { ...day, entries: "0" },
        ),
        last_read: `${dayOffset(-3)}T08:54:00.000Z`,
        entries_today: "0",
      }
    : row.source_key === "wiki_pageviews"
      ? // Switched on, nothing read in two weeks.
        {
          ...row,
          days: row.days.map((day) => ({ ...day, entries: "0" })),
          first_collected: null,
          last_read: null,
          entries_today: "0",
        }
      : row,
);

const readinessRow = (
  family: string,
  history_days: number,
  first_rank_day: string | null,
  list: { movement_list: string; known: string; songs: string } | null = null,
) => ({
  family,
  day: today,
  history_days: String(history_days),
  history_needed: "Collect more days; open song history.",
  first_rank_day,
  movement_list: list?.movement_list ?? null,
  stage_known_songs: list?.known ?? null,
  list_songs: list?.songs ?? null,
  ...rights,
});
export const shapedReadiness = [
  readinessRow("playlists", 4, dayOffset(-2)),
  readinessRow("shazam", 2, today),
  readinessRow("streams", 2, dayOffset(4)),
  readinessRow("artist_stage:new_entries", 0, null, {
    movement_list: "new_entries",
    known: "0",
    songs: "120",
  }),
  readinessRow("artist_stage:catalog_entries", 0, null, {
    movement_list: "catalog_entries",
    known: "0",
    songs: "147",
  }),
];

// A song first seen a week ago, on 13 then 15 city charts and about 1.4 million plays a day.
export const shapedSongDays = (song_key: string) =>
  Array.from({ length: 8 }, (_, i) => {
    const offset = i - 7;
    return {
      song_key,
      title_text: "Fixture Song A",
      artist_text: "fixture_artist",
      day: dayOffset(offset),
      editorial_adds: offset >= -3 ? 0 : null,
      algorithmic_adds: 0,
      shazam_cities: offset === -1 ? "13" : offset === 0 ? "15" : null,
      playlist_followers: null,
      stream_rate: offset === -1 ? 12000 : offset === 0 ? 12500 : null,
      ...rights,
    };
  });
const places: [string | null, string | null, number][] = [
  ["AU", "perth", -1],
  ["AU", "sydney", -1],
  ["AU", null, -1],
  ["CA", "calgary", -1],
  ["CA", "toronto", -1],
  ["CA", "vancouver", -1],
  ["CA", null, -1],
  ["DE", "berlin", -1],
  ["DE", "k%C3%B6ln", -1],
  ["DE", null, -1],
  ["FR", null, -1],
  ["GB", "birmingham", -1],
  ["GB", "glasgow", -1],
  ["GB", "leeds", -1],
  ["GB", "london", -1],
  ["GB", "manchester", -1],
  ["GB", null, -1],
  ["US", "phoenix", -1],
  ["US", null, -1],
  [null, null, -1],
  ["CA", "montr%C3%A9al", 0],
  ["DE", "munich", 0],
];

// Warehouse rows: the rights register as reviewed, and the song's charts.
export async function seedPages(wh: Sql) {
  await wh.unsafe("TRUNCATE marts.mart_shazam_chart_daily");
  await wh.unsafe(
    `INSERT INTO marts.mart_shazam_chart_daily
     SELECT 'shazam:' || coalesce(city, country, 'global'), (now() AT TIME ZONE 'UTC')::date + day_offset,
       CASE WHEN city IS NULL THEN 'top-200' ELSE 'top-50' END, country, city, 10, $2, '["sz_chart"]'
     FROM jsonb_to_recordset($1::text::jsonb) AS p(country text, city text, day_offset integer)`,
    [
      JSON.stringify(
        places.map(([country, city, day_offset]) => ({
          country,
          city,
          day_offset,
        })),
      ),
      copies[1].platform_track_id,
    ],
  );
}

// Screens land beside the full gate's evidence: phone and laptop, cropped, each under 300 KB.
export async function pagesWalk(
  page: Page,
  origin: string,
  out: string,
  size: string,
  song: string,
  overlays: Record<string, Overlay>,
  measurements: Record<string, ReturnType<typeof density>>,
) {
  const phone = size.startsWith("390");
  const shots: string[] = [];
  const save = async (
    name: string,
    take: (file: string) => Promise<unknown>,
  ) => {
    const file = path.join(out, `${name}-${size}.png`);
    await page.waitForTimeout(350);
    await take(file);
    assert((await stat(file)).size < 300_000, `${name} is under 300 KB`);
    shots.push(file);
  };
  const top = (name: string, bottom: Locator) =>
    save(name, async (file) => {
      const box = await bottom.boundingBox();
      assert(box, `${name} has a lower edge`);
      const viewport = page.viewportSize();
      assert(viewport);
      await page.screenshot({
        path: file,
        clip: {
          x: 0,
          y: 0,
          width: viewport.width,
          height: Math.min(viewport.height, Math.ceil(box.y + box.height + 16)),
        },
      });
    });
  // A laptop pointer left over a sheet opens its hover card; park it before a sheet capture.
  const element = async (name: string, locator: Locator) => {
    if (!phone && name.endsWith("-sheet")) await page.mouse.move(2, 2);
    await save(name, (file) => locator.screenshot({ path: file }));
  };
  const measure = async (name: string, kind: "popover" | "sheet") => {
    await page.waitForTimeout(250);
    overlays[`${name}-${size}`] = {
      kind,
      ...(await page.evaluate(overlay, { kind: kind, policy: densityPolicy })),
    };
    assert(overlays[`${name}-${size}`]!.found, `${name} opens its ${kind}.`);
  };
  const sheet = () => page.locator("dialog[open]").last();
  const close = () =>
    page.getByRole("button", { name: "Close", exact: true }).last().click();
  const tapOrClick = async (locator: Locator, x = 0.5) => {
    const box = await locator.boundingBox();
    assert(box);
    const at = { x: box.x + box.width * x, y: box.y + box.height * 0.6 };
    if (phone) await page.touchscreen.tap(at.x, at.y);
    else await page.mouse.click(at.x, at.y);
  };

  // Rising: no "artist size known for 0 of 120" while no artist has a size reading.
  await page.goto(origin + "/rising");
  await page.waitForTimeout(1300);
  assert.equal(await page.getByText(/artist size known/).count(), 0);
  if (phone)
    measurements["pages-rising"] = await page.evaluate(density, densityPolicy);
  await top("rising-header", page.locator(".rising-room .chips").first());

  // Holdings: cost waits for priced vendor usage; the bars say which days they are.
  await page.goto(origin + "/holdings");
  await page.waitForTimeout(1300);
  assert.equal(await page.getByText("$0.00").count(), 0);
  assert.equal(
    await page.getByText("Cost · not measured yet", { exact: true }).count(),
    1,
  );
  assert.equal(
    await page
      .getByText("entries each day, this week", { exact: true })
      .count(),
    1,
  );
  assert.equal(
    await page.locator(".bars-days .today").innerText(),
    "today · so far",
  );
  if (phone)
    measurements["pages-holdings"] = await page.evaluate(
      density,
      densityPolicy,
    );
  await top("holdings-numbers", page.locator(".collection-bars"));
  const cost = page.getByRole("button", {
    name: "Cost · not measured yet",
    exact: true,
  });
  if (phone) {
    await cost.tap();
    await sheet()
      .getByText(/none carries a price yet/)
      .waitFor();
    await element("holdings-cost-sheet", sheet());
    await measure("pages-cost", "sheet");
    await close();
  } else {
    await cost.hover();
    await page
      .locator(".hover-card")
      .getByText(/none carries a price yet/)
      .waitFor();
    await element("holdings-cost-hover", page.locator(".hover-card"));
    await measure("pages-cost", "popover");
    await page.mouse.move(2, 2);
  }
  await page
    .getByRole("button", { name: /^Entries each day this week/ })
    .click();
  await sheet()
    .getByText("Stored totals · not measured yet", { exact: true })
    .waitFor();
  const collection = await sheet().innerText();
  assert.match(
    collection,
    /is highest because MusicBrainz is read once a week/,
  );
  // Days before the first collection are not listed as zero.
  assert.doesNotMatch(collection, /\n0\n/);
  if (weekStart < dayOffset(-3)) assert.match(collection, /Collection began /);
  assert.match(collection, /149,347/);
  assert.doesNotMatch(collection, /History is unavailable|Earlier storage|—/);
  assert.equal(await sheet().locator('a[href*="runbook"]').count(), 0);
  await element("holdings-collection-sheet", sheet());
  await measure("pages-collection", "sheet");
  await sheet()
    .getByRole("button", { name: "What we tend ↓", exact: true })
    .click();
  assert.equal(await page.locator("dialog[open]").count(), 0);

  // Holdings' sources live is Rights' Collecting now: one rule, one count.
  const live = (
    await page
      .locator(".metric-button", { hasText: "sources live" })
      .innerText()
  ).match(/\d+/)?.[0];
  assert(live, "Holdings shows sources live.");
  // What we tend, where the Collection sheet's button lands, counts the same sources live.
  const tended = page.locator(".tending-counter", { hasText: "sources live" });
  await tended.scrollIntoViewIfNeeded();
  await page.waitForTimeout(900);
  assert.equal(
    (await tended.locator(".count").innerText()).replaceAll(",", ""),
    live,
    "What we tend counts the same sources live as the Holdings number.",
  );
  await element("holdings-tending", page.locator(".tending"));

  // Rights: each source's state from its record, what "held back" means, plain names, a card each.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("tab", { name: "Rights", exact: true }).click();
  await page.waitForTimeout(400);
  assert.equal(await page.getByText(/^Held · /).count(), 0);
  assert.equal(await page.getByText("Source unknown").count(), 0);
  await page
    .getByText(
      "Permissions say whether data is cleared for training or resale.",
      {
        exact: true,
      },
    )
    .waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: `Collecting now · ${live}`, exact: true })
      .count(),
    1,
  );
  if (phone)
    measurements["pages-rights"] = await page.evaluate(density, densityPolicy);
  await top("rights", page.locator(".rights-groups"));
  const collectingChip = page.getByRole("button", {
    name: /^Collecting now · \d+$/,
  });
  if (phone) await collectingChip.tap();
  else await collectingChip.click();
  await sheet()
    .getByText("Switched on and read in the last two weeks.", { exact: true })
    .waitFor();
  const collectingText = await sheet().innerText();
  assert.match(collectingText, /Shazam charts/);
  assert.doesNotMatch(collectingText, /Fixture tracks|Spotify playlist embeds/);
  assert.equal(await sheet().locator('a[href^="/functions"]').count(), 0);
  await element("rights-collecting-sheet", sheet());
  await measure("pages-rights-collecting", "sheet");
  const tab = async (name: RegExp) => {
    const found = sheet().getByRole("tab", { name });
    if (phone) await found.tap();
    else await found.click();
  };
  await tab(/^Switched off · \d+$/);
  await sheet()
    .getByText("Read in the last two weeks, now switched off.", { exact: true })
    .waitFor();
  assert.match(await sheet().innerText(), /Spotify playlist embeds/);
  await tab(/^Not read yet · \d+$/);
  await sheet()
    .getByText("Switched on, with nothing read in the last two weeks.", {
      exact: true,
    })
    .waitFor();
  assert.match(await sheet().innerText(), /Wikipedia page views/);
  await close();

  // Song: a tap on a lane opens the shown day; the Places sheet carries the map and one count.
  await page.goto(origin + `/s/song/${encodeURIComponent(song)}`);
  await page.waitForTimeout(1500);
  assert.equal(
    await page.locator(".lane-value").nth(1).innerText(),
    "15 cities",
  );
  assert.equal(
    await page.locator(".lane-value").nth(2).innerText(),
    "12,500 plays/day",
  );
  // The song's first view is measured, not gated: this song carries four numbers on arrival.
  await writeFile(
    path.join(out, `song-first-view-${size}.json`),
    JSON.stringify(await page.evaluate(density, densityPolicy), null, 2) + "\n",
  );
  await top("song-lanes", page.locator(".scrub-control"));
  const slider = page.getByRole("slider", { name: "Scrub day" });
  const latest = await slider.inputValue();
  await tapOrClick(
    page.getByRole("button", { name: "Open Shazam cities mark" }),
    0.5,
  );
  await sheet().locator(".value").filter({ hasText: /^15$/ }).waitFor();
  assert.equal(await slider.inputValue(), latest, "A tap never scrubs.");
  await element("song-cities-tap-sheet", sheet());
  await measure("pages-song-cities", "sheet");
  await close();
  await tapOrClick(page.getByRole("button", { name: "Open Plays mark" }), 0.35);
  await sheet()
    .locator(".value")
    .filter({ hasText: /^12,500$/ })
    .waitFor();
  assert.equal(await slider.inputValue(), latest, "A tap never scrubs.");
  await element("song-streams-tap-sheet", sheet());
  await close();
  if (!phone) {
    const lanes = await page.locator(".song-lanes").boundingBox();
    assert(lanes);
    await page.mouse.move(lanes.x + lanes.width * 0.95, lanes.y + 40);
    await page.mouse.down();
    await page.mouse.move(lanes.x + lanes.width * 0.2, lanes.y + 40, {
      steps: 12,
    });
    await page.mouse.up();
    assert.notEqual(await slider.inputValue(), latest, "A drag scrubs.");
    assert.equal(
      await page.locator("dialog[open]").count(),
      0,
      "A drag opens no sheet.",
    );
  }
  await page.getByRole("button", { name: "Places →", exact: true }).click();
  await sheet().locator(".city-map svg").waitFor();
  assert.equal(
    await sheet()
      .getByText(
        "On Shazam charts in 15 cities and 6 countries in the last 28 days, plus the global chart.",
        { exact: true },
      )
      .count(),
    1,
  );
  assert.equal(await sheet().getByText("AU", { exact: true }).count(), 0);
  await page.waitForTimeout(700);
  await element("song-places-sheet", sheet());
  await measure("pages-song-places", "sheet");
  await close();
  return shots;
}
