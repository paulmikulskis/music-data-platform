import { describe, expect, it } from "vitest";
import { lineage, lineageSchema, resolveFactPath } from "../lib/lineage";
import { stackSchema } from "../lib/stack";

const selected = {
  entry: "home.playlist_adds",
  song_key: "song:one",
  ranking_build: "ranking-one",
};
const input_build = {
  relation: "mart_playlist_events",
  scope: "global",
  cycle_id: "cycle-one",
  close_no: "12",
  built_at: "2026-09-27T00:00:00Z",
};
const addition = {
  component: "playlist_adds",
  relation: "mart_playlist_events",
  input_build,
  row_key: {
    platform: "bandcamp",
    playlist_id: "historical-list",
    variant: "default",
    stream: "full",
    occurrence_key: "occurrence-one",
    interval_id: "interval-one",
    event_type: "add",
    observed_at: "2026-09-20T00:00:00Z",
  },
};
const profile = {
  component: "follower_exposure_gain",
  relation: "mart_playlist_profile",
  input_build: { ...input_build, relation: "mart_playlist_profile" },
  row_key: {
    platform: "spotify",
    playlist_id: "list-two",
    variant: "default",
    stream: "full",
    snapshot_id: "snapshot-two",
  },
};
const row = {
  song_key: selected.song_key,
  ranking_build: selected.ranking_build,
  evidence: [addition, profile],
  source_keys: ["sp_playlist", "bc_daily_list", "sz_chart"],
};

describe("fact-specific lineage", () => {
  it("parses the complete generated artifact without casts", () => {
    expect(lineageSchema.parse(lineage).meaning).toBe("Can feed this");
    expect(lineage.entries).toHaveLength(4);
  });
  it("takes different evidence paths for two facts on one song", () => {
    const adds = resolveFactPath(selected, row);
    const audience = resolveFactPath(
      { ...selected, entry: "home.follower_exposure_gain" },
      row,
    );
    expect(adds.edges.map((edge) => edge.from)).toEqual([
      "rel:marts.mart_playlist_events",
    ]);
    expect(audience.edges.map((edge) => edge.from)).toEqual([
      "rel:marts.mart_playlist_profile",
    ]);
    expect(adds.evidence[0]?.row_key.platform).toBe("bandcamp");
  });
  it("keeps historical evidence without consulting today's enabled state", () => {
    expect(resolveFactPath(selected, row).state).toBe("evidenced");
  });
  it("does not light an upstream writer from source_keys membership", () => {
    expect(resolveFactPath(selected, { ...row, evidence: [] }).edges).toEqual(
      [],
    );
    expect(
      resolveFactPath(selected, row).edges.some((edge) =>
        edge.from.includes("sz_chart"),
      ),
    ).toBe(false);
  });
  it("does not replace missing, old or unbound evidence with today's song", () => {
    for (const value of [
      null,
      { ...row, ranking_build: "other" },
      { ...row, song_key: "other" },
      {
        ...row,
        evidence: [
          { ...addition, input_build: { ...input_build, cycle_id: null } },
        ],
      },
    ]) {
      expect(resolveFactPath(selected, value).state).toBe("unavailable");
    }
  });
  it("lights a historical collector only with an exact contributing receipt", () => {
    const contribution = {
      song_key: selected.song_key,
      ranking_build: selected.ranking_build,
      locator: addition,
      receipt: {
        source_key: "bc_daily_list",
        target_table: "raw.playlist_items",
        run_id: "00000000-0000-4000-8000-000000000001",
        dump_id: "00000000-0000-4000-8000-000000000002",
      },
    };
    expect(
      resolveFactPath(selected, row, lineage, [contribution]).edges.some(
        (edge) => edge.from === "fn:bc_daily_list",
      ),
    ).toBe(true);
    expect(
      resolveFactPath(selected, row, lineage, [
        { ...contribution, locator: profile },
      ]).edges.some((edge) => edge.from === "fn:bc_daily_list"),
    ).toBe(false);
  });
  it("drops extra identity and personal-text fields before returning evidence", () => {
    const result = resolveFactPath(selected, {
      ...row,
      evidence: [
        {
          ...addition,
          row_key: {
            ...addition.row_key,
            creator: "creator-sentinel",
            comment: "text-sentinel",
          },
        },
      ],
    });
    expect(JSON.stringify(result)).not.toMatch(
      /creator-sentinel|text-sentinel/,
    );
  });
  it("rejects tenant evidence and incomplete row keys", () => {
    expect(
      resolveFactPath(selected, {
        ...row,
        evidence: [
          {
            ...addition,
            input_build: { ...input_build, scope: "tenant:sentinel" },
          },
        ],
      }).edges,
    ).toEqual([]);
    expect(
      resolveFactPath(selected, {
        ...row,
        evidence: [{ ...addition, row_key: { platform: "bandcamp" } }],
      }).edges,
    ).toEqual([]);
  });
  it("keeps tenant and personal fields out of peek projections", () => {
    for (const [relation, projection] of Object.entries(lineage.peeks)) {
      expect(relation).not.toContain("tenant");
      expect(projection.columns.length).toBeLessThanOrEqual(4);
      expect(projection.columns.join(" ")).not.toMatch(
        /creator|comment|owner|description/,
      );
    }

  });
  it("rejects unrecognized infrastructure fields and undated probe claims", () => {
    const service = {
      alias: "Readers",
      kind: "Server",
      process_role: "Collector and API",
      region: "New Jersey",
      machine_count: 1,
      cpu_total: 1,
      memory_mb_total: 512,
      storage_gb_total: 0,
      encrypted: null,
      measured_at: input_build.built_at,
      public_link: null,
      status: { state: "not_checked", name: "Not checked", checked_at: null },
      facts: [],
    };
    const stack = {
      schema_version: 1,
      input_hashes: {},
      captured_at: input_build.built_at,
      services: [service],
    };
    expect(stackSchema.safeParse(stack).success).toBe(true);
    expect(
      stackSchema.safeParse({
        ...stack,
        services: [{ ...service, machine_id: "private-sentinel" }],
      }).success,
    ).toBe(false);
    expect(
      stackSchema.safeParse({
        ...stack,
        services: [
          {
            ...service,
            status: {
              state: "answered",
              name: "Console HTTP health",
              checked_at: null,
            },
          },
        ],
      }).success,
    ).toBe(false);
  });
});
