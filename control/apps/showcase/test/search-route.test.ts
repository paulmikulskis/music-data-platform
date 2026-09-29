import { beforeEach, expect, it, vi } from "vitest";
import type { Session } from "@mdp/showcase-auth";
import { martBuild } from "@mdp/data-sdk";
import type { SearchRow } from "../lib/search";
import { build } from "./browser/fixtures";
vi.mock("server-only", () => ({}));
const mocked = vi.hoisted(() => ({
  session: vi.fn(),
  search: vi.fn(),
  sources: vi.fn(),
  offers: vi.fn(),
}));
vi.mock("../server/session", () => ({ session: mocked.session }));
vi.mock("../server/reads", () => ({ librarySearch: mocked.search }));
vi.mock("../server/platform", () => ({ sources: mocked.sources }));
vi.mock("../server/call-offers", async (original) => ({
  ...(await original<typeof import("../server/call-offers")>()),
  callOffers: mocked.offers,
}));
import { GET } from "../app/library/search/route";
const current: Session = {
  id_hash: "fixture",
  handle: "fixture",
  csrf_token: "fixture",
  person: {
    handle: "fixture",
    display_name: "Test viewer",
    email: "fixture@example.invalid",
    api_key_id: "00000000-0000-4000-8000-000000000001",
    admin_key: "fixture",
  },
};
const row: SearchRow = {
  object_key: "song:apple:test",
  kind: "song",
  display_text: "Test song",
  context: { key: "apple:test" },
  aliases: [],
  last_seen: "2026-09-25 00:00:00",
  source_keys: ["sz_chart"],
  learning_eligible: false,
  resale_permitted: false,
};
beforeEach(() => {
  vi.clearAllMocks();
  mocked.session.mockResolvedValue(current);
  mocked.search.mockResolvedValue({
    rows: [row],
    build: martBuild.parse(build("mart_search_index")),
  });
  mocked.sources.mockResolvedValue({
    value: {
      sources: [{ source_key: "test_source", display_name: "Test source" }],
    },
  });
  mocked.offers.mockResolvedValue({});
});
it("requires a session before reading search or sources", async () => {
  mocked.session.mockResolvedValue(null);
  const response = await GET(
    new Request("http://localhost/library/search?q=test"),
  );
  expect(response.status).toBe(401);
  expect(mocked.search).not.toHaveBeenCalled();
  expect(mocked.sources).not.toHaveBeenCalled();
});
it("refuses a short or oversized query without a database read", async () => {
  for (const q of ["a", " a ", "x".repeat(101)]) {
    expect(
      (
        await GET(
          new Request(
            `http://localhost/library/search?q=${encodeURIComponent(q)}`,
          ),
        )
      ).status,
    ).toBe(400);
  }
  expect(mocked.search).not.toHaveBeenCalled();
});
it("uses control sources and forbids response caching", async () => {
  const request = new Request("http://localhost/library/search?q=test");
  const response = await GET(request);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(mocked.search).toHaveBeenCalledWith("test", request.signal);
  expect(await response.json()).toMatchObject({
    rows: [row, { kind: "source", display_text: "Test source" }],
  });
});
it("keeps content visible when calls or sources fail and names the missing actions", async () => {
  mocked.sources.mockRejectedValue(new Error("Unavailable"));
  mocked.offers.mockRejectedValue(new Error("Unavailable"));
  const response = await GET(
    new Request("http://localhost/library/search?q=test"),
  );
  expect(await response.json()).toMatchObject({
    rows: [row],
    sources_unavailable: true,
    calls_unavailable: true,
  });
});
it("returns a next step without leaking a database error", async () => {
  mocked.search.mockRejectedValue(new Error("private query details"));
  const response = await GET(
    new Request("http://localhost/library/search?q=test"),
  );
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ next_step: "Retry, or open /ops." });
});
