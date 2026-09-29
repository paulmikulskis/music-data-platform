import "server-only";
import { z } from "zod";
import { budget } from "./read-budget";
import { readCapped } from "./art";
import { warehouse } from "./clients";
// Artist photos come only from Wikimedia Commons through the artist's Wikidata item, and only with
// an author, a license and a Commons page. Anything less gets no photo; the card shows a monogram.
export const qidPattern = /^Q[1-9][0-9]{0,11}$/;
export type Credit = { author: string; license: string; license_url: string | null; page: string };
export type Photo = { type: string; bytes: Buffer; credit: Credit };
const claims = z.object({
  claims: z
    .object({
      P18: z
        .array(z.object({ mainsnak: z.object({ datavalue: z.object({ value: z.string() }).optional() }) }))
        .optional(),
    })
    .optional(),
});
const meta = z.object({ value: z.string() }).optional();
const imageInfo = z.object({
  query: z.object({
    pages: z.record(
      z.string(),
      z.object({
        imageinfo: z
          .array(
            z.object({
              thumburl: z.string(),
              descriptionurl: z.string(),
              extmetadata: z
                .object({ Artist: meta, LicenseShortName: meta, LicenseUrl: meta })
                .optional(),
            }),
          )
          .optional(),
      }),
    ),
  }),
});
export function wikimediaHost(url: URL) {
  return (
    url.protocol === "https:" &&
    !url.username &&
    !url.password &&
    !url.port &&
    ["www.wikidata.org", "commons.wikimedia.org", "upload.wikimedia.org", "thumb.wikimedia.org"].includes(url.hostname)
  );
}
// A link shown beside a photo: https only, and the file page must be on Commons.
export function safeLink(value: string | undefined, commonsOnly = false) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    if (commonsOnly && url.hostname !== "commons.wikimedia.org") return null;
    return url.toString();
  } catch {
    return null;
  }
}
// Every request of one photo job shares one deadline; each hop also has its own.
async function wikimedia(url: URL, deadline: AbortSignal, hops = 0): Promise<Response> {
  if (!wikimediaHost(url) || hops > 3) throw new Error("Photo host unavailable. Open the artist.");
  const response = await fetch(url, {
    headers: { "User-Agent": "MusicDataPlatform/1.0" },
    redirect: "manual",
    signal: AbortSignal.any([deadline, AbortSignal.timeout(4500)]),
  });
  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get("location");
    if (!location) throw new Error("Photo moved. Open the artist.");
    return wikimedia(new URL(location, url), deadline, hops + 1);
  }
  if (!response.ok) throw new Error("Photo unavailable. Open the artist.");
  return response;
}
async function json(response: Response, cap: number): Promise<unknown> {
  const body = await readCapped(response, cap);
  if (!body) throw new Error("Photo details too large. Open the artist.");
  return JSON.parse(body.toString("utf8"));
}
// Commons credit lines are HTML. Keep the words, drop the markup.
export function plainCredit(html: string | undefined) {
  const text = (html ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&[a-z]+;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 60 ? `${text.slice(0, 57).trimEnd()}…` : text;
}
export async function photoFile(qid: string, deadline = AbortSignal.timeout(8000)) {
  const url = new URL("https://www.wikidata.org/w/api.php");
  for (const [key, value] of Object.entries({ action: "wbgetclaims", entity: qid, property: "P18", format: "json" }))
    url.searchParams.set(key, value);
  return claims.parse(await json(await wikimedia(url, deadline), 200000)).claims?.P18?.[0]?.mainsnak.datavalue?.value ?? null;
}
// The thumbnail and its credit, or null when Commons gives no license or no file page.
export async function photoInfo(file: string, deadline = AbortSignal.timeout(8000)) {
  const url = new URL("https://commons.wikimedia.org/w/api.php");
  for (const [key, value] of Object.entries({ action: "query", format: "json", prop: "imageinfo", iiprop: "url|extmetadata", iiurlwidth: "240", iiextmetadatafilter: "Artist|LicenseShortName|LicenseUrl", titles: `File:${file}` }))
    url.searchParams.set(key, value);
  const pages = imageInfo.parse(await json(await wikimedia(url, deadline), 200000)).query.pages;
  const info = Object.values(pages)[0]?.imageinfo?.[0];
  const license = plainCredit(info?.extmetadata?.LicenseShortName?.value);
  const page = safeLink(info?.descriptionurl, true);
  if (!info || !license || !page) return null;
  const credit: Credit = {
    author: plainCredit(info.extmetadata?.Artist?.value) || "Author unknown",
    license,
    license_url: safeLink(info.extmetadata?.LicenseUrl?.value),
    page,
  };
  return { thumb: new URL(info.thumburl), credit };
}
// Photos keep their own small cache, apart from the room payloads, bounded by count and bytes.
// A missing photo is remembered for ten minutes, a found one for a day.
type Entry = { value: Photo | null; saved: number };
export class PhotoCache {
  private entries = new Map<string, Entry>();
  private flights = new Map<string, Promise<Photo | null>>();
  private bytes = 0;
  constructor(private maxCount = 96, private maxBytes = 12_000_000, private missTtl = 600000, private hitTtl = 86400000) {}
  get size() {
    return this.entries.size;
  }
  get totalBytes() {
    return this.bytes;
  }
  private fresh(entry: Entry) {
    return Date.now() - entry.saved < (entry.value ? this.hitTtl : this.missTtl);
  }
  private drop(key: string) {
    const entry = this.entries.get(key);
    if (!entry) return;
    this.bytes -= entry.value?.bytes.byteLength ?? 0;
    this.entries.delete(key);
  }
  private keep(key: string, value: Photo | null) {
    this.drop(key);
    this.entries.set(key, { value, saved: Date.now() });
    this.bytes += value?.bytes.byteLength ?? 0;
    for (const oldest of this.entries.keys()) {
      if (this.entries.size <= this.maxCount && this.bytes <= this.maxBytes) break;
      this.drop(oldest);
    }
  }
  read(key: string, work: () => Promise<Photo | null>) {
    const entry = this.entries.get(key);
    if (entry && this.fresh(entry)) {
      this.entries.delete(key);
      this.entries.set(key, entry);
      return Promise.resolve(entry.value);
    }
    const flight = this.flights.get(key);
    if (flight) return flight;
    const promise = work()
      .then((value) => {
        this.keep(key, value);
        return value;
      })
      .finally(() => this.flights.delete(key));
    this.flights.set(key, promise);
    return promise;
  }
}
export const photos = (globalThis.showcasePhotos ??= new PhotoCache());
declare global {
  var showcasePhotos: PhotoCache | undefined;
}
// One photo job at a time, apart from cover art; at most ten wait, for three seconds each.
let busy = false;
const waiting: (() => void)[] = [];
async function oneAtATime<T>(work: () => Promise<T>): Promise<T> {
  if (busy) {
    if (waiting.length >= 10) throw new Error("Photos are waiting. Open the artist.");
    await new Promise<void>((resolve, reject) => {
      const ready = () => {
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        waiting.splice(waiting.indexOf(ready), 1);
        reject(new Error("Photos are waiting. Open the artist."));
      }, 3000);
      waiting.push(ready);
    });
  } else busy = true;
  try {
    return await work();
  } finally {
    const next = waiting.shift();
    if (next) next();
    else busy = false;
  }
}
// Only an item the warehouse links to exactly one artist gets a photo.
export const linkedQidSql =
  "SELECT 1 AS linked FROM explore_intermediate.int_artist_identity WHERE wikidata_qid = $1 AND candidate_count = 1 LIMIT 1";
export async function linkedQid(qid: string) {
  const rows = await budget.run("light", () => warehouse().unsafe(linkedQidSql, [qid]));
  return rows.length > 0;
}
export function artistPhoto(qid: string): Promise<Photo | null> {
  if (!qidPattern.test(qid)) return Promise.resolve(null);
  return photos.read(qid, async () => {
    if (!(await linkedQid(qid))) return null;
    return oneAtATime(async () => {
      const deadline = AbortSignal.timeout(8000);
      const file = await photoFile(qid, deadline);
      if (!file) return null;
      const info = await photoInfo(file, deadline);
      if (!info) return null;
      const response = await wikimedia(info.thumb, deadline);
      const type = response.headers.get("content-type")?.split(";")[0];
      if (!type || !["image/jpeg", "image/png", "image/webp"].includes(type)) return null;
      const bytes = await readCapped(response, 500000);
      return bytes ? { type, bytes, credit: info.credit } : null;
    });
  });
}
