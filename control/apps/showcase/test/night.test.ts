import { reviewedReaderKeys, reviewedSources } from "../lib/reviewed-sources";
import { describe, expect, it } from "vitest";
import {
  viewerNight,
  nightMoments,
  nightReaders,
  matchesReady,
  momentClusters,
} from "../lib/night";
import { nightFixture } from "./browser/night-fixture";
import { build } from "./browser/fixtures";
import { tendedCounters } from "../components/tending";
import { platformSource } from "@mdp/contracts";
import { sources } from "./browser/fixtures";
describe("viewer nights", () => {
  it("uses both local dates across daylight-saving changes", () => {
    const spring = viewerNight(
      new Date("2026-03-08T16:00:00Z"),
      "America/New_York",
    );
    expect(spring.since).toBe("2026-03-07T23:00:00.000Z");
    expect(spring.until).toBe("2026-03-08T13:00:00.000Z");
    const fall = viewerNight(
      new Date("2026-11-01T16:00:00Z"),
      "America/New_York",
    );
    expect(fall.since).toBe("2026-10-31T22:00:00.000Z");
    expect(fall.until).toBe("2026-11-01T14:00:00.000Z");
  });
  it("uses half-hour and quarter-hour viewer zones", () => {
    expect(
      viewerNight(new Date("2026-09-27T08:00:00Z"), "Asia/Kathmandu"),
    ).toMatchObject({
      since: "2026-09-26T12:15:00.000Z",
      until: "2026-09-27T03:15:00.000Z",
    });
  });
  it("caps tonight at now and keeps last night before six", () => {
    const now = new Date("2026-09-27T23:15:00Z");
    expect(viewerNight(now, "America/New_York")).toMatchObject({
      title: "Tonight so far",
      since: "2026-09-27T22:00:00.000Z",
      until: now.toISOString(),
    });
    expect(
      viewerNight(new Date("2026-09-27T21:00:00Z"), "America/New_York").title,
    ).toBe("Last night");
  });
});
it("keeps a zero-output failure, partial coverage and unknown trigger", () => {
  const payload = nightFixture({
    since: "2026-09-26T22:00:00Z",
    until: "2026-09-27T13:00:00Z",
  });
  const moments = nightMoments({
    ...payload,
    ready: [],
    ready_saved_at: null,
    ready_state: "unavailable",
  });
  expect(moments).toHaveLength(2);
  expect(moments[0]).toMatchObject({
    status: "Only part delivered",
    coverage: "31 of 48 playlists read",
    trigger: "Started on its own",
  });
  expect(moments[1]).toMatchObject({
    status: "Reading failed",
    coverage: "0 of 48 playlists read",
    trigger: "Start not recorded",
  });
  expect(momentClusters(moments, payload.window, 1000)).toHaveLength(2);
});
it("never associates current songs with a replaced night update", () => {
  const payload = nightFixture({
    since: "2026-09-26T22:00:00Z",
    until: "2026-09-27T13:00:00Z",
  });
  const stamp = {
    ...build("mart_top_movers_current"),
    scope: "global" as const,
  };
  const ready = {
    relation: stamp.relation,
    cycle_id: stamp.cycle_id,
    close_no: stamp.close_no,
    built_at: stamp.built_at,
    build_key: "current",
  };
  const moment = nightMoments({
    ...payload,
    ready: [ready],
    ready_saved_at: null,
    ready_state: "live",
  }).find((m) => m.ready);
  expect(moment).toBeDefined();
  if (!moment) throw new Error("Missing ready fixture. Check the test data.");
  expect(matchesReady(moment, stamp)).toBe(true);
  expect(
    matchesReady(
      {
        ...moment,
        ready: {
          ...ready,
          built_at: new Date(ready.built_at)
            .toISOString()
            .replace(".000Z", ".000000Z"),
        },
      },
      stamp,
    ),
  ).toBe(true);
  expect(
    matchesReady(
      {
        ...moment,
        ready: {
          ...ready,
          built_at: new Date(ready.built_at)
            .toISOString()
            .replace(".000Z", ".000001Z"),
        },
      },
      stamp,
    ),
  ).toBe(false);
  expect(
    matchesReady(moment, { ...stamp, built_at: "2026-09-27T18:00:00Z" }),
  ).toBe(false);
  expect(matchesReady(moment, { ...stamp, stamped: false })).toBe(false);
});
it("excludes a switched-off playlist reader from tracked totals", () => {
  const parsed = platformSource.array().parse(sources);
  const spotify = parsed.find((source) => source.source_key === "sp_playlist");
  if (!spotify) throw new Error("Missing reader fixture. Check the test data.");
  const readers = [
    {
      ...spotify,
      enabled: true,
      tracked: {
        count: 104,
        unit: "playlists" as const,
        as_of: spotify.last_read!,
      },
    },
    {
      ...spotify,
      source_key: "sc_playlist",
      enabled: false,
      tracked: {
        count: 15,
        unit: "playlists" as const,
        as_of: spotify.last_read!,
      },
    },
  ];
  expect(
    tendedCounters(readers, null).find((counter) => counter.key === "playlists")
      ?.value,
  ).toBe(104);
  expect(
    tendedCounters([...readers, { ...readers[0], tracked: null }], null).find(
      (counter) => counter.key === "playlists",
    )?.value,
  ).toBe(104);
});

it("resolves provider nodes to actual reader keys", () => {
  expect(reviewedReaderKeys.has("am_playlist")).toBe(true);
  expect(reviewedReaderKeys.has("sp_playlist")).toBe(true);
  expect(reviewedReaderKeys.has("Apple Music")).toBe(false);
  expect(
    reviewedSources.find((source) => source.id === "source:Apple Music")
      ?.readers,
  ).toContain("am_playlist");
});

it("summarizes a reader once using distinct targets across two runs", () => {
  const payload = nightFixture({
    since: "2026-09-26T22:00:00Z",
    until: "2026-09-27T13:00:00Z",
  });
  const first = payload.runs[0];
  const second = {
    ...first,
    run_id: "00000000-0000-4000-8000-000000000063",
    admitted_at: "2026-09-27T04:00:00.000Z",
    attempts: first.attempts.map((attempt) => ({
      ...attempt,
      started_at: "2026-09-27T04:00:00.000Z",
      ended_at: "2026-09-27T04:05:00.000Z",
      targets: { ...first.targets, succeeded: 20 },
    })),
  };
  const data = {
    ...payload,
    // Reverse admission order to exercise the timestamp ordering too.
    runs: [second, first],
    // The two runs overlap: their distinct total is neither their sum nor the latest count.
    coverage: [{ ...payload.coverage[0], succeeded: 40 }],
    ready: [],
    ready_saved_at: null,
    ready_state: "unavailable" as const,
  };
  expect(nightMoments(data)).toHaveLength(2);
  expect(nightReaders(data)).toMatchObject([
    {
      source: "am_playlist",
      id: `${second.run_id}:1`,
      coverage: "40 playlists read",
    },
  ]);
  data.coverage[0].succeeded = 0;
  expect(nightReaders(data)[0].coverage).toBe("0 playlists read");
  expect(nightReaders({ ...data, coverage: [] })[0].coverage).toBe(
    "Read count not measured",
  );
  expect(
    nightReaders({
      ...data,
      coverage: [{ ...data.coverage[0], succeeded: null }],
    })[0].coverage,
  ).toBe("Read count not measured");
});
