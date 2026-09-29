import "server-only";
import { functionSentence, sourceFunctionState } from "../lib/function-copy";
import type { Person } from "@mdp/showcase-auth";
import { brandName, platformBrand } from "../lib/brands";
import { chartTitle, type LibraryItem } from "../lib/library";
import { countryName, shortDate } from "../lib/music-facts";
import { cityPlace, marketPlace, placeName } from "../lib/places";
import { shortLabel } from "../lib/presentation";
import { artistItem, chartItem, playlistItem } from "./reads";
import { sources } from "./platform";

const whole = (value: string) => BigInt(value).toLocaleString("en-US");
const platformName = (platform: string) => {
  const brand = platformBrand(platform);
  return brand ? brandName(brand) : platform;
};
// A playlist's plain facts: who runs it (only when the platform does), followers, newest songs.
export async function playlistFacts(key: string): Promise<LibraryItem | null> {
  const [platform = "", ...rest] = key.split(":");
  const id = rest.join(":");
  const read = await playlistItem(platform, id);
  const row = read.value.rows[0];
  if (!row) return null;
  const brand = platformName(row.platform);
  const owner = row.owner_name
    ? `by ${row.owner_name}`
    : row.owner_class === "user"
      ? "a listener's playlist"
      : null;
  return {
    kind: "playlist",
    title: row.title ?? "Playlist",
    subtitle: [brand, owner].filter(Boolean).join(" · "),
    facts: [
      ...(row.followers
        ? [{ value: whole(row.followers), label: "followers" }]
        : []),
      ...(row.tracks ? [{ value: whole(row.tracks), label: "songs" }] : []),
    ],
    songs_label: row.songs.length ? "New on the list" : null,
    songs: row.songs.map((song) => ({
      key: song.song_key,
      title: shortLabel(song.title, 6),
      note: song.day ? shortDate(song.day) : null,
    })),
    empty: row.songs.length
      ? null
      : "No songs have entered it since reading began.",
    places: [],
    read: `Read from ${brand} on ${shortDate(row.observed_at.slice(0, 10))}.`,
    link:
      row.platform === "spotify" && /^[A-Za-z0-9]{22}$/.test(id)
        ? {
            label: "Open on Spotify",
            href: `https://open.spotify.com/playlist/${id}`,
          }
        : null,
    engine: null,
    first_seen: null,
  };
}
// A chart's latest top five. Shazam keys read shazam:<type>:<country>[:<city>].
export async function chartFacts(key: string): Promise<LibraryItem | null> {
  const read = await chartItem(key);
  const rows = read.value.rows;
  const first = rows[0];
  if (!first) return null;
  const songs = rows.map((row) => ({
    key: row.song_key,
    title: shortLabel(row.title ?? "Untitled song", 5),
    note: [`#${row.position}`, row.artist ? shortLabel(row.artist, 3) : null]
      .filter(Boolean)
      .join(" · "),
  }));
  const facts = [{ value: whole(first.entries), label: "songs" }];
  if (key.startsWith("billboard:"))
    return {
      kind: "chart",
      title: "Billboard Hot 100",
      subtitle: null,
      facts,
      songs_label: "Top of the chart",
      songs,
      empty: null,
      places: [],
      read: `Read from Billboard, chart of ${shortDate(first.chart_date)}.`,
      link: {
        label: "Open on Billboard",
        href: "https://www.billboard.com/charts/hot-100/",
      },
      engine: null,
      first_seen: null,
    };
  const [, type = null, country = "", city = null] = key.split(":");
  const place = (city ? cityPlace(city) : null) ?? marketPlace(country);
  return {
    kind: "chart",
    title: chartTitle(placeName({ country, city }), type),
    subtitle: city ? countryName(country) : null,
    facts,
    songs_label: "Top of the chart",
    songs,
    empty: null,
    places: place ? [place] : [],
    read: `Read from Shazam on ${shortDate(first.chart_date)}.`,
    link: null,
    engine: null,
    first_seen: null,
  };
}
// The songs an artist leads in the Library, one per matched song, and the first day one had
// something real on its page.
export async function artistFacts(key: string): Promise<LibraryItem> {
  const [platform = "", ...rest] = key.split(":");
  const read = await artistItem(platform, rest.join(":"));
  const rows = read.value.rows;
  const first = rows[0];
  return {
    kind: "artist",
    title: "Songs",
    subtitle: null,
    facts: first ? [{ value: whole(first.songs), label: "songs" }] : [],
    songs_label: rows.length ? "Songs they lead" : null,
    songs: rows.map((row) => ({
      key: row.song_key,
      title: shortLabel(row.title, 6),
      note: null,
    })),
    empty: rows.length ? null : "No songs led by this artist yet.",
    places: [],
    // Lead artists come from tracked playlists, and for Apple copies from Shazam charts too.
    read:
      platform === "apple"
        ? "Read from Apple Music playlists and Shazam charts."
        : `Read from ${platformName(platform)} playlists.`,
    link: null,
    engine: null,
    first_seen: first?.first_seen ?? null,
  };
}
// A source's card: what it reads, how much it tracks and when it last read.
export async function sourceFacts(
  person: Person,
  key: string,
): Promise<LibraryItem | null> {
  const sourceKey = key.slice("source:".length);
  const read = await sources(person);
  const source = read.value.sources.find(
    (item) => item.source_key === sourceKey,
  );
  if (!source) return null;
  return {
    kind: "source",
    title: source.display_name,
    subtitle: functionSentence(source.source_key, sourceFunctionState(source)),
    facts: [],
    songs_label: null,
    songs: [],
    empty: null,
    places: [],
    read: "",
    source,
    link: null,
    engine: null,
    first_seen: null,
  };
}
