import { expect, it } from "vitest";
import type { Source } from "../components/sources";
import { tendedCounters } from "../components/tending";
import { syntheticSources } from "./synthetic-fixture";

it("keeps counted charts when Billboard has no target set", () => {
  const counter = tendedCounters(
    syntheticSources.sources,
    null,
    Date.parse(syntheticSources.queried_at),
  ).find((item) => item.key === "charts");
  const shazam = syntheticSources.sources.find(
    (source) => source.source_key === "sz_chart",
  );
  expect(counter?.value).toBe(shazam?.tracked?.count);
  expect(counter?.line).toContain("Not counted: Billboard Hot 100.");
});
it("keeps playlist totals with enabled hubs and counts curators in their own unit", () => {
  const sources = syntheticSources.sources.map((source): Source =>
    source.source_key === "sc_hubs"
      ? { ...source, enabled: true }
      : source.source_key === "sc_curator_playlists"
        ? {
            ...source,
            enabled: true,
            tracked: {
              count: 7,
              unit: "curators",
              as_of: syntheticSources.queried_at,
            },
          }
        : source,
  );
  const counters = tendedCounters(
    sources,
    null,
    Date.parse(syntheticSources.queried_at),
  );
  const counter = counters.find((item) => item.key === "playlists");
  const expected = sources
    .filter(
      (source) =>
        source.enabled &&
        source.family === "playlists" &&
        source.tracked?.unit === "playlists",
    )
    .reduce((total, source) => total + (source.tracked?.count ?? 0), 0);
  expect(counter?.value).toBeGreaterThan(0);
  expect(counter?.value).toBe(expected);
  expect(counter?.line).toContain("SoundCloud hubs");
  expect(counter?.line).toContain("SoundCloud curators");
});
it("preserves zero and reserves missing for families with no measured readers", () => {
  const source = syntheticSources.sources.find(
    (source) => source.source_key === "sz_chart",
  )!;
  expect(
    tendedCounters(
      [
        {
          ...source,
          enabled: true,
          tracked: {
            count: 0,
            unit: "charts",
            as_of: syntheticSources.queried_at,
          },
        },
      ],
      null,
      0,
    ).find((item) => item.key === "charts")?.value,
  ).toBe(0);
  expect(
    tendedCounters([{ ...source, tracked: null }], null, 0).find(
      (item) => item.key === "charts",
    )?.value,
  ).toBeNull();
});
