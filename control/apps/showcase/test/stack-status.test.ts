import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));

vi.mock("../server/relation-counts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../server/relation-counts")>();
  return { ...actual, countInputs: [...actual.countInputs, {relation: "marts.mart_hourly_fixture"}] };
});
const person = {
  handle: "fixture",
  display_name: "Fixture",
  admin_key: "k",
  api_key_id: "00000000-0000-4000-8000-000000000001",
};
const cycle = "00000000-0000-4000-8000-00000000000c";
const older = "00000000-0000-4000-8000-00000000000b";
// One night fixture the tests reshape: the runner state, the closes and the rebuilt stamps.
type Fixture = {
  runner: { state: string };
  closes: {
    cycle_id: string;
    cadence: string;
    close_no: string | null;
    closed_at: string;
    status: string;
  }[];
  ready: {
    relation: string;
    cycle_id: string;
    close_no: string | null;
    built_at: string;
    build_key: string;
  }[];
  ready_saved_at: string;
  ready_state: string;
};
const fixture = vi.hoisted((): { value: Fixture } => ({
  value: {
    runner: { state: "idle" },
    closes: [],
    ready: [],
    ready_saved_at: "2026-09-27T12:00:00Z",
    ready_state: "live",
  },
}));
vi.mock("../server/session", () => ({
  session: async () => ({ handle: "fixture", person }),
}));
vi.mock("../server/clients", () => ({
  warehouse: () => async () => [{ "?column?": 1 }],
  controlStore: () => null,
}));
vi.mock("../server/night", () => ({
  night: async () => ({
    state: "live",
    savedAt: "2026-09-27T12:00:00Z",
    value: fixture.value,
  }),
}));
import { stackStatus as statusSchema } from "../lib/stack";
import { stackAliases } from "../lib/stack-facts";

const stamp = (
  cycle_id: string,
  built_at: string,
  relation = "marts.mart_hourly_fixture",
  close_no: string | null = "1",
) => ({
  relation,
  cycle_id,
  close_no,
  built_at,
  build_key: `[${relation},${cycle_id}]`,
});
function healthyNight(now: Date) {
  const settled = new Date(now.getTime() - 50 * 60000).toISOString();
  const recent = new Date(now.getTime() - 5 * 60000).toISOString();
  fixture.value = {
    runner: { state: "idle" },
    closes: [
      {
        cycle_id: older,
        cadence: "hourly",
        close_no: "1",
        closed_at: settled,
        status: "closed",
      },
      // A weekly round leaves no reviewed stamp; it counts as a round and is never the target.
      {
        cycle_id: "00000000-0000-4000-8000-00000000000e",
        cadence: "weekly",
        close_no: "2",
        closed_at: recent,
        status: "closed",
      },
    ],
    ready: [stamp(older, new Date(now.getTime() - 45 * 60000).toISOString())],
    ready_saved_at: new Date().toISOString(),
    ready_state: "live",
  };
}
beforeEach(() => {
  vi.resetModules();
  vi.doMock("../lib/lineage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/lineage")>();
  return { ...actual, lineage: { ...actual.lineage, nodes: [...actual.lineage.nodes,
    {kind: "relation", relation: "marts.mart_hourly_fixture", cadence: "hourly"}] } };
});
  vi.stubEnv("MDP_CONTROL_API_URL", "http://control.invalid");
  vi.stubEnv("MDP_DATA_API_URL", "http://data.invalid");
  vi.stubEnv("MDP_SERVICE_URL", "http://service.invalid");
  vi.stubEnv("MDP_WORKBENCH_URL", "");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
function stubFetch(urls: string[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      return new Response("{}", { status: 200 });
    }),
  );
}
async function clock() {
  const { stackStatus } = await import("../server/stack-status");
  const status = statusSchema.parse(await stackStatus(person));
  return status.services.find((s) => s.alias === "The clock")!;
}

it("names a probe and time for every service, or says nothing checked it", async () => {
  const urls: string[] = [];
  stubFetch(urls);
  healthyNight(new Date());
  const { stackStatus } = await import("../server/stack-status");
  const status = statusSchema.parse(await stackStatus(person));
  expect(status.services.map((s) => s.alias).sort()).toEqual(
    [...stackAliases].sort(),
  );
  const by = (alias: string) => status.services.find((s) => s.alias === alias)!;
  expect(by("Warehouse")).toMatchObject({
    state: "answered",
    probe: "Warehouse SQL check",
  });
  expect(by("The clock")).toMatchObject({
    state: "answered",
    probe: "Runner state, last rounds and rebuilt tables",
    note: "2 rounds completed in the last day. No daily round closed in that day. The last hourly round rebuilt 1 ready table.",
  });
  expect(by("MusicBrainz copy")).toMatchObject({
    state: "not_checked",
    probe: "Not checked",
    checked_at: null,
  });
  expect(by("Side door").state).toBe("not_checked");
  expect(by("Workbench")).toMatchObject({ state: "not_checked" });
  expect(by("This app").state).toBe("answered");
  expect(urls).toContain("http://control.invalid/health");
  expect(urls).toContain("http://data.invalid/health");
  expect(urls).toContain("http://service.invalid/v1/health");
  // Notes carry no address, host or identifier from the probe targets.
  expect(JSON.stringify(status)).not.toMatch(/invalid|fly\.dev|http/);
  const { GET } = await import("../app/s/stack/route");
  const response = await GET();
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
});
it("checks the reviewed cadences from the lineage, never a cadence with no reviewed table", async () => {
  const { rebuildingCadences } = await import("../server/stack-status");
  expect([...rebuildingCadences].sort()).toEqual(["daily", "hourly"]);
});
it("reads a failed build: a round closed, but no reviewed table carries its stamp", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  // The hourly round that settled 50 minutes ago never rebuilt anything; an older stamp remains.
  fixture.value.ready = [
    stamp(
      cycle,
      new Date(now.getTime() - 3 * 3600000).toISOString(),
      undefined,
      "0",
    ),
  ];
  expect(await clock()).toMatchObject({
    state: "no_answer",
    probe: "Runner state, last rounds and rebuilt tables",
    note: "The last hourly round closed without rebuilding a ready table.",
  });
});
it("waits out the rebuild grace: a round closed minutes ago checks the previous settled round", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.closes.push({
    cycle_id: cycle,
    cadence: "hourly",
    close_no: "3",
    closed_at: new Date(now.getTime() - 2 * 60000).toISOString(),
    status: "closed",
  });
  expect(await clock()).toMatchObject({
    state: "answered",
    note: "3 rounds completed in the last day. No daily round closed in that day. An earlier hourly round rebuilt 1 ready table.",
  });
});
it.each([
  { target: "138", replacement: "139" },
  { target: null, replacement: null },
  { target: "0", replacement: "0" },
  { target: "9007199254740993", replacement: "9007199254740994" },
])(
  "accepts a newer hourly stamp during the grace ($target → $replacement)",
  async ({ target, replacement }) => {
    stubFetch();
    const now = new Date();
    healthyNight(now);
    fixture.value.closes[0].close_no = target;
    fixture.value.closes.push({
      cycle_id: cycle,
      cadence: "hourly",
      close_no: replacement,
      closed_at: new Date(now.getTime() - 10 * 60000).toISOString(),
      status: "closed",
    });
    // The same table's stamp replaces the settled round's stamp.
    fixture.value.ready = [
      stamp(cycle, now.toISOString(), undefined, replacement),
    ];
    expect(await clock()).toMatchObject({
      state: "answered",
      note: "3 rounds completed in the last day. No daily round closed in that day. The last hourly round rebuilt 1 ready table.",
    });
  },
);
it.each([
  { target: "138", stamped: "137" },
  { target: "9007199254740993", stamped: "9007199254740992" },
])(
  "rejects a stamp before the settled hourly close ($stamped < $target)",
  async ({ target, stamped }) => {
    stubFetch();
    const now = new Date();
    healthyNight(now);
    fixture.value.closes[0].close_no = target;
    fixture.value.ready = [stamp(cycle, now.toISOString(), undefined, stamped)];
    expect(await clock()).toMatchObject({
      state: "no_answer",
      note: "The last hourly round closed without rebuilding a ready table.",
    });
  },
);
it("does not use an open round as legacy replacement evidence", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.closes[0].close_no = null;
  fixture.value.closes.push({
    cycle_id: cycle,
    cadence: "hourly",
    close_no: null,
    closed_at: now.toISOString(),
    status: "open",
  });
  fixture.value.ready = [stamp(cycle, now.toISOString(), undefined, null)];
  expect(await clock()).toMatchObject({ state: "no_answer" });
});
it("does not let a later hourly table hide an unstamped daily round", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.closes.push(dailyRound(now, 300));
  fixture.value.closes[0].close_no = "10";
  fixture.value.ready[0].close_no = "10";
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "The last daily round closed without rebuilding a ready table.",
  });
});
it("accepts a weekly table restored under a later daily cycle", async () => {
  // No reviewed weekly table exists today. Declare one in this fixture to exercise that cadence.
  vi.doMock("../lib/lineage", () => ({
    lineage: {
      nodes: [
        {
          kind: "relation",
          relation: "marts.mart_song_day",
          cadence: "weekly",
        },
      ],
    },
  }));
  try {
    stubFetch();
    const now = new Date();
    healthyNight(now);
    fixture.value.closes = [
      {
        ...dailyRound(now, 300),
        cycle_id: older,
        cadence: "weekly",
        close_no: "138",
      },
      { ...dailyRound(now, 10), close_no: "139" },
    ];
    fixture.value.ready = [
      stamp(daily, now.toISOString(), "marts.mart_song_day", "139"),
    ];
    expect(await clock()).toMatchObject({
      state: "answered",
      note: "2 rounds completed in the last day. The last weekly round rebuilt 1 ready table.",
    });
  } finally {
    vi.doUnmock("../lib/lineage");
  }
});
const daily = "00000000-0000-4000-8000-00000000000d";
function dailyRound(now: Date, minutesAgo: number) {
  return {
    cycle_id: daily,
    cadence: "daily",
    close_no: "9",
    closed_at: new Date(now.getTime() - minutesAgo * 60000).toISOString(),
    status: "closed",
  };
}
it("judges each rebuilding cadence on its own: a stamped hourly round never hides an unstamped daily one", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  // The daily round settled five hours ago and rebuilt nothing; the hourly round is fine.
  fixture.value.closes.push(dailyRound(now, 300));
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "The last daily round closed without rebuilding a ready table.",
  });
});
it("names every cadence whose last settled round rebuilt nothing", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.closes.push(dailyRound(now, 300));
  fixture.value.ready = [];
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "The last daily and hourly rounds closed without rebuilding a ready table.",
  });
});
it("answers when the last settled round of every rebuilding cadence is stamped", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.closes.push(dailyRound(now, 300));
  fixture.value.ready.push(
    stamp(
      daily,
      new Date(now.getTime() - 290 * 60000).toISOString(),
      "marts.mart_song_day",
      "9",
    ),
    stamp(
      daily,
      new Date(now.getTime() - 289 * 60000).toISOString(),
      "marts.mart_shazam_chart_daily",
      "9",
    ),
  );
  expect(await clock()).toMatchObject({
    state: "answered",
    note: "3 rounds completed in the last day. The last daily round rebuilt 2 ready tables. The last hourly round rebuilt 1 ready table.",
  });
});
it("reads a cadence with no round in the window, and a round inside the grace, plainly", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  const without = await clock();
  expect(without.state).toBe("answered");
  expect(without.note).toContain("No daily round closed in that day.");
  vi.resetModules();
  vi.doMock("../lib/lineage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/lineage")>();
  return { ...actual, lineage: { ...actual.lineage, nodes: [...actual.lineage.nodes,
    {kind: "relation", relation: "marts.mart_hourly_fixture", cadence: "hourly"}] } };
});
  healthyNight(now);
  // A daily round closed five minutes ago with no stamp yet is still rebuilding, not failed.
  fixture.value.closes.push(dailyRound(now, 5));
  expect(await clock()).toMatchObject({
    state: "answered",
    note: "3 rounds completed in the last day. The last daily round closed under 30 minutes ago; its rebuild is not checked yet. The last hourly round rebuilt 1 ready table.",
  });
});
it("does not answer on an unknown runner, a day without rounds, or unread stamps", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.runner = { state: "unknown" };
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "Runner state unknown.",
  });
  vi.resetModules();
  vi.doMock("../lib/lineage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/lineage")>();
  return { ...actual, lineage: { ...actual.lineage, nodes: [...actual.lineage.nodes,
    {kind: "relation", relation: "marts.mart_hourly_fixture", cadence: "hourly"}] } };
});
  healthyNight(now);
  fixture.value.closes = [];
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "No completed round in the last day.",
  });
  vi.resetModules();
  vi.doMock("../lib/lineage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/lineage")>();
  return { ...actual, lineage: { ...actual.lineage, nodes: [...actual.lineage.nodes,
    {kind: "relation", relation: "marts.mart_hourly_fixture", cadence: "hourly"}] } };
});
  healthyNight(now);
  fixture.value.ready_state = "unavailable";
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "Rebuilt tables not read.",
  });
});
it.each(["busy", "cached"])(
  "accepts %s stamps saved after the target close",
  async (state) => {
    stubFetch();
    healthyNight(new Date());
    fixture.value.ready_state = state;
    expect(await clock()).toMatchObject({ state: "answered" });
  },
);
it("refuses a close newer than saved stamps", async () => {
  stubFetch();
  const now = new Date();
  healthyNight(now);
  fixture.value.ready_state = "cached";
  fixture.value.ready_saved_at = new Date(
    now.getTime() - 60 * 60000,
  ).toISOString();
  expect(await clock()).toMatchObject({
    state: "no_answer",
    note: "Saved update times precede the last round. Open night details to retry.",
  });
});
it.each(["busy", "cached"])(
  "never answers from empty %s stamps, even inside the rebuild grace",
  async (state) => {
    stubFetch();
    const now = new Date();
    healthyNight(now);
    fixture.value.closes = [
      {
        cycle_id: cycle,
        cadence: "hourly",
        close_no: "3",
        closed_at: new Date(now.getTime() - 5 * 60000).toISOString(),
        status: "closed",
      },
    ];
    fixture.value.ready = [];
    fixture.value.ready_state = state;
    expect(await clock()).toMatchObject({
      state: "no_answer",
      note: "No saved update times. Open night details to retry.",
    });
  },
);
it("never surfaces a transport error's text, only a plain no answer", async () => {
  stubFetch();
  vi.doMock("../server/night", () => ({
    night: async () => {
      throw new Error("connect ECONNREFUSED mdp-control-api.internal:8080");
    },
  }));
  try {
    expect(await clock()).toMatchObject({
      state: "no_answer",
      note: "No answer.",
    });
  } finally {
    vi.doUnmock("../server/night");
  }
});
// The route is the last gate before the browser: a note that carries a private host, an
// address, an identifier, a volume, an image, a secret name or a unlisted database is
// refused whole, and the refusal names none of it.
const sentinels = [
  "mdp-functions.internal answered",
  "10.20.30.40 answered",
  "fdaa:0:1234:a7b:1f2:3c4d:5e6f:2",
  "machine e784e9d4b12345 answered",
  "vol_abc123def456 mounted",
  "pgdata mounted",
  "mb_data mounted",
  "registry.fly.io/mdp-showcase:deployment-01ABC",
  "ANTHROPIC_API_KEY set",
  "CLERK_SECRET_KEY set",
  "DATABASE_URL set",
  "sk_" + "live_" + "abcdefghijklmnop",
  "postgresql://user:secret@host/db",
];
it.each(sentinels)(
  "refuses to send a payload that carries %s",
  async (sentinel) => {
    vi.doMock("../server/stack-status", () => ({
      stackStatus: async () => ({
        checked_at: "2026-09-27T12:00:00Z",
        services: [
          {
            alias: "Readers",
            state: "answered",
            probe: "Readers HTTP health",
            checked_at: "2026-09-27T12:00:00Z",
            note: sentinel,
          },
        ],
      }),
    }));
    try {
      const { GET } = await import("../app/s/stack/route");
      const response = await GET();
      expect(response.status).toBe(503);
      expect(await response.text()).toBe("Status unavailable. Retry.");
    } finally {
      vi.doUnmock("../server/stack-status");
    }
  },
);
it("lets the three public console names and ordinary prose through", async () => {
  const { sensitiveStrings } = await import("../lib/stack");
  expect(
    sensitiveStrings([
      "597 MB of collected and cleaned tables; 110 MB of platform records.",
      "Metadata and the database stay private.",
      "24 rounds completed in the last day; the last hourly round rebuilt 3 ready tables.",
    ]),
  ).toEqual([]);
});
