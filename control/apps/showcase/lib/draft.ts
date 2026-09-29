import { z } from "zod";
import { callSnapshot, CALL_WEEK_START, CALL_TIME_ZONE } from "./calls";

export const ruleCondition = z.discriminatedUnion("field", [
  z
    .object({
      field: z.literal("artist_stage"),
      value: z.enum(["emerging", "established"]),
    })
    .strict(),
  z
    .object({
      field: z.literal("age_class"),
      value: z.enum(["new", "catalog"]),
    })
    .strict(),
  z
    .object({
      field: z.literal("discovery_entries_at_least"),
      value: z.number().int().min(1).max(9),
    })
    .strict(),
  z
    .object({
      field: z.literal("markets_at_least"),
      value: z.number().int().min(1).max(20),
    })
    .strict(),
  z
    .object({
      field: z.literal("lists_at_least"),
      value: z.number().int().min(1).max(10),
    })
    .strict(),
  z
    .object({
      field: z.literal("reach_tier_at_most"),
      value: z.number().int().min(1).max(4),
    })
    .strict(),
]);
export const ruleValues = z
  .object({
    conditions: z.array(ruleCondition).min(1).max(4),
    limit_per_draft: z.number().int().min(1).max(5),
  })
  .strict();
export const draftRule = ruleValues.extend({
  id: z.string().regex(/^[a-z0-9-]+$/),
  title: z.string(),
  backed_by: z.string().nullable(),
});
export type DraftRule = z.infer<typeof draftRule>;
export const exampleRules: DraftRule[] = [
  {
    id: "new-discovery",
    title: "new artist, 2+ Discovery",
    conditions: [
      { field: "artist_stage", value: "emerging" },
      { field: "discovery_entries_at_least", value: 2 },
    ],
    limit_per_draft: 2,
    backed_by: null,
  },
  {
    id: "new-lists",
    title: "new song, 2+ lists",
    conditions: [
      { field: "age_class", value: "new" },
      { field: "lists_at_least", value: 2 },
    ],
    limit_per_draft: 2,
    backed_by: null,
  },
];
export const draftCandidate = z.object({
  song_key: z.string(),
  artist_stage: z.string().nullable(),
  age_class: z.string().nullable(),
  discovery_entries: z.number().int().nonnegative(),
  market_count: z.number().int().nonnegative(),
  entered_lists: z.number().int().nonnegative(),
  list_reach_tier: z.number().int().min(1).max(4),
  movement_list: z.string(),
  rank: z.number().int().positive(),
  snapshot: callSnapshot,
});
export type DraftCandidate = z.infer<typeof draftCandidate>;
export const draft = z.object({
  week_start: z.iso.date(),
  opens_at: z.string(),
  closes_at: z.string(),
  candidates: z.array(draftCandidate),
  rules: z.array(draftRule),
  closed_at: z.string().nullable(),
  closed_by: z.string().nullable(),
  close_key: z.string().nullable(),
});
export type Draft = z.infer<typeof draft>;
export const draftWeek = z.iso
  .date()
  .refine(
    (day) => new Date(`${day}T12:00:00Z`).getUTCDay() === CALL_WEEK_START,
  );

// The Friday call-week key stays fixed; warehouse observations belong to Saturday UTC.
export function draftReadDay(week: string) {
  const day = new Date(`${draftWeek.parse(week)}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}
export const draftWaiting =
  "Opens after the scheduled weekly read.";
export const ruleListOrder =
  "New songs, established artists, older songs, then songs without a list; rank breaks ties.";

// Compare local calendar time so the Saturday deadline follows daylight saving time.
export function draftWindowPassed(week: string, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: CALL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const part = (kind: Intl.DateTimeFormatPartTypes) =>
    parts.find((value) => value.type === kind)!.value;
  const day = `${part("year")}-${part("month")}-${part("day")}`;
  const readDay = draftReadDay(week);
  return day > readDay || (day === readDay && Number(part("hour")) >= 18);
}

export function nextDraftWaiting(week: string) {
  const next = new Date(`${draftReadDay(week)}T12:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 7);
  const date = next.toLocaleDateString("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
  });
  return `Weekly picks open after the scheduled read on ${date}.`;
}
