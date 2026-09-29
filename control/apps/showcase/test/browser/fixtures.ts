// Browser-test data only. The application has no fixture mode or fallback dataset.
import { sourceWording } from "@mdp/contracts/source-wording";
const today = new Date().toISOString().slice(0, 10);
const utcDay = Date.parse(today + "T00:00:00Z");
const dayOffset = (offset: number) =>
  new Date(utcDay + offset * 86400000).toISOString().slice(0, 10);
const weekOffset = (new Date(utcDay).getUTCDay() + 6) % 7;
export const at = today + "T06:12:00.000Z";
export const cycle = "00000000-0000-4000-8000-000000000041";
export const build = (relation: string) => ({
  relation: `marts.${relation}`,
  scope: "global" as const,
  tenant_slug: null,
  stamped: true,
  cycle_id: cycle,
  close_no: "41",
  built_at: at,
});
export const keys = [
  "00000000-0000-4000-8000-000000000101",
  "00000000-0000-4000-8000-000000000102",
  "00000000-0000-4000-8000-000000000103",
];
export const movers = keys.map((song_key, i) => ({
  rank: String(i + 1),
  day: today,
  song_key,
  title_text: ["Night tide", "Slow return", "After the rain"][i],
  artist_text: "Test recording",
  reason_rule: "Editorial support meets wider city discovery.",
  ranking_build: "fixture-ranking",
  window_days: 3,
  movement_list: "new_entries",
  age_class: "new",
  age_basis: "isrc_year",
  artist_stage: "unknown",
  artist_stage_basis: "none",
  chart_spread_gain: "0",
  list_reach_tier: 2,
  market_count: "1",
  last_entered_at: at,
  momentum_score: 0.8,
  coverage: '["playlists","shazam"]',
  score_parts: JSON.stringify([
    { component: "stream_rate_gain", value: 0.38, window_days: 2 },
    { component: "follower_exposure_gain", value: 1200 },
  ]),
  learning_eligible: false,
  resale_permitted: false,
  source_keys: '["sp_playlist","sz_chart","sp_track_plays"]',
  evidence: JSON.stringify(
    [
      "playlist_adds",
      "shazam_spread_gain",
      "stream_rate_gain",
      "follower_exposure_gain",
    ].map((component, j) => ({
      cluster_key: song_key,
      member_song_keys: [song_key, `${song_key}-copy-a`, `${song_key}-copy-b`],
      cluster_methods: ["isrc_crosswalk", "title_artist_duration"],
      cluster_confidence: 1,
      component,
      relation: [
        "mart_playlist_events",
        "mart_shazam_chart_daily",
        "mart_track_daily_streams",
        "mart_playlist_profile",
      ][j],
      row_key:
        component === "playlist_adds"
          ? {
              platform: "spotify",
              playlist_id: "fixture-list",
              variant: "default",
              stream: "full",
              occurrence_key: "trace-occurrence",
              interval_id: "trace-interval",
              event_type: "add",
              observed_at: at,
            }
          : component === "follower_exposure_gain"
            ? {
                platform: "spotify",
                playlist_id: "fixture-list",
                variant: "default",
                stream: "full",
                snapshot_id: "trace-snapshot",
              }
            : component === "stream_rate_gain"
              ? {
                  platform: "spotify",
                  platform_track_id: "trace-track",
                  day: today,
                }
              : {
                  chart: "shazam:city:us:new-york",
                  chart_date: today,
                  position: 1,
                },
      window: { start: dayOffset(-6), end: today },
      input_build: build(
        [
          "mart_playlist_events",
          "mart_shazam_chart_daily",
          "mart_track_daily_streams",
          "mart_playlist_profile",
        ][j],
      ),
    })),
  ),
}));
export const days = Array.from({ length: 28 }, (_, i) => ({
  song_key: keys[0],
  title_text: "Night tide",
  artist_text: "Test recording",
  day: dayOffset(i - 27),
  editorial_adds: i === 11 ? null : Math.max(0, Math.floor(i / 5)),
  algorithmic_adds: 0,
  shazam_cities: i === 9 ? null : String(Math.floor(i / 3)),
  playlist_followers: String(i * 100),
  stream_rate: null,
  learning_eligible: false,
  resale_permitted: false,
  source_keys: '["fixture"]',
}));
export const holdings = {
  queried_at: at,
  since: dayOffset(-weekOffset) + "T00:00:00.000Z",
  warehouse: "fixture",
  ingestion: {
    summary: {
      rows_inserted: String(
        (weekOffset + 1) * 14000 + weekOffset * (weekOffset + 1) * 1000,
      ),
      sources_live: "3",
      today_rows: String(14000 + weekOffset * 2000),
      today_sources: "3",
      today_measured: true,
      days: Array.from({ length: weekOffset + 1 }, (_, i) => ({
        day: dayOffset(i - weekOffset),
        rows_inserted: String(14000 + i * 2000),
      })),
    },
    rows: Array.from({ length: weekOffset + 1 }, (_, i) =>
      ["fixture-a", "fixture-b", "fixture-c"].map((source_key, j) => ({
        day: dayOffset(i - weekOffset),
        source_key,
        rows_inserted: String(j === 2 ? 14000 + i * 2000 - 8000 : 4000),
      })),
    ).flat(),
    next_step: "Open /ops.",
  },
  inventory_now: {
    state: "available",
    layers: [
      {
        layer: "marts",
        relations: 3,
        rows_est: "140000",
        bytes: "100000",
        complete: true,
      },
    ],
    next_step: "Open /explorer.",
  },
  inventory_history: {
    first_snapshot_day: today,
    unavailable_before: "2026-09-25",
    days: [
      {
        day: today,
        state: "available",
        layers: [
          {
            layer: "marts",
            relations: 3,
            rows_est: "140000",
            bytes: "100000",
            complete: true,
            captured_at: at,
          },
        ],
      },
    ],
    next_step: "Open /explorer.",
  },
  vendor_cost: {
    cost_cents: "0",
    has_current_rows: false,
    label: "estimate",
    days: [],
    infrastructure_included: false,
    next_step: "Open /ops.",
  },
};


// Movement v2 relations, one row per contract column. Shazam charts use their page slug, as landed.
const rights = {
  learning_eligible: false,
  resale_permitted: false,
  source_keys: '["sp_playlist","am_playlist","sz_chart"]',
};
const age = (list: string) => ({
  age_class:
    list === "catalog_entries"
      ? "catalog"
      : list === "unplaced" || list === "new_entries"
        ? "unknown"
        : "new",
  age_basis: list === "unplaced" || list === "new_entries" ? "none" : "isrc_year",
  artist_stage: "unknown",
  artist_stage_basis: "none",
});
type Locator = {
  component: string;
  relation: string;
  row_key: Record<string, unknown>;
  window: { start: string; end: string };
  input_build: ReturnType<typeof build>;
};
const shazam = (country: string, position: number): Locator => ({
  component: "arrivals",
  relation: "mart_shazam_chart_daily",
  row_key: {
    chart: `shazam:discovery:${country}`,
    chart_date: dayOffset(-1),
    position,
  },
  window: { start: dayOffset(-2), end: dayOffset(-1) },
  input_build: build("mart_shazam_chart_daily"),
});
const playlist = (id: string): Locator => ({
  component: "arrivals",
  relation: "mart_playlist_events",
  row_key: { platform: "spotify", playlist_id: id },
  window: { start: dayOffset(-4), end: dayOffset(-1) },
  input_build: build("mart_playlist_events"),
});
const arrival = (
  song_key: string,
  rank: number,
  movement_list: string,
  title_text: string,
  evidence: Locator[],
  extra: Record<string, unknown> = {},
) => ({
  song_key,
  rank: String(rank),
  day: today,
  title_text,
  artist_text: "Test recording",
  markets: "[]",
  entered_lists: String(
    evidence.filter((e) => e.relation === "mart_playlist_events").length,
  ),
  entered_charts: String(
    evidence.filter((e) => e.relation === "mart_shazam_chart_daily").length,
  ),
  reason_rule: "Entered tracked lists or charts over 5 days; open evidence.",
  evidence: JSON.stringify(evidence),
  window_days: 5,
  chart_spread_gain: "0",
  list_reach_tier: 2,
  market_count: "0",
  last_entered_at: at,
  movement_list,
  ...age(movement_list),
  ...rights,
  ...extra,
});
export const arrivals = [
  arrival("fixture-arrival-1", 2, "new_entries", "Low light", [
    shazam("united-states", 3),
    shazam("united-kingdom", 1),
    shazam("canada", 7),
    playlist("fixture-fresh"),
  ]),
  arrival("fixture-arrival-2", 1, "new_entries", "Open water", [
    shazam("germany", 2),
    playlist("fixture-new-music"),
  ]),
  ...Array.from({ length: 294 }, (_, i) =>
    arrival(
      `fixture-arrival-${i + 3}`,
      i + 3,
      "new_entries",
      `Fixture arrival ${i + 3}`,
      [playlist(`fixture-list-${i % 12}`)],
    ),
  ),
  arrival(
    "fixture-catalog-1",
    1,
    "catalog_entries",
    "Old flame",
    [playlist("fixture-top-50")],
    { chart_spread_gain: "3", market_count: "3", markets: '["BR","JP","MX"]' },
  ),
  arrival("fixture-catalog-2", 2, "catalog_entries", "Second summer", [
    playlist("fixture-top-50"),
    playlist("fixture-top-100"),
  ]),
  arrival("fixture-unplaced-1", 1, "unplaced", "Unplaced fixture", [
    playlist("fixture-list-1"),
  ]),
];
const early = (
  song_key: string,
  family: string,
  rank: number,
  movement_list: string,
  title_text: string,
  component: string,
  value: number,
  evidence: Locator[],
) => ({
  family,
  rank: String(rank),
  day: today,
  song_key,
  title_text,
  artist_text: "Test recording",
  component,
  value,
  reason_rule: "Moves on one source over 5 days; open evidence.",
  evidence: JSON.stringify(evidence),
  window_days: 5,
  chart_spread_gain: "0",
  list_reach_tier: 2,
  market_count: "0",
  last_entered_at: at,
  movement_list,
  ...age(movement_list),
  ...rights,
});
const add = (id: string): Locator => ({
  ...playlist(id),
  component: "playlist_adds",
});
export const earlySignals = [
  early(
    "fixture-early-1",
    "playlists",
    1,
    "new_entries",
    "Paper moon",
    "playlist_adds",
    7,
    [add("fixture-a"), add("fixture-b"), add("fixture-c"), add("fixture-a")],
  ),
  early(
    "fixture-early-2",
    "playlists",
    2,
    "new_entries",
    "Glass house",
    "follower_exposure_gain",
    18400,
    [{ ...playlist("fixture-d"), component: "follower_exposure_gain" }],
  ),
  early(
    "fixture-early-3",
    "shazam",
    1,
    "new_entries",
    "Blue hour",
    "shazam_spread_gain",
    4,
    [{ ...shazam("france", 9), component: "shazam_spread_gain" }],
  ),
  early(
    "fixture-early-4",
    "streams",
    1,
    "new_entries",
    "Night drive",
    "stream_rate_gain",
    0.42,
    [],
  ),
  early(
    "fixture-early-5",
    "playlists",
    1,
    "catalog_entries",
    "Catalog fixture",
    "playlist_adds",
    3,
    [add("fixture-e")],
  ),
];
// Young history puts the two-source ranking in the future; settled history leaves only streams warming up.
const ready = (
  family: string,
  history_days: number,
  first_rank_day: string | null,
) => ({
  family,
  day: today,
  history_days: String(history_days),
  history_needed: "Collect more days; open song history.",
  first_rank_day,
  ...rights,
});
export const readiness = (young: boolean) => [
  ...[
    {
      ...ready("playlists", 9, dayOffset(-8)),
      movement_list: null,
      stage_known_songs: null,
      list_songs: null,
    },
    young
      ? ready("shazam", 1, dayOffset(1))
      : ready("shazam", 9, dayOffset(-8)),
    young ? ready("streams", 0, null) : ready("streams", 3, dayOffset(3)),
  ].map((row) => ({
    ...row,
    movement_list: null,
    stage_known_songs: null,
    list_songs: null,
  })),
  ...[
    {
      ...ready("artist_stage:new_entries", 0, null),
      movement_list: "new_entries",
      stage_known_songs: "2",
      list_songs: "5",
    },
  ],
];

// What platform.sources answers for the fixture: counts per source, 14 days each, in the shared wording.
// Apple Music last read yesterday and read nothing today; Spotify's daily reader of weekly lists last succeeded five days ago.
const sourceDays = (base: number, lastDay: number) =>
  Array.from({ length: 14 }, (_, i) => ({
    day: dayOffset(i - 13),
    entries: String(
      i < 2 || i > 13 + lastDay ? 0 : base + (i % 4) * Math.round(base / 10),
    ),
  }));
const source = (
  source_key: string,
  targets: number | null,
  entries: number,
  first: number,
  lastDay = 0,
  cadence = "daily",
) => {
  const wording = sourceWording[source_key]!;
  return {
    source_key,
    display_name: wording.name,
    brand: wording.brand,
    family: wording.family,
    description: wording.plain,
    cadence,
    enabled: true,
    first_collected: dayOffset(first) + "T03:10:00.000Z",
    last_read: dayOffset(lastDay) + "T03:12:00.000Z",
    entries_today: String(lastDay === 0 ? entries : 0),
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
      last_success: dayOffset(lastDay) + "T03:12:00.000Z",
      attempt: null,
      incident: null,
    },
    days: sourceDays(entries, lastDay),
  };
};
export const sources = [
  source("sz_chart", 58, 5800, -11),
  source("sp_playlist", 36, 1240, -12),
  source("am_playlist", 48, 1100, -12, -1),
  source("mb_spine", null, 400, -10),
  source("sp_playlist_weekly", 20, 300, -12, -5, "daily"),
];
// Song copies and lead artists for the song, artist cards and found-on badges.
export const copies = [
  { platform: "spotify", platform_track_id: "FixtureTrack0000000001" },
  { platform: "apple", platform_track_id: "1440857781" },
  { platform: "deezer", platform_track_id: "3135556" },
];
export const artistIdentity = {
  platform: "spotify",
  platform_artist_id: "FixtureArtist000000001",
  mb_artist_gid: "00000000-0000-4000-8000-000000000301",
  mb_artist_name: "Test recording",
  candidate_count: 1,
  method: "mb_url",
  evidence: "https://open.spotify.com/artist/FixtureArtist000000001",
  wikidata_qid: "Q42",
};
// Shazam charts the first song reached, by apple song id.
export const chartPlaces = [
  {
    chart: "shazam:top-50:united-states:new-york-city",
    chart_type: "top-50",
    country: "united-states",
    city: "new-york-city",
    position: 12,
    day: -5,
  },
  {
    chart: "shazam:top-50:united-kingdom:london",
    chart_type: "top-50",
    country: "united-kingdom",
    city: "london",
    position: 20,
    day: -4,
  },
  {
    chart: "shazam:top-50:brazil:s%C3%A3o-paulo",
    chart_type: "top-50",
    country: "brazil",
    city: "s%C3%A3o-paulo",
    position: 31,
    day: -3,
  },
  {
    chart: "shazam:top-50:japan:tokyo",
    chart_type: "top-50",
    country: "japan",
    city: "tokyo",
    position: 44,
    day: -2,
  },
  {
    chart: "shazam:top-200:germany",
    chart_type: "top-200",
    country: "germany",
    city: null,
    position: 88,
    day: -1,
  },
].map(({ day, ...row }) => ({ ...row, chart_date: dayOffset(day) }));
