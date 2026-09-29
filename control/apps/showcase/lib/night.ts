import { z } from "zod";
import { platformNight } from "@mdp/contracts/platform";
import { sourceWording } from "@mdp/contracts/source-wording";
import type { Build } from "../components/number";
export const nightResponse = z.object({
  state: z.enum(["live", "cached", "busy"]),
  savedAt: z.iso.datetime(),
  value: platformNight.extend({
    ready_saved_at: z.iso.datetime().nullable(),
    ready_state: z.enum(["live", "cached", "busy", "unavailable"]),
    ready: z.array(
      z.object({
        relation: z.string(),
        cycle_id: z.string(),
        close_no: z.string().nullable(),
        built_at: z.iso.datetime(),
        build_key: z.string(),
      }),
    ),
  }),
});
export type Night = z.infer<typeof nightResponse>;
// Convert wall-clock dates independently. A night crossing a clock change is not 15 UTC hours.
export function viewerNight(now: Date, zone: string) {
  const parts = (date: Date) => {
    const values = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const get = (key: string) =>
      Number(values.find((p) => p.type === key)?.value);
    return {
      year: get("year"),
      month: get("month"),
      day: get("day"),
      hour: get("hour"),
      minute: get("minute"),
      second: get("second"),
    };
  };
  const local = parts(now);
  const day = Date.UTC(local.year, local.month - 1, local.day);
  const startDay = day - (local.hour < 18 ? 86400000 : 0);
  const wall = (date: number, hour: number) => {
    const target = date + hour * 3600000;
    let stamp = target;
    for (let i = 0; i < 3; i++) {
      const p = parts(new Date(stamp));
      stamp +=
        target -
        Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
    }
    return stamp;
  };
  const since = wall(startDay, 18);
  const end = wall(startDay + 86400000, 9);
  // Exactly at 18:00 the client waits for a nonempty interval.
  return {
    since: new Date(since).toISOString(),
    until: new Date(Math.min(now.getTime(), end)).toISOString(),
    title: now.getTime() < end ? "Tonight so far" : "Last night",
  };
}
export type Moment = {
  id: string;
  at: string;
  lane: "Reads" | "Matching" | "Ready";
  name: string;
  status: string;
  trigger: string;
  href: string;
  coverage: string | null;
  source: string | null;
  alert: { summary: string; next_step: string } | null;
  ready?: Night["value"]["ready"][number];
};
const states: Record<string, string> = {
  succeeded: "Read complete",
  partial: "Only part delivered",
  failed: "Reading failed",
  superseded: "Replaced by a later attempt",
  queued: "Queued",
  running: "Reading now",
};
export function nightMoments(data: Night["value"]): Moment[] {
  const moments: Moment[] = data.runs.flatMap((run) => {
    const attempts = run.attempts.length
      ? run.attempts
      : [
          {
            attempt_no: 0,
            started_at: run.admitted_at,
            status: run.status,
            trigger: "unknown",
            targets: null,
          },
        ];
    return attempts.map((attempt) => {
      const target = attempt.targets;
      const alert = data.alerts.find(
        (a) =>
          a.run_id === run.run_id &&
          (a.attempt_no === null || a.attempt_no === attempt.attempt_no),
      );
      const unit =
        target?.unit === "playlist"
          ? "playlists"
          : target?.unit === "account"
            ? "accounts"
            : target?.unit === "chart"
              ? "charts"
              : "targets";
      return {
        id: `${run.run_id}:${attempt.attempt_no}`,
        at: attempt.started_at,
        lane:
          run.kind === "bronze" ||
          run.source_key?.match(/playlist|chart|billboard/)
            ? ("Reads" as const)
            : ("Matching" as const),
        name: run.source_key
          ? (sourceWording[run.source_key]?.name ?? "Source reading")
          : "Song matching",
        status: states[attempt.status] ?? "State not measured yet",
        trigger:
          attempt.trigger === "scheduled"
            ? "Started on its own"
            : attempt.trigger === "manual"
              ? "Started by hand"
              : attempt.trigger === "retry/restore"
                ? "Repeated reading"
                : "Start not recorded",
        href: `/s/night/details?run=${run.run_id}&attempt=${attempt.attempt_no}`,
        source: run.source_key,
        coverage:
          target?.succeeded == null
            ? null
            : `${target.succeeded}${target.eligible == null ? "" : ` of ${target.eligible}`} ${unit} read`,
        alert: alert
          ? { summary: alert.summary, next_step: alert.next_step }
          : null,
      };
    });
  });
  for (const ready of data.ready) {
    // Only the served mover table labels the songs shown. Other tables remain separate ready events.
    moments.push({
      id: ready.build_key,
      at: ready.built_at,
      lane: "Ready",
      name:
        ready.relation === "marts.mart_top_movers_current"
          ? "Songs ready"
          : "Numbers ready",
      status: "Updated",
      trigger: "",
      href:
        ready.relation === "marts.mart_top_movers_current"
          ? "/songs?view=rising"
          : "/sources",
      coverage: null,
      source: null,
      alert: null,
      ready,
    });
  }
  return moments.sort((a, b) => a.at.localeCompare(b.at));
}
export function nightReaders(data: Night["value"]): Moment[] {
  const readers = new Map<string, Moment>();
  for (const moment of nightMoments(data)) {
    if (moment.lane !== "Reads" || !moment.source) continue;
    // One line per reader, ordered by its latest attempt. The timeline keeps every attempt.
    readers.delete(moment.source);
    const count = data.coverage.find(
      (coverage) => coverage.source_key === moment.source,
    );
    const unit =
      count?.unit === "playlist"
        ? "playlists"
        : count?.unit === "account"
          ? "accounts"
          : count?.unit === "chart"
            ? "charts"
            : "targets";
    readers.set(moment.source, {
      ...moment,
      // The API counts distinct successful targets across runs. Per-run totals overlap.
      coverage:
        count?.succeeded == null
          ? "Read count not measured"
          : `${count.succeeded} ${unit} read`,
    });
  }
  return [...readers.values()];
}
function stampedTime(at: string) {
  const fraction = /\.(\d+)/.exec(at)?.[1] ?? "";
  return (
    new Date(at).toISOString().slice(0, 19) + "." + fraction.padEnd(6, "0")
  );
}
export function matchesReady(moment: Moment, build?: Build) {
  return (
    !!moment.ready &&
    !!build?.stamped &&
    !!build.built_at &&
    moment.ready.relation === build.relation &&
    moment.ready.cycle_id === build.cycle_id &&
    moment.ready.close_no === build.close_no &&
    stampedTime(moment.ready.built_at) === stampedTime(build.built_at)
  );
}
// Leave room for a full tap target at both ends. Nearby times share one target.
export const nightTickSize = 44;
export function momentClusters(
  moments: Moment[],
  window: { since: string; until: string },
  width: number,
) {
  const start = Date.parse(window.since);
  const span = Math.max(1, Date.parse(window.until) - start);
  const usable = Math.max(0, width - nightTickSize);
  const groups: { moments: Moment[]; x: number }[] = [];
  for (const moment of moments.toSorted(
    (a, b) => Date.parse(a.at) - Date.parse(b.at),
  )) {
    const fraction = Math.max(
      0,
      Math.min(1, (Date.parse(moment.at) - start) / span),
    );
    const x = Math.min(width / 2, nightTickSize / 2) + fraction * usable;
    const last = groups.at(-1);
    if (
      last &&
      (x - last.x < nightTickSize ||
        Date.parse(moment.at) - Date.parse(last.moments[0].at) < 180000)
    )
      last.moments.push(moment);
    else groups.push({ moments: [moment], x });
  }
  return groups;
}
