import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { martBuild } from "@mdp/data-sdk";
import { mover } from "../server/models";
import { build, movers, sources } from "./browser/fixtures";
import { platformSource } from "@mdp/contracts";
import { sourceLine } from "../components/sources";
const mocks = vi.hoisted(() => ({
  room: vi.fn(),
  captured: vi.fn(),
  titles: vi.fn(),
  sources: vi.fn(),
  playlist: vi.fn(),
  chart: vi.fn(),
  dayLists: vi.fn(),
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/room", () => ({
  room: mocks.room,
  unavailable: () => null,
}));
vi.mock("../server/proof-store", () => ({
  capturedProof: mocks.captured,
  capturedHistory: () => null,
  rememberProofs: () => {},
  rememberHistory: () => {},
}));
vi.mock("../server/platform", () => ({ sources: mocks.sources }));
vi.mock("../server/reads", async (original) => ({
  ...(await original<typeof import("../server/reads")>()),
  movers: async () => null,
  songHistory: async () => {
    throw new Error("offline");
  },
  playlistTitles: mocks.titles,
  playlistItem: mocks.playlist,
  chartItem: mocks.chart,
  dayLists: mocks.dayLists,
  songGroup: async () => null,
}));
vi.mock("../components/shell", () => ({
  Shell: ({ children, retry }: { children: ReactNode; retry?: string }) => (
    <main data-retry={retry}>{children}</main>
  ),
}));
import { cycleProofLink, viewerPath, markProofLink } from "../lib/proof-link";
import { markProof, cycleProof } from "../server/proof-summary";
import { playlistFacts, chartFacts, sourceFacts } from "../server/library";
import MarkPage from "../app/s/proof/[key]/[mark]/page";
import CyclePage from "../app/s/proof/cycle/[cycle]/page";
import { signProof } from "../server/proof-token";
import { createConsoleProxy } from "../server/console-proxy";
import { ReadBudget } from "../server/read-budget";
const origin = "https://showcase.invalid";
const person = {
  handle: "fixture",
  display_name: "Test viewer",
  email: "fixture@example.invalid",
  admin_key: "fixture",
  api_key_id: "00000000-0000-4000-8000-000000000001",
};
const locator = (row_key: Record<string, unknown>, relation: string) => ({
  component: "shazam_spread_gain",
  relation,
  row_key,
  window: {},
  input_build: {
    relation: `marts.${relation}`,
    scope: "global",
    cycle_id: "00000000-0000-4000-8000-000000000041",
    close_no: "41",
    built_at: "2026-09-26T17:27:32Z",
  },
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("MDP_SHOWCASE_ORIGIN", origin);
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "fixture-secret");
  mocks.room.mockResolvedValue({ handle: "fixture", person, csrf_token: "x" });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("returns only to viewer screens, never the operator console or another host", () => {
  for (const path of [
    "/",
    "/songs?view=places",
    "/songs?view=rising&days=28",
    "/s/song/apple%3A1?ranking=r",
    "/s/proof/cycle/x?relation=mart_song_day",
  ])
    expect(viewerPath(path)).toBe(path);
  for (const path of [
    "//evil.invalid/today",
    "https://evil.invalid/today",
    "/runs/fixture",
    "/explorer?q=marts.mart_playlist_profile",
    "/functions/sz_chart",
    "/today\\evil",
    "/todayx",
    "/today/../explorer",
    "/s/song/%2e%2e/%2e%2e/runs/x",
    "/s/song/%2E%2E/%2E%2E/explorer",
    "/s/proof/./../functions/x",
    // A proof's own engine hop is the operator console too.
    "/s/proof/apple%3A1/0/engine?ranking=r",
    "/s/proof/cycle/x/engine",
    "/s/proof/cycle/x/engine/",
    null,
  ])
    expect(viewerPath(path)).toBeNull();
});
it("carries the screen a proof opens from, and names the song and sources", () => {
  vi.stubGlobal("window", { location: { pathname: "/songs?view=places", search: "" } });
  const stamp = martBuild.parse(build("mart_arrivals_current"));
  const link = new URL(
    cycleProofLink(stamp, "/songs?view=rising", {
      song: "apple:1",
      sources: ["sz_chart"],
    }),
    origin,
  );
  expect(link.pathname).toBe(`/s/proof/cycle/${stamp.cycle_id}`);
  expect(link.searchParams.get("song")).toBe("apple:1");
  expect(link.searchParams.get("sources")).toBe("sz_chart");
  expect(link.searchParams.get("from")).toBe("/songs?view=places");
  expect(markProofLink("apple:1", 2, { ranking: "r" })).toBe(
    "/s/proof/apple%3A1/2?ranking=r&from=%2Fsongs%3Fview%3Dplaces",
  );
});
it("names the exact charts behind a movement fact, not one run for everything", async () => {
  const row = mover.parse({
    ...movers[0],
    evidence: JSON.stringify([
      locator(
        {
          chart: "shazam:top-200:brazil",
          position: 39,
          chart_date: "2026-09-25",
        },
        "mart_shazam_chart_daily",
      ),
      locator(
        {
          chart: "shazam:top-50:japan:tokyo",
          position: 4,
          chart_date: "2026-09-26",
        },
        "mart_shazam_chart_daily",
      ),
    ]),
  });
  mocks.captured.mockReturnValue(row);
  const view = await markProof(row.song_key, "0", {
    ranking: row.ranking_build,
  });
  expect(view).toMatchObject({
    headline: "2 Shazam charts.",
    items: [
      { name: "Brazil", note: "#39" },
      { name: "Tokyo", note: "#4" },
    ],
    lines: ["Read from Shazam on 26 Sept.", "2 entries behind this fact."],
  });
});
it("names the playlists behind a playlist fact by their titles", async () => {
  const row = mover.parse({
    ...movers[0],
    evidence: JSON.stringify([
      {
        ...locator(
          {
            platform: "spotify",
            playlist_id: "37i9dQZF1DX4JAvHpjipBk",
            observed_at: "2026-09-26T02:55:59",
          },
          "mart_playlist_events",
        ),
        component: "playlist_adds",
      },
    ]),
  });
  mocks.captured.mockReturnValue(row);
  mocks.titles.mockResolvedValue({
    value: {
      rows: [
        {
          platform: "spotify",
          playlist_id: "37i9dQZF1DX4JAvHpjipBk",
          title: "New Music Friday",
        },
      ],
    },
  });
  const view = await markProof(row.song_key, "0", {
    ranking: row.ranking_build,
  });
  expect(view?.headline).toBe("1 playlist.");
  expect(view?.items).toEqual([{ name: "New Music Friday", note: "26 Sept" }]);
  expect(view?.lines[0]).toBe("Read from Spotify on 26 Sept.");
  // A list without a stored title is named by its platform, never a bare "Playlist".
  mocks.titles.mockResolvedValue({ value: { rows: [] } });
  const untitled = await markProof(row.song_key, "0", {
    ranking: row.ranking_build,
  });
  expect(untitled?.items[0]?.name).toBe("Spotify playlist");
});
it("renders the summary with Back to the origin screen and a quiet operator console link", async () => {
  const row = mover.parse(movers[0]);
  mocks.captured.mockReturnValue(row);
  const html = renderToStaticMarkup(
    await MarkPage({
      params: Promise.resolve({ key: row.song_key, mark: "0" }),
      searchParams: Promise.resolve({
        ranking: row.ranking_build,
        from: "/",
      }),
    }),
  );
  expect(html).toContain('href="/"');
  expect(html).toContain("Open in Console ↗");
  const engine = new URL(
    html.match(/href="([^"]+\/engine\?[^"]+)"/)![1]!.replaceAll("&amp;", "&"),
    origin,
  );
  expect(engine.pathname).toBe(
    `/s/proof/${encodeURIComponent(row.song_key)}/0/engine`,
  );
  expect(engine.searchParams.get("back")).toBe(
    `/s/proof/${encodeURIComponent(row.song_key)}/0?ranking=${encodeURIComponent(row.ranking_build)}&from=%2F`,
  );
  expect(html).not.toContain("/runs/");
  expect(html).not.toContain("/explorer");
});
it("gives a missing fact one way back", async () => {
  mocks.captured.mockReturnValue(undefined);
  const html = renderToStaticMarkup(
    await MarkPage({
      params: Promise.resolve({ key: "apple:missing", mark: "0" }),
      searchParams: Promise.resolve({ ranking: "missing", from: "/songs?view=places" }),
    }),
  );
  expect(html).toContain("Proof is unavailable.");
  expect(html.match(/<a /g)).toHaveLength(1);
  expect(html).toContain('href="/songs?view=places"');
});
it("describes a number's sources when no song applies", async () => {
  mocks.sources.mockResolvedValue({
    value: {
      sources: [
        {
          ...sources.find((source) => source.source_key === "sz_chart"),
          source_key: "sz_chart",
          display_name: "Shazam charts",
          targets: 139,
          last_read: "2026-09-20T03:12:00Z",
        },
      ],
    },
  });
  const view = await cycleProof(person, "mart_arrivals_current", null, [
    "sz_chart",
  ]);
  expect(view).toMatchObject({
    headline: "shazam charts.",
    lines: [],
    sources: [
      { source_key: "sz_chart", tracked: { count: 58, unit: "charts" } },
    ],
  });
  const stamp = martBuild.parse(build("mart_arrivals_current"));
  const html = renderToStaticMarkup(
    await CyclePage({
      params: Promise.resolve({ cycle: stamp.cycle_id! }),
      searchParams: Promise.resolve({
        relation: "marts.mart_arrivals_current",
        sources: "sz_chart",
        from: "//evil.invalid",
      }),
    }),
  );
  expect(html).toContain('href="/"');
  expect(html).toContain("/engine?");
});
it("sends the operator console's Back bar to the screen the viewer came from", async () => {
  const proxy = createConsoleProxy(
    new ReadBudget(),
    async () =>
      new Response("<html><head></head><body>Run</body></html>", {
        headers: { "content-type": "text/html" },
      }),
  );
  vi.stubEnv("MDP_CONTROL_API_URL", "http://control.internal");
  const token = signProof({
    level: "row",
    song: "apple:1",
    handle: "quartz",
    back: "/s/proof/apple%3A1/0?ranking=r&from=%2Fsongs%3Fview%3Dplaces",
  });
  const response = await proxy(
    new Request(
      `${origin}/runs/fixture?showcase_proof=${encodeURIComponent(token)}`,
    ),
    {
      id_hash: "session",
      handle: "quartz",
      csrf_token: "csrf",
      person: { ...person, handle: "quartz" },
    },
  );
  const html = await response.text();
  expect(html).toContain(
    JSON.stringify(
      `${origin}/s/proof/apple%3A1/0?ranking=r&amp;from=%2Fsongs%3Fview%3Dplaces`,
    ).slice(1, -1),
  );
  const refused = signProof({
    level: "row",
    song: "apple:1",
    handle: "quartz",
    back: "https://evil.invalid/",
  });
  const fallback = await (
    await proxy(
      new Request(
        `${origin}/runs/other?showcase_proof=${encodeURIComponent(refused)}`,
      ),
      {
        id_hash: "session",
        handle: "quartz",
        csrf_token: "csrf",
        person: { ...person, handle: "quartz" },
      },
    )
  ).text();
  expect(fallback).toContain(`${origin}/s/song/apple%3A1`);
  expect(fallback).not.toContain("evil.invalid");
});
it("builds viewer views for playlists and charts from their own facts", async () => {
  mocks.playlist.mockResolvedValue({
    value: {
      rows: [
        {
          title: "New Music Friday",
          platform: "spotify",
          owner_class: "editorial",
          owner_name: "Spotify",
          followers: "4636710",
          tracks: "100",
          observed_at: "2026-09-26 02:55:59",
          songs: [{ song_key: "s1", title: "Fixture Song D", day: "2026-09-26" }],
        },
      ],
    },
  });
  expect(await playlistFacts("spotify:37i9dQZF1DX4JAvHpjipBk")).toMatchObject({
    title: "New Music Friday",
    subtitle: "Spotify · by Spotify",
    facts: [
      { value: "4,636,710", label: "followers" },
      { value: "100", label: "songs" },
    ],
    songs: [{ key: "s1", title: "Fixture Song D", note: "26 Sept" }],
    read: "Read from Spotify on 26 Sept.",
    link: { href: "https://open.spotify.com/playlist/37i9dQZF1DX4JAvHpjipBk" },
    engine: null,
  });
  mocks.chart.mockResolvedValue({
    value: {
      rows: [
        {
          chart_type: "top-50",
          country: "JP",
          city: "tokyo",
          chart_date: "2026-09-26",
          entries: "50",
          position: 1,
          title: "Song",
          artist: "Artist",
          song_key: null,
        },
      ],
    },
  });
  expect(await chartFacts("shazam:top-50:japan:tokyo")).toMatchObject({
    title: "Tokyo · Shazam top 50",
    subtitle: "Japan",
    facts: [{ value: "50", label: "songs" }],
    songs: [{ key: null, title: "Song", note: "#1 · Artist" }],
    places: [{ name: "Tokyo" }],
    engine: null,
  });
});

it("reads a colon key once decoded, as Next hands the segment over encoded", async () => {
  const row = mover.parse({ ...movers[0], song_key: "apple:9000000601" });
  mocks.captured.mockReturnValue(row);
  const html = renderToStaticMarkup(
    await MarkPage({
      params: Promise.resolve({ key: "apple%3A9000000601", mark: "0" }),
      searchParams: Promise.resolve({ ranking: row.ranking_build }),
    }),
  );
  expect(mocks.captured).toHaveBeenCalledWith(
    row.ranking_build,
    "apple:9000000601",
  );
  expect(html).toContain('href="/s/song/apple%3A9000000601"');
  expect(html).toContain("/s/proof/apple%3A9000000601/0/engine?");
  expect(html).not.toContain('href="/s/song/apple%253A');
  expect(html).not.toContain('href="/s/proof/apple%253A');
  // Refresh keeps the proof's facts, not just its path.
  expect(html).toContain(
    `data-retry="/s/proof/apple%3A9000000601/0?ranking=${encodeURIComponent(row.ranking_build)}"`,
  );
  await expect(
    MarkPage({
      params: Promise.resolve({ key: "apple%E0%A4%A", mark: "0" }),
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow();
});
it("tells a failed read apart from a missing record", async () => {
  mocks.dayLists.mockRejectedValue(new Error("statement timeout"));
  const html = renderToStaticMarkup(
    await MarkPage({
      params: Promise.resolve({ key: "apple%3A1", mark: "0" }),
      searchParams: Promise.resolve({
        history: "h",
        day: "2026-09-26",
        from: "/songs?view=places",
      }),
    }),
  );
  expect(html).toContain("Proof is out of reach.");
  expect(html).not.toContain("could not be found");
  expect(html).toContain(
    'href="/s/proof/apple%3A1/0?history=h&amp;day=2026-09-26&amp;from=%2Fsongs%3Fview%3Dplaces"',
  );
  expect(html).toContain(">Retry");
});
it("counts daily playlist appearances without score weights", async () => {
  mocks.dayLists.mockResolvedValue({
    value: {
      rows: [
        { platform: "spotify", playlist_id: "a", title: "Viva Latino" },
        { platform: "spotify", playlist_id: "b", title: "Baila Reggaeton" },
      ],
    },
  });
  const view = await markProof("spotify:FixtureTrack0000000601", "0", {
    history: "h",
    day: "2026-09-26",
  });
  expect(view?.headline).toBe("2 playlists.");
  expect(view?.lines).toContain(
    "Playlists with new appearances on 26 Sept. Chart lists appear in Places.",
  );
});
it("uses per-reader membership and local dates on Library and source proof pages", async () => {
  const source = platformSource.parse({
    ...sources.find((source) => source.source_key === "sp_playlist"),
    targets: 139,
  });
  mocks.sources.mockResolvedValue({ value: { sources: [source] } });
  const item = await sourceFacts(person, "source:sp_playlist");
  expect(item?.facts).toEqual([]);
  expect(item?.engine).toBeNull();
  expect(item?.source).toEqual(source);
  const view = await cycleProof(person, "", null, ["sp_playlist"]);
  expect(view.sources).toEqual([source]);
  expect(sourceLine(source)).toContain("36 playlists tracked at Time loading");
  expect(sourceLine(source)).not.toContain("139");
  expect(sourceLine(source, Date.now(), "Asia/Tokyo")).toMatch(/GMT\+9|JST/);
});
