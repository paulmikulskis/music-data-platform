import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { it, expect, vi } from "vitest";
import { at, build, movers } from "./browser/fixtures";
vi.mock("server-only", () => ({}));
vi.mock("../server/room", () => ({
  room: async () => ({ csrf_token: "fixture" }),
  unavailable: () => null,
}));
vi.mock("../server/clients", () => ({
  warehouse: () => ({ unsafe: async () => [] }),
}));
vi.mock("../components/shell", () => ({
  Shell: ({
    provenances,
    cached,
  }: {
    provenances: { queried_at: string }[];
    cached: string;
  }) => (
    <div>
      {cached}
      <time>{provenances[0]?.queried_at}</time>
    </div>
  ),
}));
import RisingPage from "../app/_rising/page";
import { budget } from "../server/read-budget";
it("renders the original query time when the page refresh uses its last good payload", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(at));
  budget.invalidate("global:movers:");
  budget.setRunner("idle");
  const fetcher = vi.fn(async () =>
    Response.json({
      json: {
        rows: movers,
        next_cursor: null,
        build: build("mart_top_movers_current"),
        labels: {},
      },
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  vi.stubEnv("MDP_DATA_API_URL", "http://data.invalid");
  vi.stubEnv("MDP_SHOWCASE_READER_KEY", "fixture");
  try {
    const first = renderToStaticMarkup(
      await RisingPage({ searchParams: Promise.resolve({}) }),
    );
    expect(first).toContain(at);
    vi.advanceTimersByTime(31000);
    budget.setRunner("idle");
    fetcher.mockRejectedValue(new Error("offline"));
    const saved = renderToStaticMarkup(
      await RisingPage({ searchParams: Promise.resolve({}) }),
    );
    expect(saved).toContain("cached");
    expect(saved).toContain(at);
    expect(saved).not.toContain(new Date().toISOString());
    expect(fetcher).toHaveBeenCalledTimes(4);
  } finally {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  }
});
