import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  mover,
  arrival,
  arrivalSummary,
  earlySignal,
  readiness as readinessRow,
} from "../server/models";
import {
  artistSizeLine,
  answerWarming,
  arrivalFact,
  countryName,
  earlyFact,
  listTag,
  rankingLine,
  warmingDays,
  warmingLabel,
} from "../lib/music-facts";
import { Arrivals } from "../components/arrivals";
import { Places } from "../components/places";
import { Rising } from "../components/rising";
import { movers, arrivals, earlySignals, readiness } from "./browser/fixtures";
const p = {
  queried_at: "2026-09-25T12:00:00Z",
  scope: "global",
  query: "Arrivals.",
  sql: "SELECT 1",
};
const early = earlySignals.map((row) =>
  earlySignal.parse({ ...row, playlist_count: "3" }),
);
const lead = {
  day: "2026-09-26",
  song_key: "fixture-arrival-1",
  title_text: "Low light",
  artist_text: "Test recording",
  window_days: 5,
  movement_list: "new_entries",
  entered_lists: "1",
  reason_rule: "Entered tracked lists or charts over 5 days; open evidence.",
  learning_eligible: false,
  resale_permitted: false,
  age_basis: "isrc_year",
  discovery: ["united-kingdom", "united-states", "canada"],
};
describe("readiness", () => {
  it("dates the two-source ranking only while it is still ahead", () => {
    const young = readiness(true).map((row) => readinessRow.parse(row));
    const settled = readiness(false).map((row) => readinessRow.parse(row));
    expect(rankingLine(young, true)).toMatch(
      /^two-source ranking from \d+ \w+\.$/,
    );
    expect(rankingLine(settled, true)).toBe(
      "no song moves on two sources yet.",
    );
    expect(rankingLine(settled, false)).toBeNull();
    expect(rankingLine(undefined, false)).toBeNull();
  });
  it("counts a family's first week and maps answers to their family", () => {
    const settled = readiness(false).map((row) => readinessRow.parse(row));
    expect(warmingDays(settled, "streams")).toBe(3);
    expect(warmingDays(settled, "playlists")).toBeNull();
    expect(warmingLabel(3)).toBe("building history · 3 of 7 days");
    expect(answerWarming("mart_track_daily_streams", settled)).toBe(3);
    expect(answerWarming("mart_top_movers_current", settled)).toBeNull();
    const young = readiness(true).map((row) => readinessRow.parse(row));
    expect(answerWarming("mart_top_movers_current", young)).toBe(1);
    expect(answerWarming("mart_chart_history", young)).toBeNull();
  });
});
describe("card facts", () => {
  it("states each family's plain number", () => {
    expect(early.map(earlyFact)).toEqual([
      "Seen on 3 playlists.",
      "Added playlists total 18,400 followers.",
      "On 4 more Shazam charts.",
      "Plays up 42%.",
      "Seen on 3 playlists.",
    ]);
    expect(earlyFact({ ...early[0], playlist_count: "0" })).toBe(
      "Open the song for its evidence.",
    );
  });
  it("calls a head-only sighting seen and a full-list add added", () => {
    const row = early[0];
    const proof = row.evidence?.[0];
    if (!proof) throw new Error("Fixture needs playlist evidence.");
    const head = {
      ...proof,
      component: "playlist_adds",
      row_key: { event_type: "entered_head" },
    };
    const add = { ...head, row_key: { event_type: "add" } };
    expect(earlyFact({ ...row, evidence: [head] })).toBe(
      "Seen on 3 playlists.",
    );
    expect(earlyFact({ ...row, evidence: [head, add] })).toBe(
      "Added to 3 playlists.",
    );
    expect(earlyFact({ ...row, evidence: [] })).toBe("Seen on 3 playlists.");
  });
  it("shows measured artist coverage beside the current list", () => {
    const rows = readiness(false).map((row) => readinessRow.parse(row));
    expect(artistSizeLine(rows, "new_entries")).toBe("artist size known: 2 of 5");
    expect(artistSizeLine(undefined, "new_entries")).toBeNull();
    const html = renderToStaticMarkup(
      <Rising
        days={7}
        movers={[]}
        stageCoverage={artistSizeLine(rows, "new_entries")}
      />,
    );
    expect(html).toContain("artist size known: 2 of 5");
  });
  it("leads Places with new markets, then lists", () => {
    const rows = arrivals
      .filter((row) => row.movement_list === "catalog_entries")
      .map((row) => arrival.parse(row));
    expect(rows.map(arrivalFact)).toEqual([
      "Reached 3 new markets.",
      "Entered 2 tracked lists.",
    ]);
    expect(rows[0].markets).toEqual(["BR", "JP", "MX"]);
  });
  it("labels list-based new music with unknown age", () => {
    expect(listTag("new_entries", "none")).toBe("new list");
    expect(listTag("new_entries", null)).toBe("new list");
    expect(listTag("new_entries", "apple_id_band")).toBe("new");
    const html = renderToStaticMarkup(
      <Arrivals
        summary={arrivalSummary.parse({
          songs: "1",
          window_days: 3,
          lead: { ...lead, age_basis: "none" },
        })}
        provenance={p}
      />,
    );
    expect(html).toContain("new list");
    expect(html).toContain("age unknown");
  });
  it("tags only the catalog list as catalog and names chart countries", () => {
    expect(listTag("catalog_entries", "isrc_year")).toBe("catalog");
    expect(listTag("new_entries", "isrc_year")).toBe("new");
    expect(countryName("united-kingdom")).toBe("UK");
    expect(countryName("new-zealand")).toBe("New Zealand");
  });
});
describe("arrivals", () => {
  it("parses the summary row as the warehouse returns it", () => {
    expect(
      arrivalSummary.parse({ songs: "296", window_days: 5, lead }).lead
        ?.discovery,
    ).toEqual(lead.discovery);
    expect(
      arrivalSummary.parse({
        songs: "0",
        window_days: null,
        lead: JSON.stringify(null),
      }).lead,
    ).toBeNull();
  });
  it("leads Home with the count, one tag, its window and the Discovery song", () => {
    const html = renderToStaticMarkup(
      <Arrivals
        summary={arrivalSummary.parse({
          songs: "296",
          window_days: 5,
          lead,
        })}
        provenance={p}
      />,
    );
    for (const text of [
      "296",
      "new songs",
      "over 5 days",
      "Shazam Discovery",
      "UK · US · Canada",
      "Open Rising now",
    ])
      expect(html).toContain(text);
    expect(html.match(/data-primary/g)).toHaveLength(1);
  });
  it("says the window is quiet when no new song arrived", () => {
    const html = renderToStaticMarkup(
      <Arrivals
        summary={arrivalSummary.parse({
          songs: "0",
          window_days: null,
          lead: null,
        })}
        provenance={p}
      />,
    );
    expect(html).toContain("a quiet window.");
    expect(html).toContain("Open Rising now");
  });
});
describe("view lists", () => {
  it("groups Rising's early signals by family with their numbers", () => {
    const html = renderToStaticMarkup(
      <Rising
        days={7}
        movers={[]}
        early={{
          rows: early.filter((row) => row.movement_list === "new_entries"),
          ranking: "no song moves on two sources yet.",
        }}
      />,
    );
    expect(html).toContain("rising on one kind of list so far");
    expect(html.indexOf("Playlists</h2>")).toBeLessThan(
      html.indexOf("Shazam cities</h2>"),
    );
    expect(html).toContain("Plays up 42%.");
    expect(html).toContain("no song moves on two sources yet.");
  });
  it("keeps Places's lists apart and says when established artists arrive", () => {
    const rows = arrivals
      .filter((row) => row.movement_list !== "new_entries")
      .map((row) => arrival.parse(row));
    const html = renderToStaticMarkup(<Places view="waking" rows={rows} />);
    expect(html.indexOf(">established artists</h2>")).toBeGreaterThan(
      html.indexOf("catalog waking up"),
    );
    expect(html).toContain("Reached 3 new markets.");
    expect(html).toContain("Needs artist audience size");
    expect(html).not.toContain("Unplaced fixture");
    expect(listTag("unplaced", "none")).toBeNull();
  });
  it("says a list is empty in one quiet line once artist size is measured", () => {
    const measured = arrivals
      .filter((row) => row.movement_list === "catalog_entries")
      .map((row) => arrival.parse({ ...row, stage_measured: true }));
    const html = renderToStaticMarkup(<Places view="waking" rows={measured} />);
    expect(html).toContain(
      "No established artist&#x27;s new song entered a list yet.",
    );
    expect(html).not.toContain("Needs artist audience size");
    const empty = renderToStaticMarkup(<Places view="waking" rows={[]} />);
    expect(empty).toContain("No older song entered a tracked list yet.");
    expect(empty.match(/Open Rising now/g)).toHaveLength(2);
  });
});

it.each([0, 1, 5, 6])(
  "keeps early signals below %i movers only while fewer than six",
  (count) => {
    const rows = Array.from({ length: count }, (_, i) =>
      mover.parse({ ...movers[0], song_key: `mover-${i}` }),
    );
    const html = renderToStaticMarkup(
      <Rising days={7} movers={rows} early={{ rows: early, ranking: null }} />,
    );
    if (count < 6) expect(html).toContain("rising on one kind of list so far");
    else expect(html).not.toContain("rising on one kind of list so far");
    if (count > 0 && count < 6)
      expect(html.indexOf("mover-0")).toBeLessThan(
        html.indexOf("rising on one kind of list so far"),
      );
  },
);
