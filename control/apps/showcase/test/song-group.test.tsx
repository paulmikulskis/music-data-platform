import { beforeEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { build } from "./browser/fixtures";

// A matched Apple copy and Spotify copy, as production's Synthetic Artist.
const key = [String("2caf972b-8337-4126-87a1-a0f53987e105")].join("");
const other = "spotify:FixtureTrack0000000601";
const state = vi.hoisted(() => {
  const asked: string[] = [];
  return { otherBuilt: "", failOther: false, asked };
});
const day = (song_key: string, date: string, editorial_adds: number) => ({
  song_key,
  title_text: "Synthetic Artist",
  artist_text: "Fixture Duo",
  learning_eligible: false,
  resale_permitted: false,
  source_keys: "[]",
  day: date,
  editorial_adds,
  algorithmic_adds: 0,
  shazam_cities: "0",
  stream_rate: null,
  playlist_followers: "0",
});
vi.mock("server-only", () => ({}));
vi.mock("../server/room", () => ({
  room: async () => ({ person: {}, csrf_token: "fixture" }),
  unavailable: () => null,
}));
vi.mock("../server/platform", () => ({
  tending: async () => ({ sources: [], songs: null }),
}));
vi.mock("../server/proof-store", () => ({
  capturedProof: () => null,
  rememberProofs: () => {},
  rememberHistory: () => {},
}));
vi.mock("../server/call-offers", () => ({
  callOffers: async () => ({}),
  songProjection: () => null,
}));
vi.mock("../server/reads", async (original) => {
  const reads = await original<typeof import("../server/reads")>();
  const history = (song: string) => ({
    value: {
      rows: [day(song, "2026-09-26", song === key ? 0 : 1)],
      build:
        song === key || !state.otherBuilt
          ? build("mart_song_day")
          : { ...build("mart_song_day"), built_at: state.otherBuilt },
      queried_at: "2026-09-26T21:00:00Z",
      sql: "SELECT strict",
    },
  });
  return {
    ...reads,
    alias: async () => ({ value: { rows: [] } }),
    songMovement: async () => null,
    songGroup: async () => ({
      value: {
        rows: [key, other].map((song_key) => ({
          song_key,
          cluster_key: key,
          representative_song_key: key,
          cluster_methods: '["isrc_crosswalk","title_artist_duration"]',
          learning_eligible: false,
          resale_permitted: false,
          source_keys: "[]",
        })),
      },
    }),
    playlistDays: async (songs: string[]) => ({
      value: {
        rows: songs.includes(other)
          ? [{ day: "2026-09-26", playlists: 1 }]
          : [],
        build: build("mart_playlist_events"),
        queried_at: "2026-09-26T21:00:00Z",
        sql: "SELECT distinct lists",
      },
    }),
    songHistory: async (song: string) => {
      state.asked.push(song);
      if (song !== key && state.failOther) throw new Error("offline");
      return history(song);
    },
    identities: async (keys: string | string[]) => ({
      value: {
        rows: (typeof keys === "string" ? [keys] : keys).map((song_key) => ({
          platform: song_key === key ? "apple" : "spotify",
          platform_track_id: song_key,
          resolved: song_key === key,
          song_key,
        })),
        build: build("int_song_key__daily"),
        queried_at: "2026-09-26T21:00:00Z",
        sql: "SELECT copies",
      },
    }),
    movementReadiness: async () => null,
    songPlaces: async () => null,
  };
});
vi.mock("../components/shell", () => ({
  Shell: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("../components/song", () => ({
  Song: ({
    days,
    copies,
    match,
    provenance,
  }: {
    days: { editorial_adds: number | null }[];
    copies: unknown[];
    match?: { copies: number };
    provenance: { sql: string };
  }) => (
    <p>
      adds:{days.map((d) => d.editorial_adds).join(",")} copies:
      {copies.length} match:{match?.copies ?? "none"} sql:{provenance.sql}
    </p>
  ),
}));
import Page from "../app/s/song/[key]/page";
const render = async () =>
  renderToStaticMarkup(
    await Page({
      params: Promise.resolve({ key }),
      searchParams: Promise.resolve({}),
    }),
  );
beforeEach(() => {
  state.otherBuilt = "";
  state.failOther = false;
  state.asked = [];
});
it("reads a colon key decoded, as every link encodes it and Next passes it on encoded", async () => {
  const html = renderToStaticMarkup(
    await Page({
      params: Promise.resolve({ key: encodeURIComponent(other) }),
      searchParams: Promise.resolve({}),
    }),
  );
  expect(state.asked).toContain(other);
  expect(state.asked.some((song) => song.includes("%"))).toBe(false);
  expect(html).toContain("match:2");
});
it("reads a matched song as one: added days, every copy and the card's count", async () => {
  const html = await render();
  expect(html).toContain("adds:1");
  expect(html).toContain("copies:2");
  expect(html).toContain("match:2");
  expect(html).toContain("sum(d.editorial_adds)");
  expect(html).toContain("bool_and(d.learning_eligible)");
  expect(html).toContain("AS source_keys");
  expect(html).toContain("IN (E&#x27;2caf972b");
});
it("keeps the key alone when a copy's read fails or comes from another build", async () => {
  state.failOther = true;
  expect(await render()).toContain("match:none");
  state.failOther = false;
  state.otherBuilt = "2026-09-27T01:00:00.000Z";
  const html = await render();
  expect(html).toContain("adds:0");
  expect(html).toContain("match:none");
  expect(html).toContain("sql:SELECT strict");
});
