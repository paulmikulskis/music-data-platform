import "server-only";
import { hash } from "@mdp/showcase-auth";
import type { Bucket } from "../lib/request-bucket";

const windows = new Map<string, { second: number; count: number }>();
// The fixture browser walk measures Home (1 document, 2 data, 2 art), viewer
// open (1 RSC navigation, 1 data), peek (1 data), and Stack (1 document, 3 data).
// Treat all four actions as one second: 3 documents + 7 data per tab. Three tabs
// per session, two sessions per office IP, and 2x headroom give 18/36 documents
// and 42/84 data. Art keeps its existing 40/session ceiling, with 80/shared IP.
// See ops/evidence/h-rate/README.md to repeat the measurement. These arrival
// ceilings do not replace read-budget.ts or the warehouse connection limit.
export const limits = {
  // Keep authentication at its original shared 10 requests per IP per second.
  auth: { session: 10, ip: 10 },
  document: { session: 18, ip: 36 },
  data: { session: 42, ip: 84 },
  art: { session: 40, ip: 80 },
} as const;

// One app instance. Bound memory as well as request rate; excess distinct callers fail closed.
export function admitted(
  ip: string,
  session?: string,
  now = Date.now(),
  bucket: Bucket = "document",
): boolean {
  const second = Math.floor(now / 1000);
  const callers = [
    { key: `${bucket}:ip:${ip}`, limit: limits[bucket].ip },
    ...(session
      ? [
          {
            key: `${bucket}:session:${hash(session)}`,
            limit: limits[bucket].session,
          },
        ]
      : []),
  ];
  // Check both before spending either allowance. A refused session must not use
  // the shared IP's remaining allowance or prevent the other person reading.
  let missing = 0;
  for (const { key, limit } of callers) {
    const current = windows.get(key);
    if (!current) missing++;
    if (current?.second === second && current.count >= limit) return false;
  }
  if (windows.size + missing > 10000) {
    // Two new keys can exceed capacity when the map has only 9,999 entries.
    // Reclaim first, then recount: an expired caller key may also be removed.
    for (const [key, value] of windows) {
      if (value.second < second) windows.delete(key);
    }
    missing = callers.filter(({ key }) => !windows.has(key)).length;
  }
  if (windows.size + missing > 10000) return false;
  for (const { key } of callers) {
    const current = windows.get(key);
    windows.set(key, {
      second,
      count: current?.second === second ? current.count + 1 : 1,
    });
  }
  return true;
}
