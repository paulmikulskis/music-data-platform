import { afterEach, expect, it, vi } from "vitest";
import {
  scheduleRelationCounts,
  countInputs,
  buildStamp,
  relationBuildKey,
} from "../server/relation-counts";
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("polls on startup and after each capture without overlapping or waiting for a viewer", async () => {
  vi.useFakeTimers();
  let finish: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const capture = vi
    .fn<() => Promise<unknown>>()
    .mockReturnValueOnce(pending)
    .mockResolvedValue("captured");
  const stop = scheduleRelationCounts(capture);
  expect(capture).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(120000);
  expect(capture).toHaveBeenCalledTimes(1);
  finish();
  await vi.advanceTimersByTimeAsync(60001);
  expect(capture).toHaveBeenCalledTimes(2);
  stop();
  await vi.advanceTimersByTimeAsync(120000);
  expect(capture).toHaveBeenCalledTimes(2);
});
it("retries a missed capture and stops cleanly", async () => {
  vi.useFakeTimers();
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  const capture = vi
    .fn<() => Promise<unknown>>()
    .mockRejectedValueOnce(new Error("private row value"))
    .mockResolvedValue("captured");
  const stop = scheduleRelationCounts(capture);
  await vi.advanceTimersByTimeAsync(60001);
  expect(capture).toHaveBeenCalledTimes(2);
  expect(log).toHaveBeenCalledTimes(1);
  expect(log.mock.calls[0]?.[0]).toContain('"reason":"setup_failed"');
  expect(log.mock.calls[0]?.[0]).toContain("/ops");
  expect(log.mock.calls[0]?.[0]).not.toContain("private row value");
  stop();
});
it("reviews only global projections and gives every query a stable input identity", () => {
  expect(countInputs.length).toBeGreaterThan(0);
  expect(
    countInputs.every((row) =>
      /^(marts|intermediate|staging)\./.test(row.relation),
    ),
  ).toBe(true);
  expect(
    countInputs.every(
      (row) => row.input_hash.length === 64 && row.columns.length > 0,
    ),
  ).toBe(true);
  expect(
    countInputs.find((row) => row.relation === "marts.mart_chart_history")
      ?.filter,
  ).toBe("true");
});

it("keeps builds within the same millisecond distinct", () => {
  const stamp = {
    relation: "marts.mart_top_movers_current",
    cycle_id: "00000000-0000-4000-8000-000000000001",
    close_no: "1",
  };
  const first = buildStamp.parse({
    ...stamp,
    built_at: "2026-09-27T03:00:00.123001+00:00",
  });
  const second = buildStamp.parse({
    ...stamp,
    built_at: "2026-09-27T03:00:00.123002+00:00",
  });
  expect(new Date(first.built_at).getTime()).toBe(
    new Date(second.built_at).getTime(),
  );
  expect(relationBuildKey(first)).not.toBe(relationBuildKey(second));
});
