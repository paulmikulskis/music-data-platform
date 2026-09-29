import { createHmac, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { required } from "@mdp/showcase-auth";
const proofContext = z.object({
  // "source" opens a source page with no proof level note, only the way back.
  level: z.enum(["row", "cycle", "partial", "unavailable", "source"]),
  song: z.string(),
  handle: z.string(),
  exp: z.number(),
  unstamped: z.boolean().optional(),
  // The showcase screen the operator console's Back bar returns to.
  back: z.string().optional(),
});
export type ProofContext = z.infer<typeof proofContext>;
function signature(body: string) {
  return createHmac("sha256", required("MDP_SHOWCASE_SESSION_SECRET"))
    .update(`proof:${body}`)
    .digest("base64url");
}
export function signProof(value: Omit<ProofContext, "exp">) {
  const body = Buffer.from(
    JSON.stringify({ ...value, exp: Date.now() + 600000 }),
  ).toString("base64url");
  return `${body}.${signature(body)}`;
}
export function verifyProof(
  token: string | null,
  handle: string,
): ProofContext | null {
  if (!token || token.length > 2000) return null;
  const [body, mac] = token.split(".");
  if (!body || !mac) return null;
  const expected = signature(body);
  if (
    mac.length !== expected.length ||
    !timingSafeEqual(Buffer.from(mac), Buffer.from(expected))
  )
    return null;
  try {
    const value = proofContext.parse(
      JSON.parse(Buffer.from(body, "base64url").toString()),
    );
    return value.handle === handle && value.exp > Date.now() ? value : null;
  } catch {
    return null;
  }
}
