import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../server/room", () => ({
  room: async () => ({ csrf_token: "fixture" }),
}));
vi.mock("../components/shell", () => ({
  Shell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("../server/draft-reads", () => ({
  ensureDraft: vi.fn(async () => null),
}));
import Page from "../app/draft/page";
import { budget } from "../server/read-budget";
import { beforeEach } from "vitest";
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-26T12:00:00Z"));
  budget.setRunner("idle");
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
import { ensureDraft } from "../server/draft-reads";

it("explains when the tray opens and offers calls", async () => {
  const html = renderToStaticMarkup(
    await Page({ searchParams: Promise.resolve({ board: "1" }) }),
  );
  expect(html).toContain(
    "Opens after the scheduled weekly read.",
  );
  expect(html).toContain('href="/songs?view=picks">Open picks</a>');
});
it("keeps a failed read distinct from a read that has not landed", async () => {
  vi.mocked(ensureDraft).mockRejectedValueOnce(new Error("offline"));
  const html = renderToStaticMarkup(
    await Page({ searchParams: Promise.resolve({ board: "1" }) }),
  );
  expect(html).toContain(
    "The selection could not load. Open picks while it is unavailable.",
  );
  expect(html).toContain('href="/songs?view=picks">Open picks</a>');
});

it("names an active read without offering a reload", async () => {
  budget.setRunner("busy");
  const html = renderToStaticMarkup(
    await Page({ searchParams: Promise.resolve({ board: "1" }) }),
  );
  expect(html).toContain("The overnight read is still running.");
  expect(html).toContain('href="/songs?view=picks">Open picks</a>');
  expect(html).not.toContain("Retry");
});

it.each(["unknown", "idle", "busy"] as const)(
  "does not claim collection is active for unknown or stale %s state",
  async (state) => {
    const now = Date.now();
    budget.setRunner(state);
    if (state !== "unknown") vi.spyOn(Date, "now").mockReturnValue(now + 26000);
    const html = renderToStaticMarkup(
      await Page({ searchParams: Promise.resolve({ board: "1" }) }),
    );
    expect(html).toContain(
      "The overnight read&#x27;s status is unavailable. Open picks.",
    );
    expect(html).not.toContain("still running");
    expect(html.match(/<a /g)).toHaveLength(1);
    expect(html).toContain('href="/songs?view=picks">Open picks</a>');
  },
);

it("shows the next Saturday after a missed first opening", async () => {
  vi.setSystemTime(new Date("2026-09-26T22:00:00Z"));
  const html = renderToStaticMarkup(
    await Page({ searchParams: Promise.resolve({ board: "1" }) }),
  );
  expect(html).toContain(
    "Weekly picks open after the scheduled read on Oct 3.",
  );
  expect(html).toContain('href="/songs?view=picks">Open picks</a>');
  expect(html).not.toContain("See results");
});
