import { beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocked = vi.hoisted(() => ({ sources: vi.fn(), refresh: vi.fn() }));
vi.mock("../server/session", () => ({
  requireSession: async () => ({ person: { handle: "fixture" } }),
}));
vi.mock("../server/platform", () => ({ sources: mocked.sources }));
vi.mock("../server/heartbeat", () => ({
  heartbeat: { refresh: mocked.refresh },
}));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(path);
  },
}));
import { room } from "../server/room";
beforeEach(() => vi.clearAllMocks());
it.each([401, 403])(
  "reports a control refusal (%i) before a busy heartbeat masks it",
  async (status) => {
    mocked.sources.mockRejectedValue({ status });
    await expect(room()).rejects.toThrow("/sign-in?reason=key-refused");
    expect(mocked.refresh).not.toHaveBeenCalled();
  },
);
it("keeps ordinary outages on the room's cached path", async () => {
  mocked.sources.mockRejectedValue({ status: 503 });
  await expect(room()).resolves.toMatchObject({
    person: { handle: "fixture" },
  });
  expect(mocked.refresh).toHaveBeenCalled();
});
