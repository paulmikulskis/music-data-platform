import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { platformSource } from "@mdp/contracts";
import { martBuild } from "@mdp/data-sdk";
import { mover, songDay, type SongDay } from "../server/models";
import { Art, MoverCard } from "../components/movers";
import { SongCard } from "../components/arrivals";
import { Song } from "../components/song";
import { CallsBoard } from "../components/calls-board";
import type { HomeCalls } from "../server/call-offers";
import { TendingProvider } from "../components/sources";
import { groupDays, matchedLine } from "../lib/cluster";
import { build, sources } from "./browser/fixtures";

// Synthetic: Synthetic Artist is an Apple copy matched to a Spotify copy, and its mart row
// names every source upstream of the movers table.
const key = [String("00000000-0000-4000-8000-000000000601")].join("");
const members = [key, "spotify:FixtureTrack0000000601"];
const locator = (
  component: string,
  relation: string,
  row_key: Record<string, unknown>,
) => ({
  component,
  relation,
  row_key,
  window: { start: "2026-09-25T02:54:39", end: "2026-09-26T02:54:41" },
  cluster_key: key,
  member_song_keys: members,
  cluster_methods: ["isrc_crosswalk", "title_artist_duration"],
  cluster_confidence: 1,
  input_build: {
    relation: `marts.${relation}`,
    scope: "global",
    cycle_id: "00000000-0000-4000-8000-000000000602",
    close_no: "1",
    built_at: "2026-09-26T17:27:34Z",
  },
});
const add = {
  platform: "spotify",
  playlist_id: "37i9dQZF1DX10zKzsJ2jva",
  event_type: "add",
};
const mover0 = mover.parse({
  rank: "1",
  day: "2026-09-26",
  song_key: key,
  title_text: "Synthetic Artist",
  artist_text: "Fixture Duo",
  momentum_score: 0.9,
  score_parts: JSON.stringify([
    { component: "follower_exposure_gain", value: 15127721, window_days: 3 },
    { component: "playlist_adds", value: 3, window_days: 3 },
  ]),
  coverage: "[]",
  reason_rule: "Moves on playlists and Shazam.",
  ranking_build: "fixture-ranking",
  learning_eligible: false,
  resale_permitted: false,
  source_keys: JSON.stringify([
    "am_playlist",
    "bc_daily_list",
    "billboard_hot100",
    "sp_playlist",
    "sz_chart",
  ]),
  movement_list: "new_entries",
  window_days: 3,
  playlist_count: "1",
  evidence: JSON.stringify([
    locator("follower_exposure_gain", "mart_playlist_events", add),
    locator("playlist_adds", "mart_playlist_events", add),
    locator("shazam_spread_gain", "mart_shazam_chart_daily", {
      chart: "shazam:top-200:mexico",
    }),
  ]),
});
const tended = (missingArt: string[] = []) => ({
  sources: sources.map((source) => platformSource.parse(source)),
  songs: "8185",
  missingArt,
});

describe("artwork", () => {
  it("keeps a cover hidden over the monogram until it loads", () => {
    const html = renderToStaticMarkup(<Art song={key} title="Synthetic Artist" />);
    expect(html).toContain('data-art="loading"');
    expect(html).toContain(`src="/art/${key}"`);
    expect(html).toContain("/brand/mdp-monogram.svg");
    expect(html).not.toContain("Artwork unavailable");
  });
  it("never asks for a cover the server knows is missing", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={tended([key])}>
        <Art song={key} title="Synthetic Artist" />
      </TendingProvider>,
    );
    expect(html).toContain('data-art="failed"');
    expect(html).not.toContain("/art/");
    expect(html).toContain("Artwork unavailable");
  });
});

describe("source badges", () => {
  it("name only the sources that observed the song", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={tended()}>
        <MoverCard mover={mover0} />
      </TendingProvider>,
    );
    expect(html).toContain("Spotify. Open source");
    expect(html).toContain("Shazam. Open source");
    for (const unseen of ["Bandcamp", "Billboard", "Apple Music"])
      expect(html).not.toContain(`${unseen}. Open source`);
  });
  it("show none when a row carries no evidence", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={tended()}>
        <SongCard
          song="apple:9000000601"
          title="Fixture Song"
          artist="Artist"
          list="catalog_entries"
          days={5}
          fact="Reached 3 new markets."
          source="Counted from charts."
          basis={null}
          evidence={[]}
        />
      </TendingProvider>,
    );
    expect(html).not.toContain("Open source");
  });
});

describe("matched song page", () => {
  const day = (
    song_key: string,
    date: string,
    values: Partial<SongDay>,
  ): SongDay =>
    songDay.parse({
      song_key,
      title_text: "Synthetic Artist",
      artist_text: "Fixture Duo",
      learning_eligible: false,
      resale_permitted: false,
      source_keys: '["am_playlist"]',
      day: date,
      editorial_adds: 0,
      algorithmic_adds: 0,
      shazam_cities: null,
      stream_rate: null,
      playlist_followers: "0",
      ...values,
    });
  const apple = [
    day(key, "2026-09-25", { shazam_cities: "0" }),
    day(key, "2026-09-26", { shazam_cities: "2" }),
  ];
  const spotify = [
    day(members[1], "2026-09-25", { playlist_followers: "6940525" }),
    day(members[1], "2026-09-26", {
      editorial_adds: 1,
      playlist_followers: "17438334",
      shazam_cities: "0",
      source_keys: '["sp_playlist"]',
    }),
  ];
  it("adds its copies' days under its own key", () => {
    const merged = groupDays(key, [apple, spotify]);
    expect(merged.map((row) => row.day)).toEqual(["2026-09-25", "2026-09-26"]);
    expect(merged[1]).toMatchObject({
      song_key: key,
      editorial_adds: 1,
      shazam_cities: "2",
      playlist_followers: "17438334",
      stream_rate: null,
      source_keys: '["am_playlist","sp_playlist"]',
    });
  });
  it("agrees with its card: the add, the cities and one copy count", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={tended()}>
        <Song
          song={mover0}
          days={groupDays(key, [apple, spotify])}
          copies={[
            {
              platform: "apple",
              platform_track_id: "9000000602",
              resolved: true,
              song_key: key,
            },
            {
              platform: "spotify",
              platform_track_id: "FixtureTrack0000000601",
              resolved: false,
              song_key: members[1],
            },
          ]}
          provenance={{
            queried_at: "2026-09-26T21:00:00Z",
            scope: "global",
            query: "Daily observations.",
            sql: "SELECT 1",
            build: martBuild.parse(build("mart_song_day")),
          }}
          match={{
            copies: 2,
            methods: ["isrc_crosswalk", "title_artist_duration"],
          }}
        />
      </TendingProvider>,
    );
    expect(matchedLine(2, ["isrc_crosswalk", "title_artist_duration"])).toBe(
      "2 copies matched: same ISRC, same title and artist.",
    );
    expect(html).toContain("Same song on 2 platforms");
    expect(html).not.toContain("one copy found");
    expect(html).not.toContain("Unresolved");
    expect(html).toContain("1 playlist");
    expect(html).toContain("2 cities");
  });
});

describe("empty calls week", () => {
  const board = (callable: HomeCalls) =>
    renderToStaticMarkup(
      <CallsBoard
        calls={[]}
        observations={null}
        handle="fixture"
        week="2026-09-25"
        current
        history={[]}
        callable={callable}
      />,
    );
  it("says why no song can be called and where calls open", () => {
    expect(board("waiting")).toContain("Picks open after the next daily read.");
    expect(board("waiting")).toContain('href="/"');
    expect(board("open")).toContain("Save a song you expect to spread.");
    expect(board("no_songs")).toContain("Home has no song to pick yet.");
    expect(board("no_songs")).toContain('href="/songs?view=rising"');
    expect(board("used")).toContain("This week&#x27;s pick limit is reached");
    expect(board("used")).toContain(
      'href="/songs?view=picks&amp;week=2026-09-18"',
    );
    const states: HomeCalls[] = [
      "open",
      "used",
      "waiting",
      "no_songs",
      "unknown",
    ];
    for (const state of states)
      expect(board(state)).toContain(
        "Save a song you expect to spread.",
      );
  });
});
