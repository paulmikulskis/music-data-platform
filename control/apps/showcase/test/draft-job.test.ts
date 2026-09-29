import { expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { scheduleDraft } from "../server/draft-job";
it("retries a missed tick and stops cleanly while a tick is running", async () => {
  vi.useFakeTimers();
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  const tick = vi
    .fn<() => Promise<void>>()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue(undefined);
  const stop = scheduleDraft(tick);
  await vi.advanceTimersByTimeAsync(60000);
  expect(tick).toHaveBeenCalledTimes(2);
  expect(log).toHaveBeenCalledWith(
    expect.stringContaining("Finish weekly picks"),
  );
  stop();
  await vi.advanceTimersByTimeAsync(60000);
  expect(tick).toHaveBeenCalledTimes(2);
  vi.useRealTimers();
  log.mockRestore();
});
