import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { platformSources, sourceWording } from "@mdp/contracts";
vi.mock("server-only", () => ({}));
import { artistLink, platformBrand, songLink } from "../lib/brands";
import { cityPlace, marketPlace, placeList } from "../lib/places";
import { landColumns, landRows } from "../lib/world-land";
import { observedKeys, sourceKeys, unionKeys } from "../lib/source-keys";
import {
  readToday,
  sourceLine,
  targetsLabel,
  TendingProvider,
  SourceBadges,
} from "../components/sources";
import { behindSteps, historyDays } from "../components/behind-card";
import { tendedCounters } from "../components/tending";
import { HowCard, HowSheet, trail } from "../components/how-we-know";
import { ArtistCard, monogram } from "../components/artist";
import { copiesLine, foundOn } from "../components/song";
import {
  PhotoCache,
  plainCredit,
  photoFile,
  photoInfo,
  safeLink,
  wikimediaHost,
} from "../server/artist-photo";
import { budget } from "../server/read-budget";
import { clockLabel } from "../lib/presentation";
import { sources } from "./browser/fixtures";
const parsed = platformSources.parse({
  queried_at: "2026-09-25T06:12:00.000Z",
  sources,
  next_step: "Open /functions.",
}).sources;
const now = Date.parse(`${new Date().toISOString().slice(0, 10)}T12:00:00Z`);
const words = (html: string) =>
  html
    .replace(/<[^>]*>/g, " ")
    .match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? [];
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
describe("sources and brands", () => {
  it("says read today only for a read today, dates any other read, and names weekly sources", () => {
    const find = (key: string) =>
      parsed.find((source) => source.source_key === key)!;
    expect(targetsLabel(find("sz_chart"))).toBe("58 charts");
    expect(sourceLine(find("sz_chart"), now, "America/New_York")).toContain(
      "58 charts tracked",
    );
    expect(sourceLine(find("sz_chart"), now, "America/New_York")).toMatch(
      /EDT|EST/,
    );
    expect(sourceLine(find("mb_spine"))).not.toContain("entries");
    expect(clockLabel(find("am_playlist").last_read!)).toBe("Time loading");
    expect(readToday(find("am_playlist"), now)).toBe(false);
    expect(targetsLabel(find("am_playlist"))).toBe("48 playlists");
    expect(targetsLabel(find("sp_playlist_weekly"))).toBe("20 playlists");
    expect(trail(find("sz_chart"))).toBe(
      "Shazam charts: 58 charts tracked at Time loading; read Time loading.",
    );
  });
  it("keeps one wording map with a brand for every glyph it names", () => {
    for (const wording of Object.values(sourceWording))
      expect(wording.plain).not.toMatch(
        /\b(rows?|keys?|marts?|pipelines?|cycles?|schemas?|ingestion)\b/i,
      );
    expect(platformBrand("apple")).toBe("apple_music");
    expect(platformBrand("test source")).toBeNull();
  });
  it("links only public ids to public pages", () => {
    expect(songLink("spotify", "FixtureTrack0000000001")?.href).toBe(
      "https://open.spotify.com/track/FixtureTrack0000000001",
    );
    expect(songLink("apple", "1440857781")?.label).toBe("Open on Apple Music");
    expect(songLink("spotify", "not an id")).toBeNull();
    expect(artistLink("apple", "159260351")?.href).toBe(
      "https://music.apple.com/artist/159260351",
    );
  });
  it("reads row source keys from text or arrays", () => {
    expect(sourceKeys('["sp_playlist","sz_chart"]')).toEqual([
      "sp_playlist",
      "sz_chart",
    ]);
    expect(sourceKeys("not json")).toEqual([]);
  });
  it("names only the sources a card's evidence points at", () => {
    const seen = (relation: string, platform?: string) => ({
      relation,
      row_key: platform ? { platform } : {},
    });
    expect(
      observedKeys([
        seen("mart_playlist_events", "spotify"),
        seen("mart_playlist_profile", "spotify"),
        seen("mart_shazam_chart_daily"),
        seen("mart_playlist_events", "apple_music"),
        seen("mart_track_daily_streams", "spotify"),
        seen("mart_playlist_events", "deezer"),
        seen("mart_song_day"),
      ]),
    ).toEqual(["sp_playlist", "sz_chart", "am_playlist"]);
    expect(observedKeys(undefined)).toEqual([]);
    expect(
      unionKeys([
        { evidence: [seen("mart_shazam_chart_daily")] },
        { evidence: [seen("mart_playlist_events", "apple_music")] },
        {},
      ]),
    ).toEqual(["sz_chart", "am_playlist"]);
  });
  it("draws one badge per brand", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources: parsed, songs: "7649" }}>
        <SourceBadges
          keys={["sp_playlist", "sp_track_plays", "sz_chart", "unknown"]}
        />
      </TendingProvider>,
    );
    expect(html.match(/class="source-badge"/g)).toHaveLength(2);
  });
});
describe("places", () => {
  it("places seeded Shazam cities and markets", () => {
    expect(cityPlace("montr%C3%A9al")?.name).toBe("Montréal");
    expect(cityPlace("s%C3%A3o-paulo")?.lat).toBeLessThan(0);
    expect(marketPlace("united-kingdom")?.name).toBe("UK");
    expect(marketPlace("BR")?.name).toBe("Brazil");
    expect(
      placeList(["JP", "XX"], marketPlace).map((place) => place.name),
    ).toEqual(["Japan"]);
  });
  it("keeps the generated land mask whole", () => {
    expect(landRows).toHaveLength(48);
    for (const row of landRows) expect(row).toHaveLength(landColumns / 4);
  });
});
describe("behind the card and the tending counters", () => {
  it("counts the path from sources to the card, or says it is not measured", () => {
    const picked = parsed.filter((source) =>
      ["sp_playlist", "sz_chart"].includes(source.source_key),
    );
    const steps = behindSteps(picked, "7649", now).map((step) => step.text);
    expect(steps).toEqual([
      "Shazam charts · 58 charts tracked · read today",
      "Spotify playlists and charts · 36 playlists tracked · read today",
      "7,649 songs tracked",
      "13 days of history",
      "this card",
    ]);
    const stale = behindSteps(
      parsed.filter((source) => source.source_key === "am_playlist"),
      "7649",
      now,
    ).map((step) => step.text);
    expect(stale[0]).toMatch(
      /^Apple Music playlists and charts · last read \d+ \w+$/,
    );
    expect(behindSteps([], null, now).map((step) => step.text)).toEqual([
      "songs tracked · not measured yet",
      "days of history · not measured yet",
      "this card",
    ]);
    expect(historyDays([], now)).toBeNull();
  });
  it("separates tracked charts from entries actually read", () => {
    const chart = parsed.find((source) => source.source_key === "sz_chart");
    if (!chart) throw new Error("Fixture needs a chart source.");
    const counter = tendedCounters(
      [{ ...chart, targets: 58, entries_today: "50" }],
      null,
      now,
    ).find((item) => item.key === "charts");
    expect(counter).toMatchObject({ value: 58, label: "charts tracked" });
    expect(counter?.line).toBe(
      "50 chart appearances read today. Open Sources for each read.",
    );
  });
  it("counts what we tend from the sources' own reads", () => {
    // Keep every freshness check at the fixture day's noon, after yesterday's
    // daily read leaves its freshness grace window.
    vi.spyOn(Date, "now").mockReturnValue(now);
    const counters = Object.fromEntries(
      tendedCounters(parsed, "7649", now).map((counter) => [
        counter.key,
        counter.value,
      ]),
    );
    expect(counters).toEqual({
      sources: 3,
      playlists: 104,
      charts: 58,
      songs: 7649,
      entries: 7440,
      days: 13,
    });
    expect(
      tendedCounters([], null, now).every((counter) => counter.value === null),
    ).toBe(true);
  });
});
describe("how we know", () => {
  const provenance = {
    queried_at: "2026-09-25T06:12:00.000Z",
    scope: "global",
    query:
      "Entries collected on the regular schedule since Monday at midnight UTC.",
    sql: "SELECT 1",
    plain: "Entries mdp collected on schedule this week.",
    sources: ["sp_playlist", "sz_chart"],
    links: [
      {
        label: "Open on Spotify",
        href: "https://open.spotify.com/track/FixtureTrack0000000001",
      },
    ],
  };
  it("keeps the hover card inside its budget", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources: parsed, songs: null }}>
        <HowCard provenance={provenance} />
      </TendingProvider>,
    );
    expect(html).toContain("Entries mdp collected on schedule this week.");
    expect(html).toContain("sparkline");
    expect(words(html).length).toBeLessThanOrEqual(30);
  });
  it("tells the trail in plain words and offers the ways out", () => {
    const html = renderToStaticMarkup(
      <TendingProvider value={{ sources: parsed, songs: null }}>
        <HowSheet label="collected this week" provenance={provenance} />
      </TendingProvider>,
    );
    expect(html).toContain(
      "Spotify playlists and charts: 36 playlists tracked at Time loading; read Time loading.",
    );
    expect(html).toContain(
      "Shazam charts: 58 charts tracked at Time loading; read Time loading.",
    );
    expect(html).toContain("See the appearances →");
    expect(html).toContain('target="_blank"');
    expect(words(html).length).toBeLessThanOrEqual(80);
  });
  it("says a number without sources is not measured yet", () => {
    expect(renderToStaticMarkup(<HowCard />)).toContain("not measured yet");
  });
});
describe("artists and song copies", () => {
  it("falls back to a monogram and says when the first sighting is unknown", () => {
    expect(monogram("Test recording")).toBe("TR");
    const html = renderToStaticMarkup(<ArtistCard name="Test recording" />);
    expect(html).toContain('class="monogram"');
    expect(html).toContain("first seen · not measured yet");
  });
  it("shows no photo until its credit and license have loaded", () => {
    const artist = {
      song_key: "s",
      platform: "spotify",
      artist_id: "FixtureArtist000000001",
      wikidata_qid: "Q42",
      mb_artist_name: "Test recording",
      first_seen: "2026-08-30",
      source_keys: ["sp_playlist"],
    };
    const html = renderToStaticMarkup(
      <ArtistCard name="Test recording" artist={artist} />,
    );
    expect(html).not.toContain("/artist-photo/");
    expect(html).toContain('class="monogram"');
  });
  it("counts copies and their platforms", () => {
    const copies = [
      {
        platform: "spotify",
        platform_track_id: "FixtureTrack0000000001",
        resolved: true,
        song_key: "s",
      },
      {
        platform: "apple",
        platform_track_id: "1440857781",
        resolved: true,
        song_key: "s",
      },
      {
        platform: "spotify",
        platform_track_id: "7qiZfU4dY1lWllzX7mPBI3",
        resolved: true,
        song_key: "s",
      },
    ];
    expect(foundOn(copies).brands).toEqual(["spotify", "apple_music"]);
    expect(foundOn(copies).links.map((link) => link.label)).toEqual([
      "Open on Spotify",
      "Open on Apple Music",
    ]);
    expect(copiesLine(copies, true)).toBe("Same song on 2 platforms");
    expect(copiesLine(copies, false)).toBe("Not matched yet");
  });
});
describe("Wikimedia photos", () => {
  it("reads only Wikimedia hosts and plain credit lines", () => {
    expect(wikimediaHost(new URL("https://upload.wikimedia.org/a.jpg"))).toBe(
      true,
    );
    expect(wikimediaHost(new URL("https://example.com/a.jpg"))).toBe(false);
    expect(plainCredit('<a href="x">Jane &amp; Co</a>')).toBe("Jane & Co");
    expect(plainCredit("")).toBe("");
    expect(
      safeLink("https://commons.wikimedia.org/wiki/File:A.jpg", true),
    ).toBe("https://commons.wikimedia.org/wiki/File:A.jpg");
    expect(safeLink("https://example.com/wiki/File:A.jpg", true)).toBeNull();
    expect(safeLink("javascript:alert(1)")).toBeNull();
  });
  it("gives no photo when Commons names no license", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          query: {
            pages: {
              "1": {
                imageinfo: [
                  {
                    thumburl: "https://upload.wikimedia.org/thumb/test.jpg",
                    descriptionurl:
                      "https://commons.wikimedia.org/wiki/File:Test_photo.jpg",
                    extmetadata: { Artist: { value: "Test photographer" } },
                  },
                ],
              },
            },
          },
        }),
      ),
    );
    expect(await photoInfo("Test photo.jpg")).toBeNull();
  });
  it("follows the Wikidata image to its Commons thumbnail and credit", async () => {
    const fetcher = vi.fn(async (url: URL) => {
      if (url.hostname === "www.wikidata.org")
        return Response.json({
          claims: {
            P18: [{ mainsnak: { datavalue: { value: "Test photo.jpg" } } }],
          },
        });
      return Response.json({
        query: {
          pages: {
            "1": {
              imageinfo: [
                {
                  thumburl: "https://upload.wikimedia.org/thumb/test.jpg",
                  descriptionurl:
                    "https://commons.wikimedia.org/wiki/File:Test_photo.jpg",
                  extmetadata: {
                    Artist: { value: "<b>Test photographer</b>" },
                    LicenseShortName: { value: "CC BY-SA 4.0" },
                    LicenseUrl: {
                      value: "https://creativecommons.org/licenses/by-sa/4.0/",
                    },
                  },
                },
              ],
            },
          },
        },
      });
    });
    vi.stubGlobal("fetch", fetcher);
    expect(await photoFile("Q42")).toBe("Test photo.jpg");
    const info = await photoInfo("Test photo.jpg");
    expect(info?.credit).toEqual({
      author: "Test photographer",
      license: "CC BY-SA 4.0",
      license_url: "https://creativecommons.org/licenses/by-sa/4.0/",
      page: "https://commons.wikimedia.org/wiki/File:Test_photo.jpg",
    });
    expect(info?.thumb.hostname).toBe("upload.wikimedia.org");
  });
});
describe("photo cache", () => {
  it("stays within its count and bytes and never evicts saved room payloads", async () => {
    const rooms = budget.cache<string>();
    budget.setRunner("idle");
    await rooms.read("global:movers:3", "heavy", async () => "saved room");
    const cache = new PhotoCache(8, 4000);
    const photo = (n: number) => ({
      type: "image/jpeg",
      bytes: Buffer.alloc(1000, n),
      credit: {
        author: "A",
        license: "CC0",
        license_url: null,
        page: "https://commons.wikimedia.org/wiki/File:A.jpg",
      },
    });
    for (let n = 1; n <= 600; n++)
      await cache.read(`Q${n}`, async () => (n % 3 ? null : photo(n)));
    expect(cache.size).toBeLessThanOrEqual(8);
    expect(cache.totalBytes).toBeLessThanOrEqual(4000);
    budget.setRunner("busy");
    expect(
      (await rooms.read("global:movers:3", "heavy", async () => "fresh")).value,
    ).toBe("saved room");
    budget.setRunner("idle");
  });
  it("remembers a missing photo only briefly", async () => {
    const cache = new PhotoCache(8, 4000, 0);
    const work = vi.fn(async () => null);
    await cache.read("Q1", work);
    await cache.read("Q1", work);
    expect(work).toHaveBeenCalledTimes(2);
  });
});
