import { expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { mover, arrival, earlySignal } from "../server/models";
import { clusterLine } from "../lib/cluster";
import { movers, arrivals, earlySignals } from "./browser/fixtures";

it("keeps the matched copies and rules in parsed score evidence", () => {
  const details = {
    cluster_key: "b",
    member_song_keys: ["a", "b", "c"],
    cluster_methods: ["isrc_crosswalk", "title_artist_duration"],
    cluster_confidence: 1,
  };
  const parsed = mover.parse({
    ...movers[0],
    evidence: JSON.stringify([
      { ...mover.parse(movers[0]).evidence[0], ...details },
    ]),
  });
  expect(parsed.evidence[0]).toMatchObject(details);
  expect(clusterLine(parsed.evidence)).toBe(
    "3 copies matched: same ISRC, same title and artist.",
  );
  expect(clusterLine([])).toBeNull();
});

it("keeps matching rules on arrival and early-signal cards", () => {
  const evidence = [
    {
      ...mover.parse(movers[0]).evidence[0],
      cluster_key: "b",
      member_song_keys: ["a", "b"],
      cluster_methods: ["isrc_crosswalk"],
    },
  ];
  const entered = arrival.parse({
    ...arrivals[0],
    evidence: JSON.stringify(evidence),
  });
  const early = earlySignal.parse({
    ...earlySignals[0],
    playlist_count: "1",
    evidence: JSON.stringify(evidence),
  });
  expect(clusterLine(entered.evidence ?? [])).toBe(
    "2 copies matched: same ISRC.",
  );
  expect(clusterLine(early.evidence ?? [])).toBe(
    "2 copies matched: same ISRC.",
  );
});
