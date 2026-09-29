import { z } from "zod";
import readers from "./source-readers.generated.json" with { type: "json" };

export const sourceReaders = z
  .array(
    z.object({
      source_key: z.string(),
      unit: z.string().nullable(),
      platforms: z.array(z.string()),
      member_cadence: z.enum(["daily", "weekly"]).nullable(),
      id_prefix: z.string().nullable(),
    }),
  )
  .parse(readers);

// Plain wording for the sources viewers see. The showcase draws each brand's glyph; a source
// missing here keeps its registry provider name and the plain fallback below.
export const sourceBrand = z.enum([
  "spotify",
  "apple_music",
  "shazam",
  "billboard",
  "musicbrainz",
  "deezer",
  "wikipedia",
  "listenbrainz",
  "youtube",
  "bandcamp",
  "soundcloud",
]);
export const sourceFamily = z.enum([
  "playlists",
  "charts",
  "streams",
  "social",
  "identity",
  "attention",
  "listening",
  "radio",
  "stores",
  "derived",
  "other",
]);
export type SourceWording = {
  name: string;
  brand: z.infer<typeof sourceBrand> | null;
  family: z.infer<typeof sourceFamily>;
  plain: string;
};
const bandcamp = (name: string, plain: string): SourceWording => ({
  name,
  brand: "bandcamp",
  family: "stores",
  plain,
});
const listenbrainz = (name: string, plain: string): SourceWording => ({
  name,
  brand: "listenbrainz",
  family: "listening",
  plain,
});
const derived = (name: string, plain: string): SourceWording => ({
  name,
  brand: null,
  family: "derived",
  plain,
});
export const sourceWording: Record<string, SourceWording> = {
  sp_playlist: {
    name: "Spotify playlists and charts",
    brand: "spotify",
    family: "playlists",
    plain:
      "Spotify's staff-selected, new-music and Top 50 chart playlists, read every day.",
  },
  sp_playlist_weekly: {
    name: "Spotify artist and label playlists",
    brand: "spotify",
    family: "playlists",
    plain:
      "Playlists that artists and labels keep on Spotify, read once a week.",
  },
  am_playlist: {
    name: "Apple Music playlists and charts",
    brand: "apple_music",
    family: "playlists",
    plain:
      "Apple Music's staff-selected, new-music, decade and Top 100 chart playlists, read every day.",
  },
  am_playlist_weekly: {
    name: "Apple Music weekly playlists",
    brand: "apple_music",
    family: "playlists",
    plain: "Apple Music playlists read once a week.",
  },
  sc_playlist: {
    name: "SoundCloud playlists",
    brand: "soundcloud",
    family: "playlists",
    plain: "SoundCloud's own playlists.",
  },
  sc_playlist_weekly: {
    name: "SoundCloud weekly playlists",
    brand: "soundcloud",
    family: "playlists",
    plain: "SoundCloud playlists that change once a week.",
  },
  sc_curator_playlists: {
    name: "SoundCloud curators",
    brand: "soundcloud",
    family: "playlists",
    plain: "Playlists from SoundCloud curators.",
  },
  sc_curator_playlists_weekly: {
    name: "SoundCloud curators",
    brand: "soundcloud",
    family: "playlists",
    plain: "Playlists from SoundCloud curators, read weekly.",
  },
  sc_hubs: {
    name: "SoundCloud hubs",
    brand: "soundcloud",
    family: "playlists",
    plain: "SoundCloud's genre hubs.",
  },
  sz_chart: {
    name: "Shazam charts",
    brand: "shazam",
    family: "charts",
    plain: "What fans Shazamed, by country and by city.",
  },
  billboard_hot100: {
    name: "Billboard Hot 100",
    brand: "billboard",
    family: "charts",
    plain: "The weekly Billboard Hot 100.",
  },


  sp_track_artists: {
    name: "Spotify song credits",
    brand: "spotify",
    family: "identity",
    plain: "Who is credited on each Spotify song.",
  },

  apple_song_duration: {
    name: "Apple Music song lengths",
    brand: "apple_music",
    family: "identity",
    plain:
      "Song lengths from Apple's public lookup, used to match copies of a song.",
  },

  mb_spine: {
    name: "MusicBrainz",
    brand: "musicbrainz",
    family: "identity",
    plain: "The open music encyclopedia that matches copies of a song.",
  },
  mb_resolve: {
    name: "MusicBrainz matching",
    brand: "musicbrainz",
    family: "identity",
    plain: "Matches each song to its MusicBrainz recording.",
  },
  track_isrc_crosswalk: {
    name: "Deezer song codes",
    brand: "deezer",
    family: "identity",
    plain: "Song codes from Deezer that match copies across platforms.",
  },
  wiki_sitelinks: {
    name: "Wikipedia",
    brand: "wikipedia",
    family: "attention",
    plain: "How many Wikipedias have a page on each artist.",
  },
  wiki_pageviews: {
    name: "Wikipedia page views",
    brand: "wikipedia",
    family: "attention",
    plain: "Daily visits to each watched artist's Wikipedia page.",
  },
  lb_popularity: listenbrainz(
    "ListenBrainz listeners",
    "How many open listeners play each watched artist.",
  ),
  lb_sitewide: listenbrainz(
    "ListenBrainz charts",
    "The weekly charts of open listeners.",
  ),
  lb_fresh_releases: listenbrainz(
    "ListenBrainz new releases",
    "New releases open listeners are playing.",
  ),
  lb_similar_artists: listenbrainz(
    "ListenBrainz similar artists",
    "Artists that open listeners play together.",
  ),










  bc_discover: bandcamp(
    "Bandcamp best-sellers",
    "What sells on Bandcamp, by genre.",
  ),
  bc_discover_weekly: bandcamp(
    "Bandcamp best-sellers",
    "What sells on Bandcamp, by genre, read weekly.",
  ),
  bc_daily_list: bandcamp("Bandcamp Daily", "Bandcamp's own staff picks."),
  bc_daily_list_weekly: bandcamp(
    "Bandcamp Daily",
    "Bandcamp's own staff picks, read weekly.",
  ),
  bc_radio: bandcamp("Bandcamp Radio", "The songs played on Bandcamp Radio."),
  bc_radio_weekly: bandcamp(
    "Bandcamp Radio",
    "The songs played on Bandcamp Radio, read weekly.",
  ),
  bc_fan_playlist: bandcamp(
    "Bandcamp fan playlists",
    "Playlists Bandcamp fans make.",
  ),
  bc_fan_playlist_weekly: bandcamp(
    "Bandcamp fan playlists",
    "Playlists Bandcamp fans make, read weekly.",
  ),
  bc_tralbum: bandcamp("Bandcamp releases", "Release pages on Bandcamp."),
  kexp_plays: {
    name: "KEXP radio plays",
    brand: null,
    family: "radio",
    plain: "Every song KEXP plays on air.",
  },














  sp_playlist_embed: {
    name: "Spotify playlist embeds",
    brand: "spotify",
    family: "other",
    plain: "Spotify playlists read through their public embed.",
  },
  sp_playlist_page: {
    name: "Spotify playlist pages",
    brand: "spotify",
    family: "other",
    plain: "Spotify playlists read from their public page.",
  },
  mb_artist_catalog: {
    name: "MusicBrainz artist catalogs",
    brand: "musicbrainz",
    family: "derived",
    plain: "How many releases each credited artist has, and their first year.",
  },



  typesafe: {
    name: "TypeSafe",
    brand: null,
    family: "other",
    plain: "Typed answers from TypeSafe.",
  },
  jev_instruments: {
    name: "Instrument names",
    brand: null,
    family: "other",
    plain: "Common instrument names with their families.",
  },
  jev_instrument_family: derived(
    "Instrument families",
    "Sorts instruments into families.",
  ),
};
export const sourceFallback = "Collected by MDP.";

export const playsDescription =
  "Play counts printed on Spotify playlist pages for their first 30 songs, read every night.";
