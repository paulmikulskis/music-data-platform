import { readFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  holdings as holdingsSchema,
  platformSources,
  sourceWording,
} from "@mdp/contracts";
vi.mock("server-only", () => ({}));
import {
  readiness as readinessRow,
  rightsRow,
} from "../server/models";
import { artistSizeMeasured } from "../lib/music-facts";
import {
  barHeights,
  collectionBegan,
  counted,
  peakDay,
  weekDays,
} from "../lib/collection";
import { dayName } from "../lib/presentation";
import { placeCounts, placeName } from "../lib/places";
import {
  awaitingAccess,
  liveSources,
  liveList,
  ownLists,
  permissionLine,
  rightsGroups,
} from "../lib/rights";
import { Holdings, rightsStatus } from "../components/holdings";
import { TendingProvider } from "../components/sources";
import { tendedCounters } from "../components/tending";
import { historyDays } from "../components/behind-card";
import { laneValue } from "../components/song";
import {
  holdings as holdingsFixture,
  readiness,
  sources,
} from "./browser/fixtures";

const repo = path.resolve(__dirname, "../../../..");
const csv = (file: string) => {
  const [header, ...lines] = readFileSync(path.join(repo, file), "utf8")
    .trim()
    .split("\n");
  const keys = header!.split(",");
  return lines.map((line) =>
    Object.fromEntries(line.split(",").map((value, i) => [keys[i], value])),
  );
};
const registry = csv("dbt/seeds/rights_registry.csv");
const holdings = holdingsSchema.parse(holdingsFixture);
const parsedSources = platformSources.parse({
  queried_at: "2026-09-26T06:12:00.000Z",
  sources,
  next_step: "Open /functions.",
}).sources;

describe("Rising", () => {
  it("waits for a first artist size reading instead of showing 0 of N", () => {
    const rows = readiness(false).map((row) => readinessRow.parse(row));
    expect(artistSizeMeasured(rows, "new_entries")).toBe(true);
    const zero = rows.map((row) =>
      row.movement_list === "new_entries"
        ? { ...row, stage_known_songs: "0" }
        : row,
    );
    expect(artistSizeMeasured(zero, "new_entries")).toBe(false);
    expect(artistSizeMeasured(undefined, "new_entries")).toBe(false);
  });
});

describe("Holdings", () => {
  const since = "2026-09-21T00:00:00.000Z";
  const now = Date.parse("2026-09-26T21:00:00Z");
  const days = [
    { day: "2026-09-23", rows_inserted: "46986" },
    { day: "2026-09-24", rows_inserted: "149347" },
    { day: "2026-09-25", rows_inserted: "21850" },
    { day: "2026-09-26", rows_inserted: "16968" },
  ];
  it("draws the week so far: zero before collection, today marked, the rest empty", () => {
    const week = weekDays(since, days, { now });
    expect(week.map((day) => day.day)).toEqual([
      "2026-09-21",
      "2026-09-22",
      "2026-09-23",
      "2026-09-24",
      "2026-09-25",
      "2026-09-26",
      "2026-09-27",
    ]);
    expect(week[0]!.entries).toBe(0n);
    // Days before the first collection are not yet collecting, not zero.
    const began = weekDays(since, days, { now, began: "2026-09-23" });
    expect(began.slice(0, 3).map((day) => [day.before, day.entries])).toEqual([
      [true, null],
      [true, null],
      [false, 46986n],
    ]);
    expect(barHeights(began).slice(0, 2)).toEqual([null, null]);
    expect(
      collectionBegan([
        { first_collected: "2026-09-24T01:00:00Z" },
        { first_collected: null },
        { first_collected: "2026-09-23T04:00:00Z" },
      ]),
    ).toBe("2026-09-23");
    expect(week[5]!.today).toBe(true);
    expect(week[6]!.entries).toBeNull();
    expect(barHeights(week)[3]).toBe(1);
    expect(barHeights(week)[6]).toBeNull();
    expect(dayName("2026-09-24")).toBe("Thu 24 Sept");
    expect(
      peakDay(week, [
        { day: "2026-09-24", source_key: "mb_resolve", rows_inserted: "40794" },
        { day: "2026-09-24", source_key: "mb_spine", rows_inserted: "102269" },
      ]),
    ).toEqual({ day: "2026-09-24", source_key: "mb_spine" });
  });
  const page = (data: typeof holdings) =>
    renderToStaticMarkup(
      <Holdings
        data={{
          ...data,
          inventory_now: { queried_at: data.queried_at, layers: [] },
        }}
        provenance={{
          queried_at: data.queried_at,
          scope: "global",
          provenance: "live query",
          query: "Entries.",
          sql: "pnpm --dir control mdp platform holdings",
        }}
        sources="18"
        queries={{ inventory: "SELECT 1" }}
        view="collection"
      />,
    );
  it("says cost is not measured while vendor usage carries no price", () => {
    const html = page({
      ...holdings,
      vendor_cost: {
        ...holdings.vendor_cost,
        has_current_rows: true,
        cost_cents: "0",
      },
    });
    expect(html).toContain("Cost · not measured yet");
    expect(html).not.toContain("$0.00");
    expect(html).not.toContain("Infrastructure excluded");
  });
  it("suppresses historical catalog totals without a genuine-row basis", () => {
    const html = page(
      holdingsSchema.parse({
        ...holdings,
        inventory_history: {
          first_snapshot_day: null,
          unavailable_before: null,
          days: [{ day: "2026-09-26", state: "unavailable", layers: [] }],
          next_step: "Open /runbooks/showcase-inventory-failed.",
        },
      }),
    );
    expect(html).toContain("Stored totals · not measured yet");
    expect(html).not.toContain("runbook");
    expect(html).not.toContain("History is unavailable");
    expect(html).not.toContain("Earlier storage");
    const stored = page({
      ...holdings,
      inventory_history: {
        ...holdings.inventory_history,
        days: holdings.inventory_history.days.map((day) => ({
          ...day,
          layers: day.layers.map((layer) => ({
            ...layer,
            rows_est: "987654321",
          })),
        })),
      },
    });
    expect(stored).toContain("Stored totals · not measured yet");
    expect(stored).not.toContain("987,654,321");
    expect(stored).not.toContain("Stored on");
    expect(stored).not.toContain("987654321");
  });
});

describe("Rights", () => {
  it("names every source in the rights register in plain words", () => {
    for (const row of registry)
      expect(sourceWording[row.source_key!]?.name, row.source_key).toBeTruthy();
    expect(sourceWording.sp_playlist_embed!.name).toBe(
      "Spotify playlist embeds",
    );
  });
  it("keeps sample-row adapters out of collecting now", () => {
    const sourcesDir = path.join(repo, "functions/src/mdp_functions/sources");
    const samples = readdirSync(sourcesDir).filter((dir) => {
      const file = path.join(sourcesDir, dir, "function.py");
      return (
        existsSync(file) &&
        /Path\(__file__\)\.parent \/ "fixtures/.test(readFileSync(file, "utf8"))
      );
    });
    expect(samples).toEqual([]);
    for (const key of samples) expect(awaitingAccess.has(key), key).toBe(true);
    for (const row of registry.filter((row) => row.provider === "platform"))
      expect(ownLists.has(row.source_key!), row.source_key).toBe(true);
  });
  it("counts public collection by day", () => {
    const totals = counted(
      [
        { day: "2026-09-26", source_key: "sz_chart", rows_inserted: "58" },

        { day: "2026-09-25", source_key: "sp_playlist", rows_inserted: "40" },
      ],
      Date.parse("2026-09-26T12:00:00Z"),
    );
    expect(totals.week).toBe("98");
    expect(totals.today).toBe("58");
    expect(totals.rows.map((row) => row.source_key)).toEqual([
      "sz_chart",
      "sp_playlist",
    ]);
  });
  it("takes each source's state from its record, and Holdings counts the same live sources", () => {
    const base = parsedSources[0]!;
    const quiet = {
      ...base,
      source_key: "wiki_pageviews",
      evidence: base.evidence ? { ...base.evidence, last_success: null } : null,
      days: base.days.map((day) => ({ ...day, entries: "0" })),
    };
    // Read on 23 Sept, then switched off.
    const off = { ...base, source_key: "bc_discover", enabled: false };
    // Sample rows loaded today, but the provider has not granted access.
    const sample = {
      ...base,
      source_key: "fixture_tracks",
      last_read: new Date().toISOString(),
      evidence: base.evidence
        ? {
            ...base.evidence,
            incident: {
              alert_id: "00000000-0000-4000-8000-000000000002",
              run_id: null,
              attempt_no: null,
              class: "provider_credentials_missing",
              at: new Date().toISOString(),
              remediation: null,
            },
          }
        : null,
    };
    const records = [...parsedSources, quiet, off, sample];
    const rows = rightsRow.parse({
      annotated: "5",
      learning: "0",
      resale: "0",
      sources: [
        "sz_chart",
        "wiki_pageviews",
        "bc_discover",
        "fixture_tracks",
        "fixture_source",
      ].map((source_key) => ({ source_key, learning: false, resale: false })),
    }).sources;
    const groups = rightsGroups(rows, records);
    expect(groups.collecting.map((entry) => entry.source_key)).toEqual([
      "sz_chart",
    ]);
    expect(groups.quiet.map((entry) => entry.source_key)).toEqual([
      "wiki_pageviews",
    ]);
    expect(groups.off.map((entry) => entry.source_key)).toEqual([
      "bc_discover",
    ]);
    expect(groups.waiting.map((entry) => entry.source_key)).toEqual([
      "fixture_source",
      "fixture_tracks",
    ]);
    // Holdings' sources live and Rights' Collecting now are one count.
    expect(liveSources(records)).toBe(String(liveList(parsedSources).length));
    expect(liveSources([quiet, off, sample])).toBe("0");
    expect(liveSources([])).toBeNull();
    const card = (key: string) =>
      rightsStatus(
        groups.all.find((entry) => entry.source_key === key)!,
        false,
      );
    expect(card("fixture_tracks")).toBe("Waiting for provider access");
    expect(card("fixture_tracks")).not.toContain("last read");
    expect(card("bc_discover")).toBe("Built · switched off");
    expect(card("wiki_pageviews")).toBe("Enabled · no successful readings");
    expect(card("fixture_source")).toBe("State unavailable · Retry");
  });
  it("counts the same live sources in what we tend, and leaves sample loads out", () => {
    const now = Date.parse(
      `${parsedSources[0]!.last_read!.slice(0, 10)}T12:00:00Z`,
    );
    const base = parsedSources[0]!;
    const zero = base.days.map((day) => ({ ...day, entries: "0" }));
    // Loaded rows on the first day, then switched off, as bc_discover and five others in production.
    const off = {
      ...base,
      source_key: "bc_discover",
      enabled: false,
      entries_today: "0",
    };
    const quiet = {
      ...base,
      source_key: "wiki_pageviews",
      evidence: base.evidence ? { ...base.evidence, last_success: null } : null,
      days: zero,
      entries_today: "0",
    };
    const example = { ...base, source_key: "typesafe" };
    const records = [...parsedSources, off, quiet, example];
    const counters = Object.fromEntries(
      tendedCounters(records, null, now).map((counter) => [
        counter.key,
        counter,
      ]),
    );
    expect(counters.sources).toMatchObject({
      label: "sources live",
      value: liveList(parsedSources, now).length,
    });
    expect(String(counters.sources!.value)).toBe(liveSources(records, now));
    const real = [...parsedSources, off, quiet, example];
    expect(counters.entries!.value).toBe(
      real.reduce(
        (total, source) => total + globalThis.Number(source.entries_today),
        0,
      ),
    );
    expect(counters.days!.value).toBe(historyDays(real, now));
  });
  it("keeps internal example sources out of the viewer list", () => {
    const rows = rightsRow.parse({
      annotated: "2",
      learning: "0",
      resale: "0",
      sources: ["typesafe", "sz_chart"].map((source_key) => ({
        source_key,
        learning: false,
        resale: false,
      })),
    }).sources;
    expect(
      rightsGroups(rows, parsedSources).all.map((entry) => entry.source_key),
    ).toEqual(["sz_chart"]);
  });
  it("splits collecting now from not connected yet and the platform's own lists", () => {
    const rows = rightsRow.parse({
      annotated: "6",
      learning: "1",
      resale: "1",
      sources: [
        { source_key: "sz_chart", learning: false, resale: false },
        { source_key: "fixture_source", learning: false, resale: false },
        { source_key: "fixture_tracks", learning: false, resale: false },
        { source_key: "bc_discover", learning: false, resale: false },
        { source_key: "bc_discover_weekly", learning: false, resale: false },
      ],
    }).sources;
    const fixture_tracks = {
      ...parsedSources[0]!,
      source_key: "fixture_tracks",
      display_name: "Fixture tracks",
      evidence: parsedSources[0]?.evidence
        ? {
            ...parsedSources[0].evidence,
            incident: {
              alert_id: "00000000-0000-4000-8000-000000000002",
              run_id: null,
              attempt_no: null,
              class: "provider_credentials_missing",
              at: new Date().toISOString(),
              remediation: null,
            },
          }
        : null,
    };
    const groups = rightsGroups(rows, [...parsedSources, fixture_tracks]);
    expect(groups.collecting.map((entry) => entry.name)).toEqual([
      "Shazam charts",
    ]);
    expect(groups.waiting.map((entry) => entry.name)).toEqual([
      "Bandcamp best-sellers",
      "Bandcamp best-sellers, weekly",
      "Fixture source",
      "Fixture tracks",
    ]);
    expect(groups.own).toEqual([]);
    expect(rightsGroups(rows, null).measured).toBe(false);
    expect(rightsGroups(rows, null).all).toHaveLength(5);
    expect(rightsGroups(rows, null).waiting).toEqual([]);
    expect(permissionLine({ learning: false, resale: false })).toBe(
      "Not cleared for training or resale.",
    );
    expect(permissionLine({ learning: true, resale: false })).toBe(
      "Training cleared. Not cleared for resale.",
    );
  });
  it("renders groups, never a Held vendor chip, and no engine-room links", () => {
    const rights = rightsRow.parse({
      annotated: "75",
      learning: "10",
      resale: "9",
      sources: [
        { source_key: "fixture_source", learning: false, resale: false },
        { source_key: "sz_chart", learning: false, resale: false },
      ],
    });
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources: parsedSources, songs: null }}>
        <Holdings
          data={{
            ...holdings,
            inventory_now: { queried_at: holdings.queried_at, layers: [] },
          }}
          rights={rights}
          provenance={{
            queried_at: holdings.queried_at,
            scope: "global",
            query: "q",
            sql: "s",
            provenance: "live query",
          }}
          rightsProvenance={{
            queried_at: holdings.queried_at,
            scope: "global",
            query: "q",
            sql: "s",
            provenance: "reviewed list",
          }}
          sources="3"
          queries={{ inventory: "SELECT 1" }}
          view="sources"
        />
      </TendingProvider>,
    );
    expect(html).toContain("Collecting now · 1");
    expect(html).toContain("Not connected yet · 1");
    expect(html).not.toContain("Not read yet · 0");
    expect(html).toContain(
      "Permissions say whether data is cleared for training or resale.",
    );
    expect(html).not.toContain("Held · ");
    expect(html).not.toContain("/functions/");
    expect(html).not.toContain("Source unknown");
  });
});

describe("Song", () => {
  it("formats lane values and counts places the way the map draws them", () => {
    expect(laneValue(2, 12500)).toBe("12,500 plays/day");
    expect(laneValue(1, 1)).toBe("1 city");
    const places = [
      ...Array.from({ length: 15 }, (_, i) => ({
        country: "GB",
        city: `city-${i}`,
      })),
      ...["AU", "CA", "DE", "FR", "GB", "US"].map((country) => ({
        country,
        city: null,
      })),
      { country: null, city: null },
    ];
    expect(placeCounts(places)).toBe(
      "On Shazam charts in 15 cities and 6 countries in the last 28 days, plus the global chart.",
    );
    expect(placeCounts([])).toBe("No Shazam chart in the last 28 days.");
    expect(placeName({ country: "AU", city: null })).toBe("Australia");
    expect(placeName({ country: "CA", city: "montr%C3%A9al" })).toBe(
      "Montréal",
    );
  });
});
