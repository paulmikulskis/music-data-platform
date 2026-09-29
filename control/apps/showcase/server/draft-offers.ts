import { CALL_CARD_TTL_MS, CALLS_PER_WEEK } from "../lib/calls";
import "server-only";
import { randomUUID } from "node:crypto";
import type { Session } from "@mdp/showcase-auth";
import type { Draft } from "../lib/draft";
import type { CallOffer, SavedCall } from "../lib/calls";
import { signCall } from "./call-token";
export function draftOffers(
  current: Session,
  draft: Draft,
  calls: SavedCall[],
) {
  const offers: Record<string, CallOffer> = {};
  for (const candidate of draft.candidates)
    offers[candidate.song_key] = {
      signed: signCall({
        ...candidate.snapshot,
        handle: current.handle,
        exp: Date.now() + CALL_CARD_TTL_MS,
        idempotency_key: randomUUID(),
      }),
      existing:
        calls.find((c) => c.song_key === candidate.song_key && !c.undone_at) ??
        null,
      other: false,
      left: Math.max(0, CALLS_PER_WEEK - calls.length),
      csrf: current.csrf_token,
    };
  return offers;
}
