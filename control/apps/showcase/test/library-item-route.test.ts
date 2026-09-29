import { beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocked = vi.hoisted(() => ({
  session: vi.fn(),
  playlist: vi.fn(),
  chart: vi.fn(),
  artist: vi.fn(),
  source: vi.fn(),
}));
vi.mock("../server/session", () => ({ session: mocked.session }));
vi.mock("../server/library", () => ({
  playlistFacts: mocked.playlist,
  chartFacts: mocked.chart,
  artistFacts: mocked.artist,
  sourceFacts: mocked.source,
}));
import { GET } from "../app/library/item/route";
import { libraryItem } from "../lib/library";
const item = {
  kind: "playlist",
  title: "New Music Friday",
  subtitle: "Spotify · by Spotify",
  facts: [{ value: "4,636,710", label: "followers" }],
  songs_label: null,
  songs: [],
  empty: "No songs have entered it since reading began.",
  places: [],
  read: "Read from Spotify on 26 Sept.",
  link: null,
  engine: null,
  first_seen: null,
};
const get = (query: string) =>
  GET(new Request(`http://localhost/library/item?${query}`));
beforeEach(() => {
  vi.clearAllMocks();
  mocked.session.mockResolvedValue({ person: { handle: "fixture" } });
  mocked.playlist.mockResolvedValue(item);
});
it("requires a session before any read", async () => {
  mocked.session.mockResolvedValue(null);
  expect((await get("kind=playlist&key=spotify%3Alist")).status).toBe(401);
  expect(mocked.playlist).not.toHaveBeenCalled();
});
it("refuses unknown kinds and malformed keys without a read", async () => {
  for (const query of [
    "kind=account&key=fixture%3Ax",
    "kind=playlist&key=nocolon",
    "kind=chart&key=",
    "kind=playlist",
  ])
    expect((await get(query)).status).toBe(400);
  expect(mocked.playlist).not.toHaveBeenCalled();
});
it("answers a viewer view that never points at the Explorer", async () => {
  const response = await get(
    "kind=playlist&key=spotify%3A37i9dQZF1DX4JAvHpjipBk",
  );
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  const body = libraryItem.parse(await response.json());
  expect(mocked.playlist).toHaveBeenCalledWith(
    "spotify:37i9dQZF1DX4JAvHpjipBk",
  );
  expect(JSON.stringify(body)).not.toContain("/explorer");
});
it("says when an item left the Library and hides database errors", async () => {
  mocked.chart.mockResolvedValue(null);
  expect(
    (await get("kind=chart&key=shazam%3Atop-50%3Ajapan%3Atokyo")).status,
  ).toBe(404);
  mocked.chart.mockRejectedValue(new Error("private query details"));
  const failed = await get("kind=chart&key=shazam%3Atop-50%3Ajapan%3Atokyo");
  expect(failed.status).toBe(503);
  expect(await failed.json()).toEqual({ next_step: "Retry, or open /ops." });
});
it("reads sources through the person's own key", async () => {
  mocked.source.mockResolvedValue({
    ...item,
    kind: "source",
    engine: "/functions/sz_chart",
  });
  await get("kind=source&key=source%3Asz_chart");
  expect(mocked.source).toHaveBeenCalledWith(
    { handle: "fixture" },
    "source:sz_chart",
  );
});
