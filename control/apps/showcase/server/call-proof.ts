import { createHmac, timingSafeEqual } from "node:crypto";
import { required } from "@mdp/showcase-auth";
import { z } from "zod";
import { locator, type Locator } from "./models";
const context = z.object({
  call: z.uuid(),
  handle: z.string(),
  locator,
  exp: z.number(),
});
function mac(body: string) {
  return createHmac("sha256", required("MDP_SHOWCASE_SESSION_SECRET"))
    .update(`call-place:${body}`)
    .digest("base64url");
}
export function signCallPlace(call: string, handle: string, mark: Locator) {
  const body = Buffer.from(
    JSON.stringify({ call, handle, locator: mark, exp: Date.now() + 86400000 }),
  ).toString("base64url");
  return `${body}.${mac(body)}`;
}
export function verifyCallPlace(
  token: string | null,
  call: string,
  handle: string,
) {
  if (!token || token.length > 8000) return null;
  const [body, signature, extra] = token.split(".");
  if (
    !body ||
    !signature ||
    extra ||
    Buffer.byteLength(signature) !== Buffer.byteLength(mac(body)) ||
    !timingSafeEqual(Buffer.from(signature), Buffer.from(mac(body)))
  )
    return null;
  try {
    const value = context.parse(
      JSON.parse(Buffer.from(body, "base64url").toString()),
    );
    return value.call === call &&
      value.handle === handle &&
      value.exp > Date.now()
      ? value.locator
      : null;
  } catch {
    return null;
  }
}
