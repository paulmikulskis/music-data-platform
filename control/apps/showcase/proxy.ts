import { NextRequest, NextResponse } from "next/server";
import {
  PRE_COOKIE,
  SESSION_COOKIE,
  cookieOptions,
  preAuthentication,
} from "@mdp/showcase-auth";
import { admitted } from "./server/rate-limit";
import { bucketFor } from "./lib/request-bucket";
import { rateResponse } from "./server/rate-response";
// Only the short-lived challenge is prepared here. Protected handlers validate against the DB.
export function proxy(request: NextRequest) {
  const bucket = bucketFor(request.nextUrl.pathname, request.method);
  if (
    !admitted(
      request.headers.get("fly-client-ip") ?? "local",
      request.cookies.get(SESSION_COOKIE)?.value,
      Date.now(),
      bucket,
    )
  ) {
    // Navigation headers affect presentation only. They cannot select another
    // limiter or add requests to the shared authentication allowance.
    const document =
      request.headers.get("sec-fetch-dest") === "document" ||
      bucket === "document" ||
      (bucket === "auth" && ["GET", "HEAD"].includes(request.method));
    return rateResponse(document ? "document" : "data");
  }
  if (request.nextUrl.pathname !== "/sign-in") return NextResponse.next();
  if (request.method === "POST")
    return NextResponse.rewrite(new URL("/auth/redeem", request.url));
  const previous = request.cookies.get(PRE_COOKIE)?.value;
  const pre = preAuthentication(previous);
  request.cookies.set(PRE_COOKIE, pre);
  const response = NextResponse.next({ request: { headers: request.headers } });
  if (pre !== previous)
    response.cookies.set(PRE_COOKIE, pre, {
      ...cookieOptions,
      sameSite: "strict",
      maxAge: 600,
    });
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
export const config = {
  matcher: ["/((?!_next/static|_next/image|fonts/|brand/).*)"],
};
