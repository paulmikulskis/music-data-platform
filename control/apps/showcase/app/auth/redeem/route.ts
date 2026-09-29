import { NextRequest, NextResponse } from "next/server";
import {
  cookieOptions,
  equal,
  origin,
  PRE_COOKIE,
  redeem,
  SESSION_COOKIE,
  validPre,
  sessionLifetimes,
} from "@mdp/showcase-auth";
import { smallForm } from "../../../server/form";
import { controlStore } from "../../../server/clients";
export async function POST(request: NextRequest) {
  const fail = (reason: string) =>
    NextResponse.redirect(new URL(`/sign-in?reason=${reason}`, origin()), 303);
  if (request.headers.get("origin") !== origin()) return fail("expired");
  if (Number(request.headers.get("content-length") ?? 0) > 8192)
    return fail("expired");
  const form = await smallForm(request).catch(() => null);
  const pre = request.cookies.get(PRE_COOKIE)?.value ?? "";
  if (!pre || !validPre(pre)) return fail("timeout");
  if (
    !form ||
    typeof form.get("pre") !== "string" ||
    !equal(String(form.get("pre")), pre)
  )
    return fail("expired");
  const token = form.get("token");
  if (typeof token !== "string") return fail("expired");
  const id = await redeem(
    controlStore(),
    token,
    request.headers.get("user-agent"),
  );
  if (!id) return fail("expired");
  const response = NextResponse.redirect(new URL("/", origin()), 303);
  response.cookies.set(SESSION_COOKIE, id, {
    ...cookieOptions,
    maxAge: sessionLifetimes().maxDays * 86400,
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
