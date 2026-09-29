import { budget } from "../server/read-budget";
import { sessionForToken } from "../server/session";
vi.mock("../server/clients", () => ({ controlStore: () => ({}) }));
vi.mock("@mdp/showcase-auth", () => ({
  validateSession: vi.fn(async () => ({ handle: "fixture" })),
}));
import { it, expect, vi } from "vitest";
vi.mock("server-only", () => ({}));
import {
  appleArtworkUrl,
  artHost,
  artMissing,
  artwork,
  copyArtKeys,
  coverFor,
  songKeyShape,
} from "../server/art";
it("admits only HTTPS artwork hosts, including validated redirect destinations", () => {
  for (const url of [
    "https://coverartarchive.org/release/id/front-500",
    "https://ia800.us.archive.org/image.jpg",
    "https://i.scdn.co/image/id",
  ])
    expect(artHost(new URL(url))).toBe(true);
  for (const url of [
    "http://coverartarchive.org/a",
    "https://coverartarchive.org.evil.invalid/a",
    "https://127.0.0.1/a",
    "https://archive.org:444/a",
    "https://user:secret@archive.org/a",
  ])
    expect(artHost(new URL(url))).toBe(false);
});

it("cold artwork waits outside admission while sessions revalidate", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ releases: [] })),
  );
  try {
    const art = Promise.allSettled(
      Array.from({ length: 12 }, (_, i) =>
        artwork(`00000000-0000-4000-8000-${String(i).padStart(12, "0")}`),
      ),
    );
    const checks = await Promise.all(
      Array.from({ length: 8 }, () => sessionForToken("fixture")),
    );
    expect(checks.every((value) => value?.handle === "fixture")).toBe(true);
    expect(await budget.run("essential", async () => "heartbeat")).toBe(
      "heartbeat",
    );
    await vi.advanceTimersByTimeAsync(15000);
    await art;
  } finally {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});

it.each([
  {
    key: "00000000-0000-4000-8000-999999999999",
    json: { releases: [{ id: 42, status: "Official" }] },
  },
  { key: "spotify:aaaaaaaaaaaaaaaaaaaaaa", json: { thumbnail_url: 42 } },
])(
  "rejects malformed provider JSON before fetching an image",
  async ({ key, json }) => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => Response.json(json));
    vi.stubGlobal("fetch", fetcher);
    try {
      await expect(artwork(key)).rejects.toThrow();
      expect(fetcher).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1100);
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  },
);

it("uses a MusicBrainz release whose status is null", async () => {
  vi.useFakeTimers();
  const release = "11111111-1111-4111-8111-111111111111";
  const fetcher = vi.fn(async (url: URL | RequestInfo) =>
    String(url).startsWith("https://musicbrainz.org/")
      ? Response.json({ releases: [{ id: release, status: null }] })
      : new Response(new Uint8Array([1, 2, 3]), {
          headers: { "content-type": "image/png" },
        }),
  );
  vi.stubGlobal("fetch", fetcher);
  try {
    const result = await artwork("00000000-0000-4000-8000-888888888888");
    expect(result.value).toEqual({ type: "image/png", bytes: "AQID" });
    expect(String(fetcher.mock.calls[1]?.[0])).toBe(
      `https://coverartarchive.org/release/${release}/front-500`,
    );
    await vi.advanceTimersByTimeAsync(1100);
  } finally {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});

it("admits Apple's lookup and cover hosts", () => {
  expect(artHost(new URL("https://itunes.apple.com/lookup?id=1"))).toBe(true);
  expect(
    artHost(new URL("https://is1-ssl.mzstatic.com/image/thumb/a/600x600bb.jpg")),
  ).toBe(true);
  expect(artHost(new URL("https://mzstatic.com.evil.invalid/a"))).toBe(false);
  expect(
    artHost(new URL("https://image-cdn-fa.spotifycdn.com/image/ab67616d")),
  ).toBe(true);
  expect(artHost(new URL("https://spotifycdn.com.evil.invalid/a"))).toBe(false);
  expect(artHost(new URL("http://is1-ssl.mzstatic.com/a"))).toBe(false);
});

it("looks Apple ids up in one batch and serves the 600px cover", async () => {
  vi.useFakeTimers();
  const lookups: string[] = [];
  const fetcher = vi.fn(async (url: URL | RequestInfo) => {
    const address = String(url);
    if (address.startsWith("https://itunes.apple.com/lookup")) {
      lookups.push(address);
      return Response.json({
        resultCount: 1,
        results: [
          {
            trackId: 9000000602,
            artworkUrl100:
              "https://is1-ssl.mzstatic.com/image/thumb/Music211/cover.jpg/100x100bb.jpg",
          },
        ],
      });
    }
    return new Response(new Uint8Array([7, 8, 9]), {
      headers: { "content-type": "image/jpeg" },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  try {
    const found = artwork("apple:9000000602");
    const missing = artwork("apple:1111111111");
    await vi.advanceTimersByTimeAsync(200);
    expect((await found).value).toEqual({ type: "image/jpeg", bytes: "BwgJ" });
    expect((await missing).value).toBeNull();
    expect(lookups).toEqual([
      "https://itunes.apple.com/lookup?id=9000000602,1111111111",
    ]);
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toBe(
      "https://is1-ssl.mzstatic.com/image/thumb/Music211/cover.jpg/600x600bb.jpg",
    );
    // A known id answers from memory, without another lookup.
    expect((await appleArtworkUrl("9000000602"))).toContain("600x600bb.jpg");
    expect(lookups).toHaveLength(1);
    // A miss is asked again after ten minutes, not a day.
    await vi.advanceTimersByTimeAsync(601000);
    const again = artwork("apple:1111111111");
    await vi.advanceTimersByTimeAsync(3200);
    expect((await again).value).toBeNull();
    expect(lookups).toEqual([
      "https://itunes.apple.com/lookup?id=9000000602,1111111111",
      "https://itunes.apple.com/lookup?id=1111111111",
    ]);
  } finally {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});

it("tries a song's copies before MusicBrainz and remembers only settled misses", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: URL | RequestInfo) => {
    const address = String(url);
    if (address.startsWith("https://musicbrainz.org/"))
      return new Response("Not found", { status: 404 });
    if (address.startsWith("https://itunes.apple.com/lookup"))
      return Response.json({
        results: [
          {
            trackId: 222,
            artworkUrl100: "https://is2-ssl.mzstatic.com/x/100x100bb.jpg",
          },
        ],
      });
    return new Response(new Uint8Array([1]), {
      headers: { "content-type": "image/png" },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  try {
    const song = "00000000-0000-4000-8000-777777777777";
    const cover = coverFor(song, async () => [
      { platform: "apple", platform_track_id: "222" },
    ]);
    await vi.advanceTimersByTimeAsync(4000);
    expect(await cover).toEqual({ type: "image/png", bytes: "AQ==" });
    expect(artMissing(song)).toBe(false);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).startsWith("https://musicbrainz.org/"),
      ),
    ).toBe(false);
    const none = "00000000-0000-4000-8000-666666666666";
    const miss = coverFor(none, async () => []);
    await vi.advanceTimersByTimeAsync(1100);
    expect(await miss).toBeNull();
    expect(artMissing(none)).toBe(true);
    // A failed copy read is not a known miss.
    const unsure = "00000000-0000-4000-8000-555555555555";
    const failed = coverFor(unsure, async () => {
      throw new Error("Collection is active or unknown. Retry shortly.");
    });
    await vi.advanceTimersByTimeAsync(1100);
    expect(await failed).toBeNull();
    expect(artMissing(unsure)).toBe(false);
    expect(copyArtKeys([
      { platform: "apple", platform_track_id: "1" },
      { platform: "spotify", platform_track_id: "a".repeat(22) },
      { platform: "deezer", platform_track_id: "2" },
    ])).toEqual([`spotify:${"a".repeat(22)}`, "apple:1"]);
  } finally {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});

it("refuses keys that are not song keys before any warehouse read", async () => {
  for (const key of [
    "00000000-0000-4000-8000-000000000101",
    "spotify:FixtureTrack0000000602",
    "apple:9000000601",
    "isrc:AUXXX1100005",
    "bandcamp:900000601",
  ])
    expect(songKeyShape(key), key).toBe(true);
  const copies = vi.fn(async () => []);
  for (const key of ["random-key", "spotify%3AFixtureTrack0000000602", "isrc:x", "apple:"]) {
    expect(songKeyShape(key), key).toBe(false);
    expect(await coverFor(key, copies)).toBeNull();
    expect(artMissing(key)).toBe(false);
  }
  expect(copies).not.toHaveBeenCalled();
});
