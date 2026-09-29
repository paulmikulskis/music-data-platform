import "server-only";
import sharp from "sharp";
import { z } from "zod";
import { budget, type Cached } from "./read-budget";
type Cover = { type: string; bytes: string };
const coverCache = budget.cache<Cover | null>();
// MusicBrainz sends a null status for releases without one.
const recordingResponse = z.object({
  releases: z
    .array(z.object({ id: z.string(), status: z.string().nullish() }))
    .nullish(),
});
const embedResponse = z.object({ thumbnail_url: z.string().nullish() });
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function artHost(url: URL) {
  return (
    url.protocol === "https:" &&
    !url.username &&
    !url.password &&
    !url.port &&
    ([
      "musicbrainz.org",
      "coverartarchive.org",
      "archive.org",
      "open.spotify.com",
      "i.scdn.co",
      "itunes.apple.com",
    ].includes(url.hostname) ||
      url.hostname.endsWith(".archive.org") ||
      // Spotify's embed thumbnails moved from i.scdn.co to regional image CDN hosts.
      /^image-cdn-[a-z]{2}\.spotifycdn\.com$/.test(url.hostname) ||
      /^is\d{1,2}-ssl\.mzstatic\.com$/.test(url.hostname))
  );
}
// A 404 or 410: the provider has no such song or cover.
class MissingArt extends Error {}
async function safeFetch(url: URL, hops = 0): Promise<Response> {
  if (!artHost(url) || hops > 4)
    throw new Error("Artwork host unavailable. Open the song.");
  const response = await fetch(url, {
    headers: { "User-Agent": "MusicDataPlatform/1.0" },
    redirect: "manual",
    signal: AbortSignal.timeout(4500),
  });
  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get("location");
    if (!location) throw new Error("Artwork moved. Open the song.");
    return safeFetch(new URL(location, url), hops + 1);
  }
  if (response.status === 404 || response.status === 410)
    throw new MissingArt("Artwork unavailable. Open the song.");
  if (!response.ok) throw new Error("Artwork unavailable. Open the song.");
  return response;
}
// A provider's 404 is a known miss; any other failure stays an error, so it is tried again.
async function fetchOrMissing(url: URL) {
  try {
    return await safeFetch(url);
  } catch (error) {
    if (error instanceof MissingArt) return null;
    throw error;
  }
}
// A response body up to `cap` bytes, or null when it is missing or larger.
export async function readCapped(response: Response, cap: number) {
  if (Number(response.headers.get("content-length") ?? 0) > cap) return null;
  const reader = response.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > cap) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks);
}
// Only two artwork jobs may approach global admission; waiting keys hold no read slot.
let active = 0;
const waiting: (() => void)[] = [];
export async function scheduleArtwork<T>(work: () => Promise<T>): Promise<T> {
  if (active >= 2) {
    if (waiting.length >= 20)
      throw new Error("Artwork is waiting. Open the song.");
    await new Promise<void>((resolve, reject) => {
      const ready = () => {
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        waiting.splice(waiting.indexOf(ready), 1);
        reject(new Error("Artwork is waiting. Open the song."));
      }, 3000);
      waiting.push(ready);
    });
  } else active++;
  try {
    return await work();
  } finally {
    const next = waiting.shift();
    if (next) next();
    else active--;
  }
}
let mbTail = Promise.resolve();
async function recordingRelease(key: string) {
  // MusicBrainz asks clients to stay at one request per second, across all keys.
  const previous = mbTail;
  let unlock!: () => void;
  mbTail = new Promise<void>((resolve) => {
    unlock = resolve;
  });
  await previous;
  try {
    const response = await fetchOrMissing(
      new URL(
        `https://musicbrainz.org/ws/2/recording/${key}?inc=releases&fmt=json`,
      ),
    );
    // Some song keys are platform-wide ids, not MusicBrainz recordings.
    if (!response) return undefined;
    const json = recordingResponse.parse(await response.json());
    return (
      json.releases?.find((r) => r.status === "Official" && uuid.test(r.id))
        ?.id ?? json.releases?.find((r) => uuid.test(r.id))?.id
    );
  } finally {
    setTimeout(unlock, 1050);
  }
}
// Bounded memory: the oldest entry leaves first.
function forgetOldest(map: Map<string, unknown>) {
  const oldest = map.keys().next().value;
  if (map.size >= 2000 && oldest !== undefined) map.delete(oldest);
}
const appleLookup = z.object({
  results: z.array(
    z.object({
      trackId: z.number().optional(),
      artworkUrl100: z.string().nullish(),
    }),
  ),
});
// Apple's public lookup answers up to 50 ids a call. Ids wait here, in one batch, and calls stay
// at least three seconds apart (Apple asks for about 20 a minute).
const appleUrls = new Map<string, { url: string | null; until: number }>();
const appleQueue = new Map<
  string,
  { resolve: (url: string | null) => void; reject: (error: Error) => void }[]
>();
let appleTimer: ReturnType<typeof setTimeout> | null = null;
let appleLast = 0;
function scheduleApple() {
  if (appleTimer || !appleQueue.size) return;
  // Bounded both ways, so a clock that moves backwards never stalls the batch.
  appleTimer = setTimeout(
    () => void flushApple(),
    Math.min(3000, Math.max(120, appleLast + 3000 - Date.now())),
  );
}
async function flushApple() {
  appleTimer = null;
  const batch = [...appleQueue.entries()].slice(0, 50);
  for (const [id] of batch) appleQueue.delete(id);
  appleLast = Date.now();
  scheduleApple();
  try {
    const response = await safeFetch(
      new URL(
        `https://itunes.apple.com/lookup?id=${batch.map(([id]) => id).join(",")}`,
      ),
    );
    const found = appleLookup.parse(await response.json()).results;
    for (const [id, waiting] of batch) {
      const small = found.find(
        (row) => String(row.trackId) === id,
      )?.artworkUrl100;
      // Apple serves the same cover at any size named in its path.
      const url =
        small?.replace(/\/\d+x\d+bb\.(jpg|png)$/, "/600x600bb.jpg") ?? null;
      forgetOldest(appleUrls);
      appleUrls.set(id, {
        url,
        until: Date.now() + (url ? 86400000 : 600000),
      });
      for (const { resolve } of waiting) resolve(url);
    }
  } catch {
    for (const [, waiting] of batch)
      for (const { reject } of waiting)
        reject(new Error("Artwork unavailable. Open the song."));
  }
}
// The cover URL Apple lists for one song id, or null when Apple lists none.
export function appleArtworkUrl(id: string): Promise<string | null> {
  const known = appleUrls.get(id);
  if (known && known.until > Date.now()) return Promise.resolve(known.url);
  if (!appleQueue.has(id) && appleQueue.size >= 200)
    return Promise.reject(new Error("Artwork is waiting. Open the song."));
  return new Promise((resolve, reject) => {
    appleQueue.set(id, [...(appleQueue.get(id) ?? []), { resolve, reject }]);
    scheduleApple();
  });
}
const appleKey = /^apple:(\d{1,15})$/;
const spotifyKey = /^spotify:[A-Za-z0-9]{22}$/;
const otherSongKey = /^(?:isrc:[A-Z0-9]{12}|bandcamp:\d{1,20})$/i;
// The shapes a song key takes. Any other key has no cover and never reaches a warehouse read.
export function songKeyShape(key: string) {
  return (
    uuid.test(key) ||
    spotifyKey.test(key) ||
    appleKey.test(key) ||
    otherSongKey.test(key)
  );
}
export async function artwork(key: string): Promise<Cached<Cover | null>> {
  if (!uuid.test(key) && !spotifyKey.test(key) && !appleKey.test(key))
    return { value: null, savedAt: new Date().toISOString(), state: "live" };
  // Apple ids resolve before an artwork slot is taken, so a waiting batch holds no slot.
  const apple = appleKey.exec(key)?.[1];
  const appleUrl = apple ? await appleArtworkUrl(apple) : null;
  // An Apple miss lasts as long as the lookup remembers it, ten minutes, not a day.
  if (apple && !appleUrl)
    return { value: null, savedAt: new Date().toISOString(), state: "live" };
  return coverCache.read(
    `art:${key}`,
    "light",
    async () => {
      let url: URL;
      if (appleUrl) {
        url = new URL(appleUrl);
      } else if (uuid.test(key)) {
        const release = await recordingRelease(key);
        if (!release) return null;
        url = new URL(
          `https://coverartarchive.org/release/${release}/front-500`,
        );
      } else {
        const response = await fetchOrMissing(
          new URL(
            `https://open.spotify.com/oembed?url=${encodeURIComponent(`https://open.spotify.com/track/${key.slice(8)}`)}`,
          ),
        );
        if (!response) return null;
        const json = embedResponse.parse(await response.json());
        if (!json.thumbnail_url) return null;
        url = new URL(json.thumbnail_url);
      }
      const response = await fetchOrMissing(url);
      if (!response) return null;
      const type = response.headers.get("content-type")?.split(";")[0];
      if (!type || !["image/jpeg", "image/png", "image/webp"].includes(type))
        return null;
      const bytes = await readCapped(response, 500000);
      return bytes ? { type, bytes: bytes.toString("base64") } : null;
    },
    86400000,
    undefined,
    scheduleArtwork,
  );
}
// A copy's own artwork key: Spotify copies first, then Apple, at most three tries.
export function copyArtKeys(
  copies: { platform: string; platform_track_id: string }[],
) {
  const keys = copies.map((copy) =>
    copy.platform === "spotify"
      ? `spotify:${copy.platform_track_id}`
      : copy.platform === "apple"
        ? `apple:${copy.platform_track_id}`
        : "",
  );
  return [
    ...keys.filter((key) => spotifyKey.test(key)),
    ...keys.filter((key) => appleKey.test(key)),
  ].slice(0, 3);
}
const missing = new Map<string, number>();
// True when the song's last complete lookup found no cover, within ten minutes.
export function artMissing(key: string, now = Date.now()) {
  return (missing.get(key) ?? 0) > now;
}
// A song's cover. A Spotify or Apple key tries itself first. Any other key tries its Spotify and
// Apple copies before MusicBrainz, which allows one request a second. A miss is remembered only
// when every lookup answered, so a slow or refusing provider never hides a cover.
export async function coverFor(
  key: string,
  copies: () => Promise<{ platform: string; platform_track_id: string }[]>,
) {
  if (!songKeyShape(key)) return null;
  let settled = true;
  const attempt = async (candidate: string) => {
    try {
      return (await artwork(candidate)).value;
    } catch {
      settled = false;
      return null;
    }
  };
  const direct = spotifyKey.test(key) || appleKey.test(key);
  let cover = direct ? await attempt(key) : null;
  if (!cover) {
    const rows = await copies().catch(() => {
      settled = false;
      return [];
    });
    for (const candidate of copyArtKeys(rows).filter((k) => k !== key)) {
      cover = await attempt(candidate);
      if (cover) break;
    }
  }
  if (!cover && !direct) cover = await attempt(key);
  if (cover) {
    missing.delete(key);
    if (!colors.has(key)) {
      const stats = await sharp(Buffer.from(cover.bytes, "base64"))
        .resize(32, 32, { fit: "inside" })
        .stats()
        .catch(() => null);
      if (stats) {
        const { r, g, b } = stats.dominant;
        forgetOldest(colors);
        colors.set(key, `rgb(${r} ${g} ${b})`);
      }
    }
  } else if (settled) {
    forgetOldest(missing);
    missing.set(key, Date.now() + 600000);
  }
  return cover;
}

const colors = new Map<string, string>();
export function cachedArtColor(key: string) {
  return colors.get(key) ?? null;
}
