import type { Build } from "../components/number";

// Viewer screens a proof page may return to. Anything else (the operator console, including a proof's
// own /engine hop, another host, a dot segment, plain or encoded) falls back to the caller's default.
const viewerScreens =
  /^\/(?:songs|sources|stack|team|search|today|live|holdings|draft|library)(?:[/?#]|$)|^\/s\/(?:song|proof)\//;
const base = "http://viewer.invalid";
export function viewerPath(value: string | null | undefined) {
  if (!value || value.length > 800) return null;
  if (!value.startsWith("/") || value.startsWith("//") || /[\\\s]/.test(value))
    return null;
  const path = value.split(/[?#]/, 1)[0] ?? "";
  if (
    /%(?:2e|2f|5c|25)/i.test(path) ||
    path.split("/").some((part) => part === "." || part === "..")
  )
    return null;
  // The path the browser would open must be the path that was checked.
  const url = new URL(value, base);
  if (url.origin !== base || url.pathname !== path) return null;
  return (url.pathname === "/" || viewerScreens.test(url.pathname)) &&
    !/\/engine\/?$/.test(url.pathname)
    ? value
    : null;
}
// The screen a proof link opens from, so the proof page's Back returns there. Proof links render
// inside sheets, after the page has loaded, so the browser location is always known.
export function here() {
  return typeof window === "undefined"
    ? null
    : viewerPath(window.location.pathname + window.location.search);
}
function withFrom(path: string, from: string | null) {
  return from
    ? `${path}${path.includes("?") ? "&" : "?"}from=${encodeURIComponent(from)}`
    : path;
}
// The plain proof summary for a number: what was read, when and from where. A song narrows it to
// that song's own entries; sources name what to describe when no song applies.
export function cycleProofLink(
  build?: Build,
  fallback = "/songs?view=rising",
  context: { song?: string; sources?: string[] } = {},
) {
  if (!build?.stamped || !build.cycle_id) return fallback;
  const query = new URLSearchParams({ relation: build.relation });
  if (context.song) query.set("song", context.song);
  if (context.sources?.length)
    query.set("sources", context.sources.slice(0, 6).join(","));
  return withFrom(
    `/s/proof/cycle/${encodeURIComponent(build.cycle_id)}?${query}`,
    here(),
  );
}
// A source's plain summary: what it reads, how much it tracks and when it last read.
export function sourceProofLink(source: string) {
  return withFrom(`/s/proof/source/${encodeURIComponent(source)}`, here());
}
// The proof summary for one fact on a song: a movement fact or a lane on one day.
export function markProofLink(
  song: string,
  mark: number,
  query: Record<string, string>,
) {
  return withFrom(
    `/s/proof/${encodeURIComponent(song)}/${mark}?${new URLSearchParams(query)}`,
    here(),
  );
}
