import { beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({
  mart: vi.fn(),
  playlistReceipt: vi.fn(),
  chain: vi.fn(),
}));
vi.mock("../server/reads", () => ({
  mart: mocks.mart,
  playlistReceipt: mocks.playlistReceipt,
}));
vi.mock("../server/clients", () => ({
  controlClient: () => ({ lineage: { chain: mocks.chain } }),
}));
import { resolveProof } from "../server/proof";
import { budget } from "../server/read-budget";
const person = {
  handle: "fixture",
  display_name: "Test viewer",
  email: "fixture@example.invalid",
  admin_key: "server-only",
  api_key_id: "key",
};
const build = {
  relation: "marts.mart_track_daily_streams",
  scope: "global",
  cycle_id: "00000000-0000-4000-8000-000000000041",
  close_no: "41",
  built_at: "2026-09-25T06:12:00Z",
};
const mark = {
  component: "stream_rate_gain",
  relation: "mart_track_daily_streams",
  row_key: { day: "2026-09-25" },
  window: {},
  input_build: build,
};
const run = "00000000-0000-4000-8000-000000000009";
beforeEach(() => {
  vi.clearAllMocks();
  budget.invalidate("proof:");
  budget.setRunner("idle");
});
it("opens an exact run only while the row still has the captured build", async () => {
  mocks.mart.mockResolvedValue({
    rows: [{ _run_ids: JSON.stringify([run]) }],
    build: { ...build, stamped: true },
  });
  expect(await resolveProof(person, mark)).toEqual({
    level: "row",
    href: `/runs/${run}`,
  });
  expect(mocks.chain).not.toHaveBeenCalled();
});
it("follows the captured cycle after a rebuild and skips unrelated receipts", async () => {
  mocks.mart.mockResolvedValue({
    rows: [{ _run_ids: JSON.stringify([run]) }],
    build: { ...build, close_no: "42", stamped: true },
  });
  mocks.chain
    .mockResolvedValueOnce({
      dumps: [{ id: "other", run_id: "other" }],
      receipts: [{ dump_id: "other", target_table: "raw.unrelated" }],
      next: { dumps: "cursor", receipts: "cursor", requests: null },
    })
    .mockResolvedValueOnce({
      dumps: [{ id: "wanted", run_id: run }],
      receipts: [{ dump_id: "wanted", target_table: "raw.track_streams" }],
      next: { dumps: null, receipts: null, requests: null },
    });
  expect(await resolveProof(person, mark)).toEqual({
    level: "partial",
    href: `/runs/${run}`,
  });
  expect(mocks.chain.mock.calls[0][0].cycle_id).toBe(build.cycle_id);
  expect(mocks.chain).toHaveBeenCalledTimes(2);
});
it("does not turn an unstamped input into current-cycle proof", async () => {
  expect(
    await resolveProof(person, {
      ...mark,
      input_build: { ...build, built_at: null },
    }),
  ).toEqual({ level: "unavailable", href: "/ops" });
  expect(mocks.mart).not.toHaveBeenCalled();
});
it("resolves a matching playlist snapshot through its reviewed projection", async () => {
  mocks.mart.mockResolvedValue({
    rows: [
      { snapshot_id: "snapshot", platform: "spotify", playlist_id: "list" },
    ],
    build: { ...build, stamped: true },
  });
  mocks.playlistReceipt.mockResolvedValue({
    value: { rows: [{ run_id: run }] },
  });
  expect(
    await resolveProof(person, {
      ...mark,
      relation: "mart_playlist_events",
      component: "playlist_adds",
    }),
  ).toEqual({ level: "row", href: `/runs/${run}` });
  expect(mocks.playlistReceipt).toHaveBeenCalledWith(
    "snapshot",
    "spotify",
    "list",
  );
});
it("keeps a called place at cycle proof and falls back after a rebuild or missing locator", async () => {
  const { signCallPlace, verifyCallPlace } =
    await import("../server/call-proof");
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  const captured = {
    ...mark,
    component: "shazam_places",
    relation: "marts.mart_shazam_chart_daily",
    row_key: {
      chart: "shazam:city:de:berlin",
      chart_date: "2026-09-25",
      position: 3,
    },
  };
  const id = "00000000-0000-4000-8000-000000000011";
  const token = signCallPlace(id, person.handle, captured);
  const stored = verifyCallPlace(token, id, person.handle);
  expect(stored).toEqual(captured);
  if (!stored)
    throw new Error("Expected signed place. Inspect the test fixture.");
  mocks.mart.mockResolvedValue({
    rows: [{}],
    build: { ...build, stamped: true },
  });
  mocks.chain.mockResolvedValue({
    dumps: [{ id: "chart", run_id: run }],
    receipts: [{ dump_id: "chart", target_table: "raw.shazam_chart_entries" }],
    next: null,
  });
  expect((await resolveProof(person, stored)).level).toBe("cycle");
  budget.invalidate("proof:");
  mocks.mart.mockResolvedValue({
    rows: [{}],
    build: { ...build, close_no: "99", stamped: true },
  });
  expect((await resolveProof(person, stored)).level).toBe("partial");
  expect(verifyCallPlace(token, id, "other")).toBeNull();
  expect(verifyCallPlace(null, id, person.handle)).toBeNull();
  mocks.chain.mockResolvedValue({ dumps: [], receipts: [], next: null });
  expect((await resolveProof(person, stored)).level).toBe("unavailable");
  vi.unstubAllEnvs();
});

it("resolves a captured cycle without guessing a playlist source", async () => {
  const { resolveCycleProof } = await import("../server/proof");
  mocks.chain.mockResolvedValue({
    dumps: [{ id: "chart", run_id: run }],
    receipts: [{ dump_id: "chart", target_table: "raw.shazam_chart_entries" }],
    next: null,
  });
  expect(
    await resolveCycleProof(person, build.cycle_id, "mart_arrivals_current"),
  ).toEqual({ level: "cycle", href: `/runs/${run}` });
  expect(mocks.chain.mock.calls[0][0].cycle_id).toBe(build.cycle_id);
  expect(mocks.mart).not.toHaveBeenCalled();
});
it("uses the captured relation before a component label", async () => {
  mocks.mart.mockResolvedValue({
    rows: [],
    build: { ...build, stamped: true },
  });
  mocks.chain.mockResolvedValue({
    dumps: [{ id: "chart", run_id: run }],
    receipts: [{ dump_id: "chart", target_table: "raw.shazam_chart_entries" }],
    next: null,
  });
  expect(
    await resolveProof(person, {
      ...mark,
      component: "renamed",
      relation: "mart_shazam_chart_daily",
    }),
  ).toEqual({ level: "cycle", href: `/runs/${run}` });
});
