import { localTime } from "./local-time";
import type { Provenance } from "../components/number";
import type { Locator } from "../server/models";
// The oldest time on screen. Only a live query may use its query time; a reviewed list without a
// stamped build has no read time, so it neither dates the footer nor hides it.
export function updated(
  provenances: (Provenance | undefined)[],
): string | null {
  const timed = provenances.filter(
    (p) => !(p?.provenance === "reviewed list" && !p.build?.stamped),
  );
  if (
    !timed.length ||
    timed.some(
      (p) =>
        !p ||
        (!p.build?.stamped &&
          p.provenance !== "live query" &&
          !p.inputs?.every((b) => b.stamped)),
    )
  )
    return null;
  const times = timed
    .flatMap((p) => [
      p?.observed_at,
      p?.build?.built_at,
      ...(p?.inputs?.map((b) => b.built_at) ?? []),
      p?.provenance === "live query" ? p.queried_at : null,
    ])
    .filter((t): t is string => !!t)
    .map(Date.parse)
    .filter(Number.isFinite);
  return times.length ? new Date(Math.min(...times)).toISOString() : null;
}
// A browser zone is required. Server rendering never substitutes its own clock zone.
export function clockLabel(iso: string, now = Date.now(), zone?: string) {
  return zone ? localTime(iso, true, new Date(now), zone) : "Time loading";
}
export function isToday(iso: string, now = Date.now()) {
  return (
    new Date(iso).toISOString().slice(0, 10) ===
    new Date(now).toISOString().slice(0, 10)
  );
}
// A UTC date such as 20 Sep.
export function dayLabel(iso: string) {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
export function proofLink(mark: Locator) {
  if (!mark.input_build.cycle_id || !mark.input_build.built_at) return null;
  return `/s/proof/cycle/${encodeURIComponent(mark.input_build.cycle_id)}?relation=${encodeURIComponent(mark.relation)}`;
}
export function modelState(
  learning: boolean,
  explanation?: {
    ranking_build: string;
    evidence_hash: string;
    reason_model: string;
  },
  ranking?: { ranking_build: string; evidence_hash: string },
) {
  if (!learning) return { state: "Held: rights", text: null };
  if (!explanation) return { state: "Pending", text: null };
  if (
    !ranking ||
    explanation.ranking_build !== ranking.ranking_build ||
    explanation.evidence_hash !== ranking.evidence_hash
  )
    return { state: "Stale", text: null };
  return { state: "Ready", text: explanation.reason_model };
}
export function lanePath(values: (number | null)[], width = 300, height = 60) {
  const valid = values.filter(
    (v): v is number => v !== null && Number.isFinite(v),
  );
  const max = Math.max(1, ...valid);
  let pen = false;
  let previousX = 0;
  let previousY = 0;
  return values
    .map((value, i) => {
      if (value === null || !Number.isFinite(value)) {
        pen = false;
        return "";
      }
      const x = (i * width) / Math.max(1, values.length - 1);
      const y = height - (value / max) * (height - 8);
      const point = `${x.toFixed(2)},${y.toFixed(2)}`;
      const mid = ((previousX + x) / 2).toFixed(2);
      const segment = pen
        ? `C${mid},${previousY.toFixed(2)} ${mid},${y.toFixed(2)} ${point}`
        : `M${point}`;
      pen = true;
      previousX = x;
      previousY = y;
      return segment;
    })
    .join(" ");
}

export function shortLabel(value: string, words: number) {
  const parts = value.trim().split(/\s+/);
  return parts.length > words ? parts.slice(0, words).join(" ") + "…" : value;
}

export function weekdayName(day: string) {
  return new Date(`${day.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short",
    timeZone: "UTC",
  });
}
// A UTC day with its weekday, such as Thu 24 Sept, in the date style the viewer screens share.
export function dayName(day: string) {
  return `${weekdayName(day)} ${dayLabel(`${day.slice(0, 10)}T00:00:00Z`)}`;
}
