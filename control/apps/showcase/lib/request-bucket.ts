export type Bucket = "auth" | "document" | "data" | "art";

// /s/song and /s/proof are pages. Only the named JSON handlers use the data bucket.
const jsonRoutes = new Set([
  "/s/apps",
  "/s/stack",
  "/s/night",
  "/s/night/details",
  "/s/trace",
  "/s/peek",
  "/s/team-question",
  "/library/search",
  "/library/item",
  "/events",
  "/health/status",
]);
export function bucketFor(pathname: string, method = "GET"): Bucket {
  // The sign-in page and redemption handler share one ceiling for every method.
  // Headers can select a refusal's format, but never an admission bucket.
  if (/^\/(?:sign-in|sign-out|auth)(?:\/|$)/.test(pathname)) return "auth";
  if (
    jsonRoutes.has(pathname) ||
    pathname === "/rpc" ||
    pathname.startsWith("/rpc/") ||
    pathname === "/api" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/s/operations/") ||
    /^\/artist-photo\/[^/]+\/credit$/.test(pathname) ||
    (method !== "GET" && method !== "HEAD")
  )
    return "data";
  if (pathname.startsWith("/art/") || pathname.startsWith("/artist-photo/"))
    return "art";
  return "document";
}
