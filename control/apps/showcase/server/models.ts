import { z } from "zod";
import {
  martBuild,
  mart_top_movers_current,
  mart_song_day,
  mart_arrivals_current,
  mart_early_signals_current,
  mart_readiness,
} from "@mdp/data-sdk";
export const locator = z.object({
  folded_song_keys: z.array(z.string()).optional(),
  cluster_key: z.string().optional(),
  member_song_keys: z.array(z.string()).optional(),
  cluster_methods: z.array(z.string()).optional(),
  cluster_confidence: z.number().optional(),
  component: z.string(),
  relation: z.string(),
  row_key: z.record(z.string(), z.unknown()),
  window: z.unknown(),
  input_build: z.object({
    relation: z.string(),
    scope: z.string(),
    cycle_id: z.string().nullable(),
    close_no: z.string().nullable(),
    built_at: z.string().nullable(),
  }),
});
const json = <T extends z.ZodType>(schema: T) =>
  z.preprocess(
    (value) => (typeof value === "string" ? JSON.parse(value) : value),
    schema,
  );
const day = z.preprocess(
  (value) => (value instanceof Date ? value.toISOString().slice(0, 10) : value),
  z.string(),
);
// The showcase reads the columns it shows, so new mart columns never break it.
export const mover = mart_top_movers_current
  .pick({
    rank: true,
    day: true,
    song_key: true,
    title_text: true,
    artist_text: true,
    momentum_score: true,
    score_parts: true,
    coverage: true,
    reason_rule: true,
    evidence: true,
    ranking_build: true,
    learning_eligible: true,
    resale_permitted: true,
    source_keys: true,
    movement_list: true,
  })
  .extend({
    score_parts: json(
      z
        .array(
          z.object({
            component: z.string(),
            value: z.number(),
            weight: z.number().optional(),
            percentile: z.number().optional(),
            window_days: z.number().optional(),
          }),
        )
        .nullable(),
    ).transform((value) => value ?? []),
    age_basis: z.string().nullable().default(null),
    playlist_count: z.string().nullable().default(null),
    // The adaptive window; older rows and fixtures without it read null.
    window_days: z.number().int().nullable().default(null),
    day,
    song_key: z.string(),
    reason_rule: z.string(),
    ranking_build: z.string(),
    coverage: json(z.unknown()),
    evidence: json(z.array(locator)),
  });
export type Mover = z.infer<typeof mover>;
export type Locator = z.infer<typeof locator>;
// The fields the Live sheet shows for any song card on screen.
export type LiveSong = {
  song_key: string;
  title_text: string | null;
  reason_rule: string | null;
  learning_eligible: boolean;
  resale_permitted: boolean;
};
// Home's lead arrival; discovery lists its Shazam Discovery country slugs.
export const lead = mart_arrivals_current
  .pick({
    day: true,
    song_key: true,
    title_text: true,
    artist_text: true,
    window_days: true,
    movement_list: true,
    entered_lists: true,
    reason_rule: true,
    learning_eligible: true,
    resale_permitted: true,
  })
  .extend({
    age_basis: z.string().nullable().default(null),
    discovery: z.array(z.string()),
    evidence: json(z.array(locator)).optional(),
  });
export type Lead = z.infer<typeof lead>;
export const arrivalSummary = z.object({
  songs: z.string(),
  window_days: z.number().int().nullable(),
  // Every source upstream of the counted songs, for their rights.
  source_keys: json(z.array(z.string())).default([]),
  // Where the counted songs were seen: each evidence relation and platform once.
  observed: json(
    z.array(
      z.object({
        relation: z.string(),
        row_key: z.record(z.string(), z.unknown()),
      }),
    ),
  ).default([]),
  lead: json(lead.nullable()),
});
export type ArrivalSummary = z.infer<typeof arrivalSummary>;
export const arrival = mart_arrivals_current
  .pick({
    movement_list: true,
    rank: true,
    song_key: true,
    title_text: true,
    artist_text: true,
    window_days: true,
    entered_lists: true,
    entered_charts: true,
    market_count: true,
    chart_spread_gain: true,
    age_basis: true,
    reason_rule: true,
    learning_eligible: true,
    resale_permitted: true,
    source_keys: true,
  })
  .extend({
    evidence: json(z.array(locator)).optional(),
    markets: json(z.array(z.string()).nullable()).transform(
      (value) => value ?? [],
    ),
    new_markets: json(z.array(z.string()).nullable()).default(null),
    // True once any arrival carries an artist size measure, so an empty established list is real.
    stage_measured: z.boolean().default(false),
  });
export type Arrival = z.infer<typeof arrival>;
export const earlySignal = mart_early_signals_current
  .pick({
    day: true,
    family: true,
    movement_list: true,
    rank: true,
    title_text: true,
    artist_text: true,
    window_days: true,
    component: true,
    value: true,
    age_basis: true,
    reason_rule: true,
    learning_eligible: true,
    resale_permitted: true,
    source_keys: true,
  })
  .extend({
    song_key: z.string(),
    playlist_count: z.string(),
    evidence: json(z.array(locator)).optional(),
  });
export type EarlySignal = z.infer<typeof earlySignal>;
export const readiness = mart_readiness.pick({
  family: true,
  day: true,
  history_days: true,
  first_rank_day: true,
  movement_list: true,
  stage_known_songs: true,
  list_songs: true,
});
export type Readiness = z.infer<typeof readiness>;
export const songDay = mart_song_day
  .pick({
    song_key: true,
    title_text: true,
    artist_text: true,
    learning_eligible: true,
    resale_permitted: true,
    source_keys: true,
    day: true,
    editorial_adds: true,
    algorithmic_adds: true,
    shazam_cities: true,
    stream_rate: true,
    playlist_followers: true,
  })
  .extend({ day });
export type SongDay = z.infer<typeof songDay>;
export const page = <T extends z.ZodType>(row: T) =>
  z.object({
    rows: z.array(row),
    build: martBuild,
    next_cursor: z.string().nullable(),
  });
export const identity = z.object({
  platform: z.string(),
  platform_track_id: z.string(),
  resolved: z.boolean(),
  song_key: z.string(),
});
export type Identity = z.infer<typeof identity>;
export const rightsRow = z.object({
  annotated: z.string(),
  learning: z.string(),
  resale: z.string(),
  // Every source in the rights register with its two permissions; unknown reads as not allowed.
  sources: z.array(
    z.object({
      source_key: z.string(),
      learning: z.boolean(),
      resale: z.boolean(),
    }),
  ),
});
export type Rights = z.infer<typeof rightsRow>;
// A song's lead artist: platform id, Wikidata item, first day on mdp and where they show up.
export const songArtist = z.object({
  song_key: z.string(),
  platform: z.string(),
  artist_id: z.string(),
  wikidata_qid: z.string().nullable(),
  mb_artist_name: z.string().nullable(),
  first_seen: z.string().nullable(),
  source_keys: json(z.array(z.string())),
});
export type SongArtist = z.infer<typeof songArtist>;
// A Shazam chart the song reached in the last four weeks; city is null on a country chart.
export const songPlace = z.object({
  country: z.string().nullable(),
  city: z.string().nullable(),
  first_day: z.string(),
});
export type SongPlace = z.infer<typeof songPlace>;
// Library sheets. Counts arrive as text; the newest entries arrive as JSON.
const listedSong = z.object({
  song_key: z.string(),
  title: z.string(),
  day: z.string().nullable(),
});
export const playlistItemRow = z.object({
  title: z.string().nullable(),
  platform: z.string(),
  owner_class: z.string().nullable(),
  owner_name: z.string().nullable(),
  followers: z.string().nullable(),
  tracks: z.string().nullable(),
  observed_at: z.string(),
  songs: json(z.array(listedSong)),
});
export type PlaylistItemRow = z.infer<typeof playlistItemRow>;
export const chartEntryRow = z.object({
  chart_type: z.string().nullable(),
  country: z.string().nullable(),
  city: z.string().nullable(),
  chart_date: z.string(),
  entries: z.string(),
  position: z.number().int(),
  title: z.string().nullable(),
  artist: z.string().nullable(),
  song_key: z.string().nullable(),
});
export type ChartEntryRow = z.infer<typeof chartEntryRow>;
export const artistSongRow = z.object({
  song_key: z.string(),
  title: z.string(),
  first_day: z.string().nullable(),
  songs: z.string(),
  first_seen: z.string().nullable(),
});
export type ArtistSongRow = z.infer<typeof artistSongRow>;
