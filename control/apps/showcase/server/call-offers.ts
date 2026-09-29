import { CALL_CARD_TTL_MS, CALLS_PER_WEEK } from "../lib/calls";
import "server-only";
import { randomUUID } from "node:crypto";
import { person, type Session } from "@mdp/showcase-auth";
import type { Build } from "../components/number";
import type {
  ArrivalSummary,
  EarlySignal,
  Mover,
  SongDay,
  SongPlace,
} from "./models";
import {
  callSnapshot,
  callWeek,
  type CallOffer,
  type CallSnapshot,
} from "../lib/calls";
import { sourceKeys } from "../lib/source-keys";
import { musicFacts } from "../lib/music-facts";
import { signCall } from "./call-token";
import { callAnchors } from "./call-reads";
import { readCalls } from "./call-store";
import { budget } from "./read-budget";
import { controlStore } from "./clients";
import { consistentCallBuilds } from "./call-builds";
import { arrivals, movers } from "./reads";

type ProjectionOf<T> = T extends CallSnapshot
  ? Omit<
      T,
      | "v"
      | "anchors"
      | "anchors_truncated"
      | "idempotency_key"
      | "handle"
      | "exp"
    >
  : never;
type Projection = ProjectionOf<CallSnapshot>;
export function searchProjection(
  row: {
    context: { key: string; subtitle?: string | null };
    display_text: string;
    last_seen: string;
    source_keys: string[];
  },
  build: Build,
): Extract<Projection, { card: "search" }> {
  return {
    card: "search",
    song_key: row.context.key,
    display_text: row.display_text,
    subtitle: row.context.subtitle ?? null,
    places_shown: null,
    builds: [build],
    source_keys: row.source_keys,
    facts_day: row.last_seen.slice(0, 10),
    close_no: build.close_no ?? "0",
  };
}
export function arrivalProjection(
  summary: ArrivalSummary,
  build: Build,
): Extract<Projection, { card: "arrival" }> | null {
  const lead = summary.lead;
  if (!lead?.day) return null;
  const discovery = lead.discovery.slice(0, 3);
  return {
    card: "arrival" as const,
    song_key: lead.song_key,
    list: lead.movement_list,
    window_days: summary.window_days,
    entered_lists: lead.entered_lists ?? "0",
    discovery,
    places_shown: discovery.length
      ? discovery.map((country) => ({ country, city: null }))
      : null,
    builds: [build],
    source_keys: summary.source_keys,
    facts_day: lead.day,
    close_no: build.close_no ?? "0",
  };
}
export function earlyProjection(
  row: EarlySignal,
  build: Build,
): Extract<Projection, { card: "early" }> | null {
  if (!row.day) return null;
  return {
    card: "early" as const,
    song_key: row.song_key,
    list: row.movement_list,
    window_days: row.window_days,
    component: row.component ?? "",
    value: row.value,
    playlist_count: row.playlist_count,
    age_basis: row.age_basis,
    places_shown: null,
    builds: [build],
    source_keys: sourceKeys(row.source_keys),
    facts_day: row.day,
    close_no: build.close_no ?? "0",
  };
}
export function moverProjection(
  row: Mover,
  build: Build,
): Extract<Projection, { card: "mover" }> {
  return {
    card: "mover" as const,
    song_key: row.song_key,
    list: row.movement_list,
    window_days: row.window_days,
    parts: row.score_parts.map(({ component, value, window_days }) => ({
      component,
      value,
      window_days: window_days ?? null,
    })),
    playlist_count: row.playlist_count,
    // The same list the card renders, so a part's own window survives the call.
    shown: musicFacts(row).map(({ component, text }) => ({
      component,
      text,
      window_days:
        row.score_parts.find((part) => part.component === component)
          ?.window_days ?? row.window_days,
    })),
    places_shown: null,
    builds: [build],
    source_keys: sourceKeys(row.source_keys),
    facts_day: row.day,
    close_no: build.close_no ?? "0",
  };
}
export function songProjection(
  row: SongDay,
  places: SongPlace[] | null,
  builds: Build[],
  factsDay = row.day,
): Extract<Projection, { card: "song" }> {
  return {
    card: "song" as const,
    song_key: row.song_key ?? "",
    window_days: 28 as const,
    day: row.day,
    editorial_adds: row.editorial_adds,
    shazam_cities: row.shazam_cities,
    stream_rate: row.stream_rate,
    places_shown:
      places?.map(({ country, city }) => ({ country, city })) ?? null,
    builds,
    source_keys: [
      ...new Set([
        ...sourceKeys(row.source_keys),
        ...(places ? ["sz_chart"] : []),
      ]),
    ],
    facts_day: factsDay,
    close_no: builds[0]?.close_no ?? "0",
  };
}
export async function callOffers(
  current: Session,
  projections: readonly (Projection | null)[],
) {
  const cards = projections.filter((p) => p !== null);
  const offers: Record<string, CallOffer> = {};
  if (!cards.length) return offers;
  const [anchors, calls] = await Promise.all([
    callAnchors(cards.map((c) => c.song_key)),
    budget.run("light", () => readCalls(controlStore(), callWeek())),
  ]);
  for (const card of cards) {
    const builds = [...card.builds, anchors.value.build];
    if (!card.builds.length || !consistentCallBuilds(builds, card.close_no))
      continue;
    const rows = anchors.value.rows.filter(
      (r) => r.requested_key === card.song_key,
    );
    if (!rows.length) continue;
    const frozen = rows
      .slice(0, 20)
      .map(({ platform, platform_track_id, song_key }) => ({
        platform,
        platform_track_id,
        song_key,
      }));
    const facts = callSnapshot.parse({
      ...card,
      v: 1,
      builds,
      source_keys: [
        ...new Set([
          ...card.source_keys,
          ...rows.flatMap((r) => r.source_keys),
        ]),
      ],
      anchors: frozen,
      anchors_truncated: rows.length > 20,
      handle: current.handle,
      exp: Date.now() + CALL_CARD_TTL_MS,
      idempotency_key: randomUUID(),
    });
    const matching = calls.filter(
      (c) =>
        !c.undone_at &&
        c.facts.anchors.some((a) =>
          frozen.some(
            (b) =>
              a.platform === b.platform &&
              a.platform_track_id === b.platform_track_id,
          ),
        ),
    );
    const offer: CallOffer = {
      signed: signCall(facts),
      existing: matching.find((c) => c.author === current.handle) ?? null,
      other: matching.some((c) => c.author !== current.handle),
      otherInitial: person(
        matching.find((c) => c.author !== current.handle)?.author ?? "",
      )?.display_name.slice(0, 1),
      left: Math.max(
        0,
        CALLS_PER_WEEK -
          calls.filter((c) => c.author === current.handle).length,
      ),
      csrf: current.csrf_token,
    };
    offers[card.song_key] = offer;
    if (facts.card === "song") offers[`${card.song_key}:${facts.day}`] = offer;
  }
  return offers;
}

// Whether a song on Home can be called now. Home leads with movers, else its lead arrival; a card
// gets Call it only when all its reads come from one closed daily read.
export type HomeCalls = "open" | "used" | "waiting" | "no_songs" | "unknown";
export async function homeCalls(current: Session): Promise<HomeCalls> {
  try {
    const songs = await movers();
    let cards: (Projection | null)[] = songs.value.rows.map((row) =>
      moverProjection(row, songs.value.build),
    );
    if (!cards.length) {
      const landing = await arrivals();
      const arrived = landing.value.rows[0];
      cards = arrived ? [arrivalProjection(arrived, landing.value.build)] : [];
    }
    if (!cards.some((card) => card !== null)) return "no_songs";
    const offers = Object.values(await callOffers(current, cards));
    if (!offers.length) return "waiting";
    // Undone calls still use their slots, so an empty board can have none left.
    return offers.some((offer) => offer.left > 0) ? "open" : "used";
  } catch {
    return "unknown";
  }
}
