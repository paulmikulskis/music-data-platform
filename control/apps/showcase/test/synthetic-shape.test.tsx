import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  syntheticSources,
  syntheticNightResponse,
} from "./synthetic-fixture";
import {
  isMusicSource,
  sourceFamilies,
  liveProviderFamilies,
} from "../lib/source-families";
import { momentClusters, nightMoments, nightTickSize } from "../lib/night";
import { SourceStrip } from "../components/home/source-strip";
import { SourceList } from "../components/source-list";
import { TendingProvider } from "../components/sources";
import { reviewedSources } from "../lib/reviewed-sources";
const sources = syntheticSources.sources;
const now = Date.parse(syntheticSources.queried_at);
const families = sourceFamilies(sources, now);

describe("synthetic source readers", () => {
  it("puts all declared keys on exactly one side of the declaration boundary", () => {
    expect(sources).toHaveLength(33);
    const readers = sources.filter(isMusicSource);
    const machinery = sources.filter((source) => !isMusicSource(source));
    expect(
      new Set([...readers, ...machinery].map((source) => source.source_key))
        .size,
    ).toBe(33);
    expect(readers).toHaveLength(29);
    expect(reviewedSources.map((source) => source.label)).not.toEqual(
      expect.arrayContaining(["cycle_close", "targets_export", "TypeSafe"]),
    );
    for (const key of [
      "cycle_close",
      "lifecycle_daily_probe",
      "targets_export",
    ])
      expect(machinery.map((source) => source.source_key)).toContain(key);
    expect(readers.map((source) => source.source_key)).toEqual(
      expect.arrayContaining([
        "sp_playlist",
        "sp_playlist_weekly",
        "sz_chart",
        "mb_artist_catalog",
      ]),
    );
  });
  it("shows every live provider once with Home's music readers first", () => {
    expect(
      liveProviderFamilies(families).map((family) => family.primary.brand),
    ).toEqual([
      "spotify",
      "apple_music",
      "shazam",
      "billboard",
      "deezer",
      "musicbrainz",
    ]);
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources, songs: null }}>
        <SourceStrip />
      </TendingProvider>,
    );
    for (const provider of [
      "Spotify",
      "Apple Music",
      "Shazam",
      "Billboard",
      "MusicBrainz",
    ])
      expect(html).toContain(`aria-label="Open ${provider}"`);
    expect(html).toContain('href="https://open.spotify.com"');
    expect(html).toContain('href="https://www.shazam.com"');
    expect(html).toContain("trace=source.sp_playlist");
    expect(html.match(/Bandcamp best-sellers/g)).toHaveLength(1);
  });
  it("groups every weekly twin and keeps different states visible", () => {
    for (const source of sources.filter(
      (source) =>
        source.source_key.endsWith("_weekly") && isMusicSource(source),
    )) {
      const family = families.find(
        (family) => family.key === source.source_key.replace(/_weekly$/, ""),
      );
      expect(family?.readers.map((reader) => reader.source_key)).toContain(
        source.source_key,
      );
    }
    expect(
      families.find((family) => family.key === "bc_daily_list")?.label,
    ).toBe("Built · switched off");
    const apple = families.find((family) => family.key === "am_playlist");
    expect(apple?.label).toContain("Up to date");
    expect(apple?.label).toContain("Enabled · no targets");
    expect(apple?.readers).toHaveLength(2);
  });
  it("defaults to enabled readers without any hidden live cards", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources, songs: null }}>
        <SourceList />
      </TendingProvider>,
    );
    const keys = Array.from(
      html.matchAll(/data-source-family="([^"]+)"/g),
      (match) => match[1],
    );
    expect(keys.slice(0, 5)).toEqual([
      "sp_playlist",
      "am_playlist",
      "sz_chart",
      "billboard_hot100",
      "apple_song_duration",
    ]);
    expect(keys).toContain("mb_artist_catalog");
    expect(keys).not.toContain("bc_daily_list");
    expect(keys).not.toContain("cycle_close");
  });
});

describe("synthetic night density", () => {
  const data = syntheticNightResponse.value;
  const moments = nightMoments(data);
  it("retains all attempts while fitting small and wide tracks", () => {
    expect(data.runs).toHaveLength(131);
    expect(data.closes).toHaveLength(21);
    for (const width of [1, 120, 260, 350, 600, 1360]) {
      const groups = momentClusters(moments, data.window, width);
      expect(groups.flatMap((group) => group.moments)).toEqual(moments);
      expect(groups.length).toBeLessThanOrEqual(
        Math.max(1, Math.floor(width / nightTickSize)),
      );
      for (const [index, group] of groups.entries()) {
        expect(group.x).toBeGreaterThanOrEqual(0);
        expect(group.x).toBeLessThanOrEqual(width);
        if (index)
          expect(group.x - groups[index - 1].x).toBeGreaterThanOrEqual(
            nightTickSize,
          );
      }
    }
  });
});
