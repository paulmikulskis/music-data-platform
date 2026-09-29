import "server-only";
import type { Person } from "@mdp/showcase-auth";
import { brandName, platformBrand } from "../lib/brands";
import { shortDate } from "../lib/music-facts";
import { placeName } from "../lib/places";
import { shortLabel } from "../lib/presentation";
import type { Source } from "../components/sources";
import { sources } from "./platform";
import { capturedProof } from "./proof-store";
import {
  arrivals,
  dayCharts,
  dayLists,
  dayPlays,
  earlySignals,
  movers,
  playlistTitles,
  recentMovers,
  signalArrivals,
  songGroup,
  songHistory,
  songPlaces,
} from "./reads";
import type { Locator } from "./models";

// A plain proof summary: what was read, when and from where, then the exact lists or charts.
// The operator console stays one quiet link away.
export type ProofView = {
  subject: string;
  headline: string;
  items: { name: string; note: string | null }[];
  more: number;
  lines: string[];
  sources?: Source[];
  engine: string | null;
};
const plural = (count: number, one: string, many: string) =>
  `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
const list = (names: string[]) =>
  names.length < 3
    ? names.join(" and ")
    : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
const platformName = (platform: string) => {
  const brand = platformBrand(platform);
  return brand ? brandName(brand) : platform;
};
const day = (value: unknown) =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)
    ? value.slice(0, 10)
    : null;
const text = (value: unknown) => (typeof value === "string" ? value : null);
// Shazam names a chart shazam:<type>:<country>[:<city>]; the reader sees the place.
function chartPlace(chart: string) {
  const [, , country = "", city = null] = chart.split(":");
  return placeName({ country, city });
}
const shown = <T>(items: T[]) => ({
  items: items.slice(0, 4),
  more: Math.max(0, items.length - 4),
});

// The rows one fact rests on, as captured when its card was ranked.
async function evidenceView(
  subject: string,
  evidence: Locator[],
): Promise<Omit<ProofView, "engine">> {
  const charts = new Map<
    string,
    { position: number | null; day: string | null }
  >();
  const lists = new Map<
    string,
    { platform: string; playlist_id: string; day: string | null }
  >();
  const plays: { platform: string; day: string | null }[] = [];
  for (const entry of evidence) {
    const key = entry.row_key;
    const chart = text(key.chart);
    const playlist = text(key.playlist_id);
    const platform = text(key.platform);
    if (chart && !charts.has(chart))
      charts.set(chart, {
        position: typeof key.position === "number" ? key.position : null,
        day: day(key.chart_date),
      });
    else if (playlist && platform && !lists.has(`${platform}:${playlist}`))
      lists.set(`${platform}:${playlist}`, {
        platform,
        playlist_id: playlist,
        day: day(key.observed_at),
      });
    else if (platform && entry.relation.endsWith("mart_track_daily_streams"))
      plays.push({ platform, day: day(key.day) });
  }
  const days = evidence
    .map(
      (entry) =>
        day(entry.row_key.chart_date) ??
        day(entry.row_key.observed_at) ??
        day(entry.row_key.day),
    )
    .filter((value) => value !== null)
    .sort();
  const latest = days.at(-1) ?? day(evidence[0]?.input_build.built_at);
  const behind = plural(evidence.length, "entry", "entries");
  if (charts.size >= lists.size && charts.size >= plays.length && charts.size) {
    const rows = [...charts].map(([chart, row]) => ({
      name: chartPlace(chart),
      note: row.position === null ? null : `#${row.position}`,
    }));
    return {
      subject,
      headline: `${plural(charts.size, "Shazam chart", "Shazam charts")}.`,
      ...shown(rows),
      lines: [
        `Read from Shazam${latest ? ` on ${shortDate(latest)}` : ""}.`,
        `${behind} behind this fact.`,
      ],
    };
  }
  if (lists.size >= plays.length && lists.size) {
    const wanted = [...lists.values()];
    const titles = await playlistTitles(wanted).catch(() => null);
    const rows = wanted.map((row) => ({
      name: shortLabel(
        titles?.value.rows.find(
          (title) =>
            title.platform === row.platform &&
            title.playlist_id === row.playlist_id,
        )?.title ?? `${platformName(row.platform)} playlist`,
        5,
      ),
      note: row.day ? shortDate(row.day) : null,
    }));
    const brands = [
      ...new Set(wanted.map((row) => platformName(row.platform))),
    ];
    return {
      subject,
      headline: `${plural(lists.size, "playlist", "playlists")}.`,
      ...shown(rows),
      lines: [
        `Read from ${list(brands)}${latest ? ` on ${shortDate(latest)}` : ""}.`,
        `${behind} behind this fact.`,
      ],
    };
  }
  if (plays.length) {
    const brands = [...new Set(plays.map((row) => platformName(row.platform)))];
    return {
      subject,
      headline: `${plural(plays.length, "play count", "play counts")}.`,
      ...shown(
        plays.map((row) => ({
          name: platformName(row.platform),
          note: row.day ? shortDate(row.day) : null,
        })),
      ),
      lines: [
        `Read from ${list(brands)}${latest ? ` on ${shortDate(latest)}` : ""}.`,
      ],
    };
  }
  return {
    subject,
    headline: "appearances not measured yet.",
    items: [],
    more: 0,
    lines: ["Open Console for the full record."],
  };
}

// A movement fact (mark indexes the card's evidence) or one lane on one song day.
export async function markProof(
  key: string,
  mark: string,
  query: { ranking?: string; history?: string; day?: string },
): Promise<Omit<ProofView, "engine"> | null> {
  if (!/^\d+$/.test(mark)) return null;
  if (query.history !== undefined) {
    const date = day(query.day);
    if (!date || !/^[0-2]$/.test(mark)) return null;
    const [history, group] = await Promise.all([
      songHistory(key).catch(() => null),
      songGroup(key).catch(() => null),
    ]);
    // A matched song's lanes add up its copies, so its proof reads every copy too.
    const keys = group?.value.next_cursor
      ? [key]
      : [key, ...(group?.value.rows ?? []).map((row) => row.song_key)];
    const title = history?.value.rows.at(-1)?.title_text ?? "Song";
    const when = shortDate(date);
    if (mark === "0") {
      const read = await dayLists(keys, date);
      const rows = read.value.rows.map((row) => ({
        name: shortLabel(
          row.title ?? `${platformName(row.platform)} playlist`,
          5,
        ),
        note: platformName(row.platform),
      }));
      return {
        subject: title,
        headline: rows.length
          ? `${plural(rows.length, "playlist", "playlists")}.`
          : "no new playlist appearances.",
        ...shown(rows),
        lines: [
          `Playlists with new appearances on ${when}. Chart lists appear in Places.`,
          "Read from the platforms' playlist pages.",
        ],
      };
    }
    if (mark === "1") {
      const read = await dayCharts(keys, date);
      const rows = read.value.rows.map((row) => ({
        name: placeName(row),
        note: `#${row.position}`,
      }));
      return {
        subject: title,
        headline: rows.length
          ? `${plural(rows.length, "city chart", "city charts")}.`
          : "no city charts.",
        ...shown(rows),
        lines: [
          `Shazam city charts the song was on, ${when}.`,
          "Read from Shazam.",
        ],
      };
    }
    const read = await dayPlays(keys, date);
    // Two copies on one platform read as copy 1 and copy 2.
    const seen = new Map<string, number>();
    const twice = new Set(
      read.value.rows
        .map((row) => row.platform)
        .filter((platform, i, all) => all.indexOf(platform) !== i),
    );
    const rows = read.value.rows.map((row) => {
      const n = (seen.get(row.platform) ?? 0) + 1;
      seen.set(row.platform, n);
      return {
        name: twice.has(row.platform)
          ? `${platformName(row.platform)} copy ${n}`
          : platformName(row.platform),
        note: `${BigInt(row.play_count).toLocaleString("en-US")} plays so far`,
      };
    });
    return {
      subject: title,
      headline: rows.length
        ? `${plural(rows.length, "play counter", "play counters")}.`
        : "no play counts.",
      ...shown(rows),
      lines: [
        `Play counters read on ${when}.`,
        "Plays a day is the change between two reads of a counter.",
      ],
    };
  }
  await movers(50).catch(() => null);
  const song = capturedProof(query.ranking ?? "", key);
  const entry = song?.evidence[Number(mark)];
  if (!song || !entry) return null;
  return evidenceView(
    song.title_text ?? "Song",
    song.evidence.filter((row) => row.component === entry.component),
  );
}

const families: Record<string, RegExp> = {
  shazam: /^sz_/,
  playlist: /playlist/,
  stream: /stream|play/,
};
// A number's proof. A song narrows it to that song's own entries; otherwise it names its sources.
export async function cycleProof(
  person: Person,
  relation: string,
  song: string | null,
  keys: string[],
): Promise<Omit<ProofView, "engine">> {
  if (song && /arrivals|movers|early_signals/.test(relation)) {
    const [signal, home, early, recent] = await Promise.all([
      signalArrivals().catch(() => null),
      arrivals().catch(() => null),
      earlySignals().catch(() => null),
      recentMovers().catch(() => null),
    ]);
    const lead = home?.value.rows[0]?.lead;
    const row =
      signal?.value.rows.find((item) => item.song_key === song) ??
      (lead?.song_key === song ? lead : undefined) ??
      early?.value.rows.find((item) => item.song_key === song) ??
      recent?.value.rows.find((item) => item.song_key === song);
    if (row?.evidence?.length)
      return evidenceView(row.title_text ?? "Song", row.evidence);
  }
  if (song && relation === "mart_shazam_chart_daily") {
    const [places, history] = await Promise.all([
      songPlaces(song),
      songHistory(song).catch(() => null),
    ]);
    const rows = places.value.rows.map((row) => ({
      name: placeName(row),
      note: shortDate(row.first_day),
    }));
    return {
      subject: history?.value.rows.at(-1)?.title_text ?? "Song",
      headline: rows.length
        ? `${plural(rows.length, "Shazam chart", "Shazam charts")}.`
        : "no Shazam charts yet.",
      ...shown(rows),
      lines: [
        "Charts the song reached in the last 28 days.",
        "Read from Shazam.",
      ],
    };
  }
  if (song && relation === "mart_song_day") {
    const history = await songHistory(song);
    const rows = history.value.rows;
    const latest = rows.at(-1);
    return {
      subject: latest?.title_text ?? "Song",
      headline: `${plural(rows.length, "day", "days")} read.`,
      items: [],
      more: 0,
      lines: [
        latest
          ? `Latest day read ${shortDate(latest.day)}.`
          : "No day read yet.",
        "From playlists, Shazam charts and play counters.",
      ],
    };
  }
  const read = await sources(person).catch(() => null);
  const all = read?.value.sources ?? [];
  const family = Object.entries(families).find(([name]) =>
    relation.includes(name),
  )?.[1];
  const picked = keys.length
    ? all.filter((source) => keys.includes(source.source_key))
    : family
      ? all.filter((source) => family.test(source.source_key))
      : [];
  const [only] = picked;
  if (!only)
    return {
      subject: "Sources",
      headline: "source not measured yet.",
      items: [],
      more: 0,
      lines: ["Open Console for the full record."],
    };
  return {
    subject: picked.length === 1 ? "Source" : "Sources",
    headline:
      picked.length === 1
        ? `${only.display_name.toLowerCase()}.`
        : `${plural(picked.length, "source", "sources")}.`,
    ...shown(
      picked.map((source) => ({ name: source.display_name, note: null })),
    ),
    lines: [],
    sources: picked.slice(0, 2),
  };
}
