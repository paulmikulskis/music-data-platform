import { expect, it } from "vitest";
import { lanePath, modelState, proofLink, updated } from "../lib/presentation";
it("routes captured proof through the cycle resolver", () => {
  const mark = {
    component: "stream_rate_gain",
    relation: "mart_track_daily_streams",
    row_key: {},
    window: {},
    input_build: {
      relation: "marts.mart_track_daily_streams",
      scope: "global",
      cycle_id: "00000000-0000-4000-8000-000000000041",
      close_no: "41",
      built_at: "2026-09-25T06:12:00Z",
    },
  };
  expect(proofLink(mark)).toBe(
    `/s/proof/cycle/${mark.input_build.cycle_id}?relation=mart_track_daily_streams`,
  );
  expect(
    proofLink({
      ...mark,
      input_build: { ...mark.input_build, built_at: null },
    }),
  ).toBeNull();
});
it("breaks lanes at missing observations instead of drawing invented growth", () => {
  expect(lanePath([1, null, 2])).toBe("M0.00,34.00  M300.00,8.00");
  expect(lanePath([null, null])).toBe(" ");
});
it("requires a matching ranking and evidence before showing model prose", () => {
  const ranking = { ranking_build: "today", evidence_hash: "hash" };
  const explanation = { ...ranking, reason_model: "A supported reason." };
  expect(modelState(false, explanation, ranking).state).toBe("Held: rights");
  expect(modelState(true).state).toBe("Pending");
  expect(
    modelState(true, explanation, { ...ranking, evidence_hash: "other" }).state,
  ).toBe("Stale");
  expect(modelState(true, explanation, ranking).text).toBe(
    "A supported reason.",
  );
});
it("uses the oldest on-screen time and never substitutes query time for an absent stamp", () => {
  const p = {
    queried_at: "2026-09-25T12:00:00Z",
    observed_at: "2026-09-24T00:00:00Z",
    scope: "global",
    query: "Fact",
    sql: "SELECT 1",
    provenance: "live query" as const,
  };
  expect(updated([p])).toBe("2026-09-24T00:00:00.000Z");
  expect(updated([p, undefined])).toBeNull();
  expect(updated([{ ...p, provenance: undefined }])).toBeNull();
  // A reviewed list never passes its query time off as a read time.
  const list = {
    ...p,
    observed_at: undefined,
    provenance: "reviewed list" as const,
  };
  expect(updated([list])).toBeNull();
  expect(updated([p, list])).toBe("2026-09-24T00:00:00.000Z");
});
it("accepts SQL dates in the history view without changing their UTC day", async () => {
  const { mover } = await import("../server/models");
  const { movers } = await import("./browser/fixtures");
  expect(
    mover.parse({ ...movers[0], day: new Date("2026-09-25T00:00:00Z") }).day,
  ).toBe("2026-09-25");
});
it("uses raw playlist evidence and observed gains without relabelling model scores", async () => {
  const { musicFacts } = await import("../lib/music-facts");
  const { mover } = await import("../server/models");
  const { movers } = await import("./browser/fixtures");
  const song = mover.parse({ ...movers[0], playlist_count: "4" });
  expect(musicFacts(song).map((f) => f.text)).toEqual([
    "Added to 4 playlists over 3 days.",
    "Plays up 38% over 2 days.",
    "Added playlists total 1,200 followers.",
  ]);
  expect(
    musicFacts({
      ...song,
      playlist_count: null,
      score_parts: [
        { component: "playlist_adds", value: 900 },
        { component: "shazam_spread_gain", value: 12 },
      ],
    }),
  ).toEqual([]);
  expect(musicFacts({ ...song, evidence: [] })).toEqual([]);
  expect(mover.parse({ ...movers[0], score_parts: null }).score_parts).toEqual(
    [],
  );
});
it("names chart places from decoded city slugs", async () => {
  const { placeName } = await import("../lib/places");
  expect(placeName({ country: "brazil", city: "s%C3%A3o-paulo" })).toBe(
    "São Paulo",
  );
  expect(placeName({ country: "germany", city: "m%C3%BCnster-nord" })).toBe(
    "Münster Nord",
  );
  expect(placeName({ country: "germany", city: null })).toBe("Germany");
  expect(placeName({ country: null, city: null })).toBe("Global chart");
});
