import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const state = vi.hoisted(() => ({
  close: "41",
  countClose: "41",
  reads: 0,
  fail: false,
}));
vi.mock("../server/read-budget", async () => {
  const { ReadBudget } = await vi.importActual<
    typeof import("../server/read-budget")
  >("../server/read-budget");
  const budget = new ReadBudget();
  budget.setRunner("idle");
  return { budget };
});
const relation = "marts.mart_playlist_profile";
const cycle = "00000000-0000-4000-8000-000000000041";
const at = "2026-09-27T06:12:00.000000Z";
const key = (close: string) => JSON.stringify([relation, cycle, close, at]);
vi.mock("../server/lineage-counts", () => ({
  lineageCounts: async () => [
    {
      relation,
      row_count: "19",
      captured_at: at,
      build_key: key(state.countClose),
    },
  ],
}));
vi.mock("../server/clients", () => ({
  warehouse: () => ({
    begin: async (_options: string, work: (tx: unknown) => Promise<unknown>) =>
      work(
        Object.assign(
          async () => [
            {
              stamp: {
                relation,
                cycle_id: cycle,
                close_no: state.close,
                built_at: at,
              },
            },
          ],
          {
            unsafe: async (sql: string) => {
              if (sql.startsWith("LOCK")) return [];
              state.reads++;
              if (state.fail) throw new Error("Read unavailable. Retry.");
              return [
                {
                  title: "Night list",
                  platform: "spotify",
                  followers: "1200",
                  observed_at: at,
                  description: "personal-text-sentinel",
                  owner_id: "creator-sentinel",
                  commenter: "commenter-sentinel",
                  tenant: "tenant-sentinel",
                },
              ];
            },
          },
        ),
      ),
  }),
}));
beforeEach(() => {
  vi.resetModules();
  state.close = "41";
  state.countClose = "41";
  state.reads = 0;
  state.fail = false;
});
it("caches only reviewed columns and includes only a matching denominator", async () => {
  const { peek } = await import("../server/peek");
  const first = await peek({ relation });
  const cached = await peek({ relation });
  expect(cached).toEqual(first);
  expect(state.reads).toBe(1);
  expect(first.total).toBe("19");
  expect(JSON.stringify(first)).not.toMatch(
    /sentinel|description|owner_id|commenter|tenant/,
  );
  expect(first.rows[0]?.title).toBe("Night list");
});
it("hides stale and filtered totals, and refuses a different requested build", async () => {
  const { peek } = await import("../server/peek");
  state.countClose = "40";
  expect((await peek({ relation })).total).toBeNull();
  state.countClose = "41";
  expect(
    (await peek({ relation, filters: { playlist_id: "one" } })).total,
  ).toBeNull();
  expect((await peek({ relation }, key("40"))).state).toBe("changed");
  expect(state.reads).toBe(2);
});
it("does not read any unreviewed relation", async () => {
  const { peek } = await import("../server/peek");
  expect(
    (await peek({ relation: "tenant_sentinel_marts.private" })).state,
  ).toBe("unavailable");
  expect(state.reads).toBe(0);
});

afterEach(() => vi.useRealTimers());
it("dates cached fallbacks while collection is busy and when refresh fails", async () => {
  vi.useFakeTimers();
  const { peek } = await import("../server/peek");
  const { budget } = await import("../server/read-budget");
  const first = await peek({ relation });
  expect(first.cache_state).toBe("live");
  expect(first.saved_at).toBeTruthy();
  budget.setRunner("busy");
  const busy = await peek({ relation });
  expect(busy.cache_state).toBe("busy");
  expect(busy.saved_at).toBe(first.saved_at);
  expect(state.reads).toBe(1);
  vi.setSystemTime(Date.now() + 11000);
  budget.setRunner("idle");
  state.fail = true;
  const fallback = await peek({ relation });
  expect(fallback.cache_state).toBe("cached");
  expect(fallback.saved_at).toBe(first.saved_at);
  expect(fallback.rows).toEqual(first.rows);
});
