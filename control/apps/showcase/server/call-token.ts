import { createHmac, timingSafeEqual } from "node:crypto";
import { required } from "@mdp/showcase-auth";
import { callSnapshot, type CallSnapshot, type SignedCard } from "../lib/calls";
import { consistentCallBuilds } from "./call-builds";
function signature(body: string) {
  return createHmac("sha256", required("MDP_SHOWCASE_SESSION_SECRET"))
    .update(`call:${body}`)
    .digest("base64url");
}
export function signCall(value: CallSnapshot): SignedCard {
  const parsed = callSnapshot.parse(value);
  const body = JSON.stringify(parsed);
  return {
    body,
    signature: signature(body),
    idempotency_key: parsed.idempotency_key,
  };
}
export function verifyCall(card: SignedCard, handle: string, now = Date.now()) {
  const expected = signature(card.body);
  if (
    Buffer.byteLength(card.signature) !== Buffer.byteLength(expected) ||
    !timingSafeEqual(Buffer.from(card.signature), Buffer.from(expected))
  )
    return null;
  try {
    const value = callSnapshot.parse(JSON.parse(card.body));
    return value.handle === handle &&
      value.idempotency_key === card.idempotency_key &&
      value.exp > now &&
      consistentCallBuilds(value.builds, value.close_no)
      ? value
      : null;
  } catch {
    return null;
  }
}
