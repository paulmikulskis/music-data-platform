import { z } from "zod";
import { martBuild } from "@mdp/data-sdk";
import { locator } from "../server/models";

export const CALLS_PER_WEEK = 5;
export const CALL_UNDO_MS = 10 * 60 * 1000;
export const CALL_CARD_TTL_MS = 10 * 60 * 1000;
export const CALL_WEEK_START = 5;
export const CALL_TIME_ZONE = "America/New_York";
export const CALL_WEEK_DAY = "Friday";

export const callPlace = z.object({
  country: z.string().nullable(),
  city: z.string().nullable(),
});
export const callAnchor = z.object({
  platform: z.string(),
  platform_track_id: z.string(),
  song_key: z.string(),
});
const common = {
  v: z.literal(1),
  draft_week: z.iso.date().optional(),
  song_key: z.string().min(1),
  anchors: z.array(callAnchor).min(1).max(20),
  anchors_truncated: z.boolean(),
  places_shown: z.array(callPlace).max(60).nullable(),
  builds: z.array(martBuild).min(1),
  source_keys: z.array(z.string()),
  facts_day: z.iso.date(),
  close_no: z.string().regex(/^\d+$/),
  idempotency_key: z.uuid(),
  handle: z.string(),
  exp: z.number().int(),
};
const music = {
  list: z.string(),
  window_days: z.number().nullable(),
};
export const callSnapshot = z.discriminatedUnion("card", [
  z.object({
    ...common,
    card: z.literal("search"),
    display_text: z.string(),
    subtitle: z.string().nullable(),
    places_shown: z.null(),
  }),
  z.object({
    ...common,
    ...music,
    card: z.literal("arrival"),
    entered_lists: z.string(),
    discovery: z.array(z.string()),
  }),
  z.object({
    ...common,
    ...music,
    card: z.literal("early"),
    component: z.string(),
    value: z.number().nullable(),
    playlist_count: z.string(),
    age_basis: z.string().nullable(),
  }),
  z.object({
    ...common,
    ...music,
    card: z.literal("mover"),
    parts: z.array(
      z.object({
        component: z.string(),
        value: z.number(),
        window_days: z.number().nullable(),
      }),
    ),
    playlist_count: z.string().nullable(),
    // The facts the card showed, in order, each with its own window. The card-level
    // window is window_days.
    shown: z.array(
      z.object({
        component: z.string(),
        text: z.string(),
        window_days: z.number().nullable(),
      }),
    ),
  }),
  z.object({
    ...common,
    card: z.literal("song"),
    window_days: z.literal(28),
    day: z.string(),
    editorial_adds: z.number().nullable(),
    shazam_cities: z.string().nullable(),
    stream_rate: z.number().nullable(),
  }),
]);
export type CallSnapshot = z.infer<typeof callSnapshot>;
export const signedCard = z.object({
  body: z.string().max(64000),
  signature: z.string(),
  idempotency_key: z.uuid(),
});
export type SignedCard = z.infer<typeof signedCard>;
export const savedCall = z.object({
  id: z.uuid(),
  author: z.string(),
  song_key: z.string(),
  week_start: z.string(),
  submitted_at: z.string(),
  undone_at: z.string().nullable(),
  hidden_at: z.string().nullable(),
  facts: callSnapshot,
});
export type SavedCall = z.infer<typeof savedCall>;
export const callObservation = callPlace.extend({
  chart_date: z.string(),
  chart: z.string(),
  chart_type: z.string().nullable(),
  position: z.number(),
  // True when the earliest observation came from a copy joined only by its provisional song group.
  matched_copy: z.boolean(),
  locator,
});
export type CallObservation = z.infer<typeof callObservation>;
export const callOffer = z.object({
  signed: signedCard,
  existing: savedCall.nullable(),
  other: z.boolean(),
  otherInitial: z.string().optional(),
  left: z.number().int(),
  csrf: z.string(),
});
export type CallOffer = {
  signed: SignedCard;
  existing: SavedCall | null;
  other: boolean;
  otherInitial?: string;
  left: number;
  csrf: string;
};
export function callWeek(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: CALL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  const date = new Date(
    `${part("year")}-${part("month")}-${part("day")}T00:00:00Z`,
  );
  date.setUTCDate(
    date.getUTCDate() - ((date.getUTCDay() - CALL_WEEK_START + 7) % 7),
  );
  return date.toISOString().slice(0, 10);
}
export function earlierWeek(week: string) {
  const date = new Date(`${week}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 7);
  return date.toISOString().slice(0, 10);
}
