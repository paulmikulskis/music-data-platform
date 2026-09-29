import { afterEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { z } from "zod";
import { build, days, movers } from "./browser/fixtures";

const current = vi.hoisted(() => ({ representative: "a" }));
vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({
  warehouse: () => ({ unsafe: async () => [] }),
}));
vi.mock("@orpc/client", () => ({
  createORPCClient: () => ({
    mart_song_cluster_members: async (input: unknown) => {
      const key = z
        .object({ filters: z.object({ song_key: z.string() }) })
        .parse(input).filters.song_key;
      return {
        rows: [
          {
            song_key: key,
            cluster_key: current.representative,
            representative_song_key: current.representative,
            cluster_methods: '["isrc_crosswalk"]',
            source_keys: "[]",
            learning_eligible: false,
            resale_permitted: false,
          },
        ],
        build: build("mart_song_cluster_members"),
        next_cursor: null,
      };
    },
    mart_top_movers_current: async (input: unknown) => {
      const key = z
        .object({ filters: z.object({ song_key: z.string() }) })
        .parse(input).filters.song_key;
      return {
        rows:
          key === current.representative
            ? [{ ...movers[0], song_key: current.representative }]
            : [],
        build: build("mart_top_movers_current"),
        next_cursor: null,
      };
    },
  }),
}));
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
vi.mock("../server/reads", async (original) => {
  const reads = await original<typeof import("../server/reads")>();
  return {
    ...reads,
    alias: async () => ({ value: { rows: [] } }),
    songHistory: async () => ({
      value: {
        rows: days,
        build: build("mart_song_day"),
        queried_at: "2026-09-26",
      },
    }),
    identities: async () => null,
    movementReadiness: async () => null,
    songPlaces: async () => null,
  };
});
vi.mock("../components/shell", () => ({
  Shell: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("../components/song", () => ({
  Song: ({
    song,
  }: {
    song: { song_key: string; momentum_score: number | null };
  }) => (
    <p>
      {song.song_key}:{song.momentum_score}
    </p>
  ),
}));
import Page from "../app/s/song/[key]/page";
import { budget } from "../server/read-budget";
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});
it("keeps both bookmarked song pages on the scored row after a representative change", async () => {
  vi.stubEnv("MDP_DATA_API_URL", "http://fixture.local");
  vi.stubEnv("MDP_SHOWCASE_READER_KEY", "fixture");
  vi.useFakeTimers();
  for (const representative of ["a", "b"]) {
    current.representative = representative;
    vi.advanceTimersByTime(60000);
    budget.setRunner("idle");
    for (const key of ["a", "b"]) {
      const page = await Page({
        params: Promise.resolve({ key }),
        searchParams: Promise.resolve({}),
      });
      expect(renderToStaticMarkup(page)).toContain(`${representative}:0.8`);
    }
  }
});
