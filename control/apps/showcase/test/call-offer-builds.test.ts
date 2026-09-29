import { afterEach, expect, it, vi } from "vitest";
import type { Session } from "@mdp/showcase-auth";
import type { Build } from "../components/number";
import { mover } from "../server/models";
import { build, movers } from "./browser/fixtures";
import { martBuild } from "@mdp/data-sdk";

vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({ controlStore: vi.fn() }));
vi.mock("../server/call-store", () => ({ readCalls: async () => [] }));
vi.mock("../server/call-reads", () => ({ callAnchors: vi.fn() }));
import { callAnchors } from "../server/call-reads";
import { callOffers, moverProjection } from "../server/call-offers";
import { verifyCall } from "../server/call-token";

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
const row = mover.parse(movers[0]);
const anchorBuild: Build = {
  ...cardBuild,
  relation: "explore_intermediate.int_song_key__daily",
};
const cases: { name: string; change: Partial<Build> }[] = [
  { name: "another cycle", change: { cycle_id: "another-cycle" } },
  { name: "another close", change: { close_no: "999" } },
  { name: "no stamp", change: { stamped: false, built_at: null } },
  { name: "no cycle", change: { cycle_id: null } },
  { name: "no close", change: { close_no: null } },
];
function anchors(stamp: Build) {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  vi.stubEnv("MDP_SHOWCASE_PEOPLE", "[]");
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
afterEach(() => vi.unstubAllEnvs());

it("offers a call when every build matches the card", async () => {
  anchors(anchorBuild);
  const offers = await callOffers(current, [moverProjection(row, cardBuild)]);
  const offer = offers[row.song_key];
  expect(offer).toBeDefined();
  expect(verifyCall(offer.signed, current.handle)?.builds).toEqual([
    cardBuild,
    anchorBuild,
  ]);
});

it.each(cases)(
  "omits an offer when the anchors have $name",
  async ({ change }) => {
    anchors({ ...anchorBuild, ...change });
    expect(
      await callOffers(current, [moverProjection(row, cardBuild)]),
    ).toEqual({});
  },
);

it.each(cases)(
  "omits an offer when another card input has $name",
  async ({ change }) => {
    anchors(anchorBuild);
    const card = moverProjection(row, cardBuild);
    card.builds.push({
      ...cardBuild,
      relation: "marts.mart_song_day",
      ...change,
    });
    expect(await callOffers(current, [card])).toEqual({});
  },
);

it("omits an offer when the card has no build", async () => {
  anchors(anchorBuild);
  const card = moverProjection(row, cardBuild);
  card.builds = [];
  expect(await callOffers(current, [card])).toEqual({});
});
