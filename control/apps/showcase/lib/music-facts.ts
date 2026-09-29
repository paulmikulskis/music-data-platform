import type {
  Arrival,
  EarlySignal,
  Mover,
  Readiness,
  Locator,
} from "../server/models";
// "over N days" from the component's own window, else the song's window.
export function overDays(days: number | null | undefined) {
  return days
    ? `over ${days} ${days === 1 ? "day" : "days"}`
    : "over the current window";
}
// The widest window among rows, for copy that describes a whole list.
export function rowsWindow(rows: { window_days: number | null }[] | undefined) {
  return overDays(
    Math.max(0, ...(rows ?? []).map((row) => row.window_days ?? 0)) || null,
  );
}
function playlistVerb(evidence: Locator[] | undefined) {
  return evidence?.some(
    (proof) =>
      proof.component === "playlist_adds" && proof.row_key.event_type === "add",
  )
    ? "Added to"
    : "Seen on";
}
export function musicFacts(song: Mover) {
  const facts: { text: string; component: string }[] = [];
  const over = (component: string) =>
    overDays(
      song.score_parts.find((p) => p.component === component)?.window_days ??
        song.window_days,
    );
  if (song.playlist_count && BigInt(song.playlist_count) > 0n)
    facts.push({
      text: `${playlistVerb(song.evidence)} ${BigInt(song.playlist_count).toLocaleString("en-US")} ${song.playlist_count === "1" ? "playlist" : "playlists"} ${over("playlist_adds")}.`,
      component: "playlist_adds",
    });
  const stream = song.score_parts.find(
    (p) => p.component === "stream_rate_gain",
  )?.value;
  if (stream !== undefined && stream > 0)
    facts.push({
      text: `Plays up ${(stream * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}% ${over("stream_rate_gain")}.`,
      component: "stream_rate_gain",
    });
  const followers = song.score_parts.find(
    (p) => p.component === "follower_exposure_gain",
  )?.value;
  if (followers !== undefined && followers > 0)
    facts.push({
      text: `Added playlists total ${Math.round(followers).toLocaleString("en-US")} followers.`,
      component: "follower_exposure_gain",
    });
  return facts
    .filter((fact) =>
      song.evidence.some((proof) => proof.component === fact.component),
    )
    .slice(0, 3);
}
// A list can suggest new music while release age is still unknown.
// Unplaced songs have no list, so they have no tag.
export function listTag(list: string, basis: string | null) {
  if (list === "catalog_entries") return "catalog";
  if (list !== "new_entries" && list !== "established_entries") return null;
  return basis === "isrc_year" || basis === "apple_id_band"
    ? "new"
    : "new list";
}
const families: Record<string, string> = {
  playlists: "Playlists",
  shazam: "Shazam cities",
  streams: "Plays",
};
export function familyName(family: string) {
  return families[family] ?? family;
}
const whole = (value: bigint | number) =>
  typeof value === "bigint"
    ? value.toLocaleString("en-US")
    : Math.round(value).toLocaleString("en-US");
const plural = (count: bigint | number, one: string, many: string) =>
  `${whole(count)} ${count === 1 || count === 1n ? one : many}`;
// The family's plain number: playlists, followers, charts or a play-rate change.
export function earlyFact(row: EarlySignal) {
  const value = row.value ?? 0;
  if (row.component === "playlist_adds" && BigInt(row.playlist_count) > 0n)
    return `${playlistVerb(row.evidence)} ${plural(BigInt(row.playlist_count), "playlist", "playlists")}.`;
  if (row.component === "follower_exposure_gain" && value > 0)
    return `Added playlists total ${plural(Math.round(value), "follower", "followers")}.`;
  if (row.component === "shazam_spread_gain" && value > 0)
    return `On ${plural(Math.round(value), "more Shazam chart", "more Shazam charts")}.`;
  if (row.component === "stream_rate_gain" && value > 0)
    return `Plays up ${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}%.`;
  return "Open the song for its evidence.";
}
const positive = (value: string | null) => value !== null && BigInt(value) > 0n;
// Places leads with new markets, then tracked lists, then Shazam charts.
export function arrivalFact(row: Arrival) {
  if (positive(row.chart_spread_gain))
    return `Reached ${plural(BigInt(row.chart_spread_gain ?? 0), "new market", "new markets")}.`;
  if (positive(row.entered_lists))
    return `Entered ${plural(BigInt(row.entered_lists ?? 0), "tracked list", "tracked lists")}.`;
  if (positive(row.entered_charts))
    return `Entered ${plural(BigInt(row.entered_charts ?? 0), "Shazam chart", "Shazam charts")}.`;
  return "Open the song for its evidence.";
}
const countries: Record<string, string> = {
  "united-states": "US",
  "united-kingdom": "UK",
};
// Shazam names a chart by its page slug, such as united-states.
export function countryName(slug: string) {
  return (
    countries[slug] ??
    slug
      .split("-")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
  );
}
export function shortDate(day: string) {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
// Two-source ranking starts on the second family's first rank day.
export function rankingLine(rows: Readiness[] | undefined, noMovers: boolean) {
  const bound = rows?.[0]?.day;
  const start = (rows ?? [])
    .map((row) => row.first_rank_day)
    .filter((day): day is string => !!day)
    .sort()[1];
  if (bound && start && start > bound)
    return `two-source ranking from ${shortDate(start)}.`;
  return noMovers ? "no song moves on two sources yet." : null;
}
// A family warms up until its comparison window holds seven days.
export function warmingDays(rows: Readiness[] | undefined, family: string) {
  const row = rows?.find((item) => item.family === family);
  if (!row) return null;
  const days = Number(row.history_days);
  return days < 7 ? days : null;
}
export function warmingLabel(days: number) {
  return `building history · ${days} of 7 days`;
}
// The size line waits for a first reading; "0 of 120" reads as a failure, not as coming.
export function artistSizeMeasured(
  rows: Readiness[] | undefined,
  list: string,
) {
  const known = rows?.find(
    (item) => item.movement_list === list,
  )?.stage_known_songs;
  return !!known && BigInt(known) > 0n;
}
const answerFamilies: Record<string, string> = {
  mart_playlist_profile: "playlists",
  mart_playlist_events: "playlists",
  mart_shazam_chart_daily: "shazam",
  mart_track_daily_streams: "streams",
};
// Movement needs two families, so it warms up until the second one holds seven days.
export function answerWarming(
  relation: string | null,
  rows: Readiness[] | undefined,
) {
  if (!relation || !rows?.length) return null;
  if (relation === "mart_top_movers_current") {
    const second =
      rows
        .filter((row) => !row.movement_list)
        .map((row) => Number(row.history_days))
        .sort((a, b) => b - a)[1] ?? 0;
    return second < 7 ? second : null;
  }
  const family = answerFamilies[relation];
  return family ? warmingDays(rows, family) : null;
}
const sources: Record<string, string> = {
  playlist_adds:
    "Counted from the tracked editorial and new-music playlists where this song appeared.",
  follower_exposure_gain:
    "Followers of the playlists this song entered. Audiences can overlap; this is not a count of distinct listeners.",
  shazam_spread_gain: "Counted from Shazam country, city and Discovery charts.",
  stream_rate_gain: "Compared from the daily play counts of this song.",
};
// Plain words for where a card's number comes from.
export function earlySource(component: string | null) {
  return (
    sources[component ?? ""] ??
    "Collected from the tracked sources for this song."
  );
}
export const arrivalSource =
  "Counted from tracked chart lists, editorial lists and Shazam charts, by country.";
const ages: Record<string, string> = {
  isrc_year: "Age from the song's code, not a release date.",
  apple_id_band:
    "Its age comes from its Apple song id range, not a release date.",
};
// Age is a proxy. Say which one, and that it is not a release date.
export function ageSource(basis: string | null) {
  return (
    ages[basis ?? ""] ??
    "Its age is not measured yet. A new-music list placed it here."
  );
}

// Coverage counts the current served lists, including songs whose artist size is unknown.
export function artistSizeLine(rows: Readiness[] | undefined, list: string) {
  const row = rows?.find((item) => item.movement_list === list);
  if (!row || row.stage_known_songs === null || row.list_songs === null)
    return null;
  return `artist size known: ${BigInt(row.stage_known_songs).toLocaleString("en-US")} of ${BigInt(row.list_songs).toLocaleString("en-US")}`;
}

// A headline names a pair only when every positive, evidenced part uses that pair.
export function risingHeadline(songs: Mover[]) {
  const kinds: Record<string, string> = {
    playlist_adds: "playlists",
    follower_exposure_gain: "playlists",
    shazam_spread_gain: "Shazam",
    stream_rate_gain: "plays",
  };
  const pairs = songs.map((song) =>
    [
      ...new Set(
        song.score_parts
          .filter(
            (part) =>
              part.value > 0 &&
              song.evidence.some((proof) => proof.component === part.component),
          )
          .map((part) => kinds[part.component])
          .filter(Boolean),
      ),
    ].sort(
      (a, b) =>
        ["playlists", "Shazam", "plays"].indexOf(a) -
        ["playlists", "Shazam", "plays"].indexOf(b),
    ),
  );
  const pair = pairs[0];
  const common =
    pair?.length === 2 && pairs.every((item) => item.join() === pair.join());
  const count = songs.length;
  return {
    headline: common
      ? `${count} ${count === 1 ? "song" : "songs"} rising on ${pair.join(" and ")}.`
      : `${count} ${count === 1 ? "song" : "songs"} rising.`,
    detail: common ? null : "Each is climbing on two kinds of list at once.",
  };
}
