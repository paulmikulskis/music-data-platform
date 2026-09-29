import type { ReactNode } from "react";
import type { CallOffer } from "../lib/calls";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { martBuild } from "@mdp/data-sdk";
import { earlySignal, mover } from "../server/models";
import { build, earlySignals, movers } from "./browser/fixtures";

const captured = vi.hoisted(() => ({
  offers: vi.fn<(offers: Record<string, CallOffer>) => void>(),
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/room", () => ({
  room: async () => ({ handle: "fixture", csrf_token: "fixture", person: {} }),
  unavailable: () => null,
}));
vi.mock("../components/shell", () => ({
  Shell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("../components/call-it", async (original) => ({
  ...(await original<typeof import("../components/call-it")>()),
  CallsProvider: ({
    children,
    offers,
  }: {
    children: ReactNode;
    offers: Record<string, CallOffer>;
  }) => {
    captured.offers(offers);
    return children;
  },
}));
vi.mock("../server/platform", () => ({
  tending: async () => ({ sources: [], songs: null }),
}));
vi.mock("../server/clients", () => ({ controlStore: vi.fn() }));
vi.mock("../server/call-store", () => ({ readCalls: async () => [] }));
vi.mock("../server/call-reads", () => ({
  callAnchors: async (keys: string[]) => ({
    value: {
      build: stamp,
      rows: keys.map((key) => ({
        requested_key: key,
        song_key: key,
        platform: "spotify",
        platform_track_id: key,
        source_keys: ["sp_playlist"],
      })),
    },
  }),
}));
vi.mock("../server/reads", async (original) => ({
  ...(await original<typeof import("../server/reads")>()),
  movers: async () => ({
    state: "live",
    value: { rows: [promoted], build: stamp },
  }),
  earlySignals: async () => ({
    state: "cached",
    value: { rows: [stale, unique], build: oldStamp },
  }),
  signalReadiness: async () => null,
}));
import Page from "../app/_rising/page";
import { verifyCall } from "../server/call-token";

const stamp = martBuild.parse(build("mart_top_movers_current"));
const oldStamp = martBuild.parse({
  ...build("mart_early_signals_current"),
  close_no: "40",
  built_at: "2026-09-25T00:00:00Z",
});
const promoted = mover.parse(movers[0]);
const stale = earlySignal.parse({
  ...earlySignals[0],
  playlist_count: "3",
  song_key: promoted.song_key,
  title_text: "Stale early title",
  day: "2026-09-25",
});
const unique = earlySignal.parse({ ...earlySignals[1], playlist_count: "3" });
afterEach(() => vi.unstubAllEnvs());

it("keeps a promoted mover's signed facts when the cached early feed overlaps", async () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "fixture-session-secret");
  vi.stubEnv("MDP_SHOWCASE_PEOPLE", "[]");
  const html = renderToStaticMarkup(
    await Page({ searchParams: Promise.resolve({}) }),
  );
  const offers = captured.offers.mock.calls[0]?.[0];
  expect(offers).toBeDefined();
  const offer = offers?.[promoted.song_key];
  expect(offer).toBeDefined();
  if (!offer) throw new Error("The mover needs a call offer. Rerun this test.");
  expect(verifyCall(offer.signed, "fixture")).toMatchObject({
    card: "mover",
    facts_day: promoted.day,
    close_no: stamp.close_no,
    builds: [stamp, stamp],
  });
  expect(html).not.toContain("Stale early title");
  expect(html).toContain(unique.title_text);
  // The stale card still renders, but its close differs from the live anchors.
  expect(offers?.[unique.song_key]).toBeUndefined();
  expect(Object.keys(offers ?? {})).toHaveLength(1);
});
