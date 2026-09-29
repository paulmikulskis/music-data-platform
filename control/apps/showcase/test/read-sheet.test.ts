import { afterEach, expect, it, vi } from "vitest";
import { readSheet } from "../components/lineage/read";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("waits for Retry-After and returns a repeated refusal to the calm retry state", async () => {
  vi.useFakeTimers();
  const refused = () =>
    Response.json(
      { error_class: "showcase_rate_limited" },
      { status: 429, headers: { "Retry-After": "2" } },
    );
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(refused())
    .mockResolvedValueOnce(refused());
  vi.stubGlobal("fetch", fetcher);
  const result = readSheet("/s/trace", new AbortController().signal);
  await vi.advanceTimersByTimeAsync(1999);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect((await result).status).toBe(429);
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it("stops the retry when the sheet closes", async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(
      new Response(null, { status: 429, headers: { "Retry-After": "1" } }),
    );
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const result = readSheet("/s/peek", controller.signal);
  const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(1);
  controller.abort();
  await rejected;
  await vi.advanceTimersByTimeAsync(2000);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
