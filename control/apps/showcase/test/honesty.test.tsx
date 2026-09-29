import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { platformSource } from "@mdp/contracts";
import { honesty, remediation } from "../lib/honesty";
import { localTime, LocalTime } from "../components/local-time";
import { sourceLine } from "../components/sources";

const at = "2026-09-27T12:00:00.000Z";
const source = platformSource.parse({
  source_key: "am_playlist_weekly",
  display_name: "Apple Music playlists",
  brand: "apple_music",
  family: "playlists",
  description: "Public playlists.",
  cadence: "daily",
  enabled: true,
  first_collected: null,
  last_read: null,
  entries_today: "0",
  targets: 139,
  days: [],
  tracked: { count: 0, unit: "playlists", as_of: at },
  evidence: {
    declared_at: at,
    configured_at: at,
    checked_at: at,
    tenant_bound: false,
    last_success: null,
    attempt: null,
    incident: null,
  },
});
const evidence = source.evidence!;
describe("dated source states", () => {
  it("keeps enabled but empty separate from live and the shared list", () => {
    expect(honesty(source, Date.parse(at)).state).toBe("empty");
    expect(sourceLine(source)).toContain("Enabled · no targets");
    expect(sourceLine(source)).not.toContain("139");
  });
  it("does not infer health from old loads or registry defaults", () => {
    expect(honesty({ ...source, evidence: null }).state).toBe("unknown");
    expect(honesty({ ...source, enabled: false }).label).toBe(
      "Built · switched off",
    );
    expect(honesty(null).state).toBe("unavailable");
    const collected = {
      ...source,
      tracked: { ...source.tracked!, count: 20 },
      evidence: { ...evidence, last_success: at },
    };
    expect(honesty(collected, Date.parse(at)).state).toBe("current");
    expect(honesty(collected, Date.parse(at) + 8 * 86400000).state).toBe(
      "overdue",
    );
    for (const status of ["running", "queued", "partial"] as const) {
      expect(
        honesty({
          ...collected,
          evidence: {
            ...collected.evidence,
            attempt: {
              run_id: "00000000-0000-4000-8000-000000000001",
              attempt_no: 2,
              status,
              at,
            },
          },
        }).state,
      ).toBe(status);
    }
  });
  it.each(["sp_playlist_weekly", "am_playlist_weekly", "sc_playlist_weekly"])(
    "uses the daily reader schedule for %s across target weekday buckets",
    (source_key) => {
      const collected = {
        ...source,
        source_key,
        tracked: { ...source.tracked!, count: 20 },
        evidence: { ...evidence, last_success: at },
      };
      expect(honesty(collected, Date.parse(at) + 5 * 86400000).state).toBe(
        "overdue",
      );
      expect(honesty(collected, Date.parse(at) + 23 * 3600000).state).toBe(
        "current",
      );
      expect(
        honesty(
          { ...collected, cadence: "weekly" },
          Date.parse(at) + 5 * 86400000,
        ).state,
      ).toBe("current");
      for (const cadence of [null, "unknown", "on_demand"]) {
        expect(honesty({ ...collected, cadence }, Date.parse(at)).state).toBe(
          "unknown",
        );
      }
    },
  );
  it.each([
    {
      cadence: "hourly",
      currentMinutes: 65,
      overdueMinutes: 105,
      boundaryMinutes: 90,
    },
    {
      cadence: "daily",
      currentMinutes: 25 * 60,
      overdueMinutes: 28 * 60,
      boundaryMinutes: 27 * 60,
    },
    {
      cadence: "weekly",
      currentMinutes: 174 * 60,
      overdueMinutes: 186 * 60,
      boundaryMinutes: 180 * 60,
    },
  ])(
    "allows the declared grace for a $cadence reader",
    ({ cadence, currentMinutes, overdueMinutes, boundaryMinutes }) => {
      const collected = {
        ...source,
        cadence,
        tracked: { ...source.tracked!, count: 20 },
        evidence: { ...evidence, last_success: at },
      };
      const afterMinutes = (minutes: number) =>
        Date.parse(at) + minutes * 60000;
      expect(honesty(collected, afterMinutes(currentMinutes)).label).toBe(
        "Up to date",
      );
      expect(honesty(collected, afterMinutes(overdueMinutes)).state).toBe(
        "overdue",
      );
      expect(honesty(collected, afterMinutes(boundaryMinutes)).state).toBe(
        "current",
      );
      expect(honesty(collected, afterMinutes(boundaryMinutes) + 1).state).toBe(
        "overdue",
      );
      expect(
        honesty(
          {
            ...collected,
            evidence: {
              ...collected.evidence,
              attempt: {
                run_id: "00000000-0000-4000-8000-000000000001",
                attempt_no: 2,
                status: "failed",
                at,
              },
            },
          },
          afterMinutes(currentMinutes),
        ).state,
      ).toBe("overdue");
    },
  );
  it("never invents a repair for a paused reader", () => {
    const paused = {
      ...source,
      enabled: false,
      evidence: {
        ...evidence,
        incident: {
          alert_id: "00000000-0000-4000-8000-000000000002",
          run_id: null,
          attempt_no: null,
          class: "surface_drift",
          at,
          remediation: null,
        },
      },
    };
    expect(honesty(paused).label).toBe("Paused · see details");
    expect(remediation(paused)).toBeNull();
    expect(honesty({ ...paused, source_key: "fixture_tracks" }).state).toBe(
      "paused",
    );
    expect(
      honesty({
        ...paused,
        evidence: {
          ...paused.evidence,
          incident: {
            ...paused.evidence.incident,
            class: "provider_credentials_missing",
          },
        },
      }).state,
    ).toBe("access");
    expect(
      remediation({
        ...paused,
        evidence: {
          ...paused.evidence,
          incident: {
            ...paused.evidence.incident,
            remediation: "Parser repair is recorded. Open source details.",
          },
        },
      }),
    ).toBe("Parser repair is recorded. Open source details.");
  });
});
it("uses a neutral server placeholder and names the viewer zone across DST", () => {
  const html = renderToStaticMarkup(<LocalTime at={at} />);
  expect(html).toContain("Time loading");
  expect(html).not.toContain("UTC");
  const before = localTime(
    "2026-03-08T06:30:00Z",
    false,
    new Date(at),
    "America/New_York",
  );
  const after = localTime(
    "2026-03-08T07:30:00Z",
    false,
    new Date(at),
    "America/New_York",
  );
  expect(before).toMatch(/1:30.*(?:EST|GMT-5)/);
  expect(after).toMatch(/3:30.*(?:EDT|GMT-4)/);
  expect(localTime(at, false, new Date(at), "Asia/Tokyo")).toMatch(
    /9:00.*(?:GMT\+9|JST)/,
  );
  expect(localTime(at, false, new Date(at), "UTC")).toContain("UTC");
});

it("compares calendar days in the viewer zone across a short DST day", () => {
  expect(
    localTime(
      "2026-03-09T00:15:00Z",
      true,
      new Date("2026-03-08T04:30:00Z"),
      "America/New_York",
    ),
  ).toMatch(/^Tomorrow,.*(?:EDT|GMT-4)/);
  expect(
    localTime(
      "2026-03-08T04:30:00Z",
      true,
      new Date("2026-03-09T00:15:00Z"),
      "America/New_York",
    ),
  ).toMatch(/^Yesterday,.*(?:EST|GMT-5)/);
});
