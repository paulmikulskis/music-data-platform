import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Session } from "@mdp/showcase-auth";
import type { Build } from "../components/number";
import { mover } from "../server/models";
import { build, movers as moverRows } from "./browser/fixtures";
import { martBuild } from "@mdp/data-sdk";
import { CALLS_PER_WEEK } from "../lib/calls";

// Picks's weekly board asks homeCalls whether a Home song can be called now.
const state = vi.hoisted(() => ({
  calls: [] as {
    author: string;
    undone_at: string | null;
    facts: { anchors: { platform: string; platform_track_id: string }[] };
  }[],
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({ controlStore: vi.fn() }));
vi.mock("../server/call-store", () => ({ readCalls: async () => state.calls }));
vi.mock("../server/call-reads", () => ({ callAnchors: vi.fn() }));
vi.mock("../server/reads", () => ({ movers: vi.fn(), arrivals: vi.fn() }));
import { callAnchors } from "../server/call-reads";
import { arrivals, movers } from "../server/reads";
import { homeCalls } from "../server/call-offers";

const current: Session = {
  id_hash: "fixture",
  handle: "fixture",
  csrf_token: "fixture",
  person: {
    handle: "fixture",
    display_name: "Test viewer",
    email: "fixture@example.invalid",
    admin_key: "fixture",
    api_key_id: "00000000-0000-4000-8000-000000000001",
  },
};
const cardBuild = martBuild.parse(build("mart_top_movers_current"));
const row = mover.parse(moverRows[0]);
const anchorBuild: Build = {
  ...cardBuild,
  relation: "explore_intermediate.int_song_key__daily",
};
function home(rows: (typeof row)[]) {
  vi.mocked(movers).mockResolvedValue({
    state: "live",
    savedAt: "2026-09-26T12:00:00Z",
    value: { rows, build: cardBuild, next_cursor: null, queried_at: "2026-09-26T12:00:00Z", sql: "" },
  });
  vi.mocked(arrivals).mockResolvedValue({
    state: "live",
    savedAt: "2026-09-26T12:00:00Z",
    value: { rows: [], build: cardBuild, queried_at: "2026-09-26T12:00:00Z", sql: "" },
  });
}
function anchors(stamp: Build) {
  vi.mocked(callAnchors).mockResolvedValue({
    state: "live",
    savedAt: "2026-09-26T12:00:00Z",
    value: {
      build: stamp,
      rows: [
        {
          requested_key: row.song_key,
          song_key: row.song_key,
          platform: "apple",
          platform_track_id: "fixture",
          source_keys: ["am_playlist"],
        },
      ],
    },
  });
}
beforeEach(() => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  vi.stubEnv("MDP_SHOWCASE_PEOPLE", "[]");
  state.calls = [];
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetAllMocks();
});

it("opens calls when a Home song's reads come from one closed daily read", async () => {
  home([row]);
  anchors(anchorBuild);
  expect(await homeCalls(current)).toBe("open");
});

it("waits for the next daily read when no Home song can be called yet", async () => {
  home([row]);
  anchors({ ...anchorBuild, stamped: false, built_at: null });
  expect(await homeCalls(current)).toBe("waiting");
});

it("counts undone calls against the week, so a clear board can have none left", async () => {
  home([row]);
  anchors(anchorBuild);
  state.calls = Array.from({ length: CALLS_PER_WEEK }, (_, i) => ({
    author: current.handle,
    undone_at: i % 2 ? "2026-09-25T10:00:00Z" : null,
    facts: { anchors: [{ platform: "spotify", platform_track_id: `other-${i}` }] },
  }));
  expect(await homeCalls(current)).toBe("used");
});

it("says there are no songs when Home has neither movers nor a lead arrival", async () => {
  home([]);
  expect(await homeCalls(current)).toBe("no_songs");
});

it("says the state is unknown when a read fails", async () => {
  vi.mocked(movers).mockRejectedValue(new Error("Warehouse unavailable."));
  expect(await homeCalls(current)).toBe("unknown");
});
