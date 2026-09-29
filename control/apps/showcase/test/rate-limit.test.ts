import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { SESSION_COOKIE } from "@mdp/showcase-auth";
vi.mock("server-only", () => ({}));
import { bucketFor } from "../lib/request-bucket";
import { rateResponse } from "../server/rate-response";

beforeEach(() => vi.resetModules());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

it.each([
  ["/sign-in", "GET", "auth"],
  ["/sign-in", "POST", "auth"],
  ["/auth/redeem", "POST", "auth"],
  ["/auth", "GET", "auth"],
  ["/auth/another-handler", "POST", "auth"],
  ["/sign-out", "POST", "auth"],
  ["/", "GET", "document"],
  ["/stack", "GET", "document"],
  ["/s/song/one", "GET", "document"],
  ["/s/proof/source/one", "GET", "document"],
  ["/s/proof/one/two/engine", "GET", "document"],
  ["/s/trace", "GET", "data"],
  ["/s/peek", "GET", "data"],
  ["/s/apps", "GET", "data"],
  ["/s/stack", "GET", "data"],
  ["/s/night", "GET", "data"],
  ["/s/night/details", "GET", "data"],
  ["/s/operations/one", "GET", "data"],
  ["/s/team-question", "GET", "data"],
  ["/rpc/platform/night", "POST", "data"],
  ["/rpc/platform/sources", "GET", "data"],
  ["/events", "HEAD", "data"],
  ["/events", "GET", "data"],
  ["/library/search", "GET", "data"],
  ["/visit", "POST", "data"],
  ["/calls", "POST", "data"],
  ["/calls", "GET", "document"],
  ["/art/apple%3A1", "GET", "art"],
  ["/artist-photo/Q42", "GET", "art"],
  ["/artist-photo/Q42/credit", "GET", "data"],
])("classifies %s %s as %s", (pathname, method, expected) => {
  expect(bucketFor(pathname, method)).toBe(expected);
});

it("gives each bucket its own IP ceiling and opens the next second", async () => {
  const { admitted, limits } = await import("../server/rate-limit");
  for (const bucket of ["auth", "document", "data", "art"] as const) {
    for (let i = 0; i < limits[bucket].ip; i++) {
      expect(admitted("office", undefined, 1000, bucket)).toBe(true);
    }
    expect(admitted("office", undefined, 1999, bucket)).toBe(false);
    expect(admitted("office", undefined, 2000, bucket)).toBe(true);
  }
});

it("bounds one session across IP changes without spending the other person's IP budget", async () => {
  const { admitted, limits } = await import("../server/rate-limit");
  for (const bucket of ["auth", "document", "data", "art"] as const) {
    for (let i = 0; i < limits[bucket].session; i++) {
      expect(admitted(`ip-${i}`, "loop", 1000, bucket)).toBe(true);
    }
    for (let i = 0; i < limits[bucket].ip; i++) {
      expect(admitted("office", "loop", 1000, bucket)).toBe(false);
    }
    expect(admitted("office", "other-person", 1000, bucket)).toBe(true);
  }
});

it("admits six simultaneous Home, viewer and peek bursts on one IP with headroom", async () => {
  const { admitted } = await import("../server/rate-limit");
  // Twice the measured Home + viewer + peek + Stack demand in one second.
  for (let person = 0; person < 2; person++) {
    for (let tab = 0; tab < 3; tab++) {
      for (const [bucket, count] of [
        ["document", 6],
        ["data", 14],
        ["art", 4],
      ] as const) {
        for (let i = 0; i < count; i++) {
          expect(admitted("office", `person-${person}`, 1000, bucket)).toBe(
            true,
          );
        }
      }
    }
  }
});

it("returns catalog JSON and a next step for data and art", async () => {
  for (const bucket of ["data", "art"] as const) {
    const response = rateResponse(bucket);
    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBe("1");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toMatchObject({
      error_class: "showcase_rate_limited",
      next_step: expect.stringContaining("Retry"),
    });
  }
});
it("returns a styled document with one automatic retry and manual Reload", async () => {
  const response = rateResponse("document");
  expect(response.status).toBe(429);
  expect(response.headers.get("Content-Type")).toContain("text/html");
  expect(response.headers.get("Retry-After")).toBe("1");
  const html = await response.text();
  expect(html).toContain("wait a second.");
  expect(html).toContain("setTimeout(() => location.reload(), 1000)");
  expect(html).toContain('id="reload"');
});

it("bounds caller memory and reclaims expired windows", async () => {
  const { admitted } = await import("../server/rate-limit");
  for (let i = 0; i < 5000; i++) {
    expect(admitted(`ip-${i}`, `session-${i}`, 1000, "data")).toBe(true);
  }
  expect(admitted("new-ip", "new-session", 1000, "data")).toBe(false);
  expect(admitted("new-ip", "new-session", 2000, "data")).toBe(true);
});

it("reclaims expired cookies when a new caller needs two slots at 9,999 entries", async () => {
  const { admitted, limits } = await import("../server/rate-limit");
  let now = 1000;
  // One IP key plus 9,998 session keys leaves one free slot.
  for (let i = 0; i < 9998; i++) {
    now = 1000 + Math.floor(i / limits.data.ip) * 1000;
    expect(admitted("rotating-ip", `cookie-${i}`, now, "data")).toBe(true);
  }
  expect(admitted("new-ip", "new-session", now + 1000, "data")).toBe(true);
});

it("classifies app requests by path and method", () => {
  expect(bucketFor("/actions", "GET")).toBe("document");
  expect(bucketFor("/actions", "POST")).toBe("data");
  expect(bucketFor("/rpc/platform/night", "GET")).toBe("data");
});

it("shares ten auth requests per IP across routes, methods, cookies and headers", async () => {
  vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  vi.spyOn(Date, "now").mockReturnValue(1000);
  const { proxy } = await import("../proxy");
  const paths = [
    "/sign-in",
    "/auth/redeem",
    "/sign-in/",
    "/auth/redeem/",
    "/sign-out",
  ];
  const destinations = [undefined, "document", "empty", "iframe", "script"];
  function request(index: number) {
    const destination =
      destinations[Math.floor(index / paths.length) % destinations.length];
    const headers = new Headers({
      cookie: `${SESSION_COOKIE}=rotated-${index}`,
      accept: index % 2 ? "text/html" : "application/json",
      "content-type":
        index % 2 ? "application/x-www-form-urlencoded" : "application/json",
      "x-forwarded-for": `untrusted-${index}`,
      rsc: String(index % 2),
    });
    if (destination !== undefined) headers.set("sec-fetch-dest", destination);
    return new NextRequest(
      `https://showcase.invalid${paths[index % paths.length]}`,
      {
        method: index % 2 ? "POST" : "GET",
        headers,
      },
    );
  }
  for (let i = 0; i < 10; i++) expect(proxy(request(i)).status).toBe(200);
  for (let i = 10; i < 35; i++) {
    const req = request(i);
    const response = proxy(req);
    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBe("1");
    expect(response.headers.get("Content-Type")).toContain(
      req.method === "GET" || req.headers.get("sec-fetch-dest") === "document"
        ? "text/html"
        : "application/json",
    );
  }
  vi.mocked(Date.now).mockReturnValue(2000);
  expect(proxy(request(30)).status).toBe(200);
});

it("keeps form and fetch refusals in one app bucket while formatting each response", async () => {
  vi.spyOn(Date, "now").mockReturnValue(1000);
  const { proxy } = await import("../proxy");
  const { admitted, limits } = await import("../server/rate-limit");
  for (let i = 0; i < limits.data.ip; i++) {
    expect(admitted("local", undefined, 1000, "data")).toBe(true);
  }
  for (const destination of ["document", "empty"]) {
    const response = proxy(
      new NextRequest("https://showcase.invalid/actions", {
        method: "POST",
        headers: { "sec-fetch-dest": destination },
      }),
    );
    expect(response.status).toBe(429);
    expect(response.headers.get("Content-Type")).toContain(
      destination === "document" ? "text/html" : "application/json",
    );
  }
});
