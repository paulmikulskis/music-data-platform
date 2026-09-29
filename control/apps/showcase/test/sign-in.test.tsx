import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { renderToStaticMarkup } from "react-dom/server";
import {
  PRE_COOKIE,
  preAuthentication,
  signLink,
  random,
} from "@mdp/showcase-auth";
vi.mock("../server/session", () => ({ sessionForToken: async () => null }));
vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({ controlStore: () => ({}) }));
const mocked = vi.hoisted(() => {
  const state: {
    redeem: ReturnType<typeof vi.fn>;
    pre: string;
    clearedReason: "expired" | "timeout" | "key-refused" | "ended" | null;
  } = { redeem: vi.fn(), pre: "", clearedReason: null };
  return state;
});
vi.mock("@mdp/showcase-auth", async (original) => ({
  ...(await original<typeof import("@mdp/showcase-auth")>()),
  redeem: mocked.redeem,
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      name === PRE_COOKIE ? { value: mocked.pre } : undefined,
  }),
}));
vi.mock("../components/clear-link", () => ({
  ClearLink: ({
    reason,
  }: {
    reason: "expired" | "timeout" | "key-refused" | "ended" | null;
  }) => {
    mocked.clearedReason = reason;
    return null;
  },
}));
import { POST } from "../app/auth/redeem/route";
import SignIn from "../app/sign-in/page";
import { proxy } from "../proxy";
beforeEach(() => {
  vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  vi.stubEnv(
    "MDP_SHOWCASE_PEOPLE",
    JSON.stringify([
      {
        handle: "quartz",
        display_name: "Quartz",
        email: "quartz@example.invalid",
        admin_key: "hidden-admin-key",
        api_key_id: "00000000-0000-4000-8000-000000000001",
      },
    ]),
  );
  mocked.pre = preAuthentication();
  mocked.redeem.mockReset();
});
afterEach(() => vi.unstubAllEnvs());
const token = () =>
  signLink({
    handle: "quartz",
    nonce: random(16),
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
function request(origin: string | null, pre = mocked.pre, formPre = pre) {
  return new NextRequest("https://mdp-showcase.example.invalid/sign-in", {
    method: "POST",
    headers: { ...(origin ? { origin } : {}), cookie: `${PRE_COOKIE}=${pre}` },
    body: new URLSearchParams({ token: token(), pre: formPre }),
  });
}
it("GET preview carries the token in a form but never consumes it or exposes an admin key", async () => {
  const html = renderToStaticMarkup(
    await SignIn({ searchParams: Promise.resolve({ t: token() }) }),
  );
  expect(html).toContain('method="post"');
  expect(html).toContain('name="token"');
  expect(html).not.toContain("hidden-admin-key");
  expect(mocked.redeem).not.toHaveBeenCalled();
});
it.each(["missing", "expired", "valid"])(
  "a visitor without a link gets the opening step with a %s pre-authentication cookie",
  async (state) => {
    mocked.pre =
      state === "missing"
        ? ""
        : preAuthentication(
            undefined,
            Date.now() - (state === "expired" ? 601000 : 0),
          );
    const html = renderToStaticMarkup(
      await SignIn({ searchParams: Promise.resolve({}) }),
    );
    expect(html).toContain(
      "Open your sign-in link to continue. If you do not have one, ask the platform operator.",
    );
    expect(html).not.toContain("This link has expired.");
    expect(mocked.clearedReason).toBeNull();
  },
);
it("keeps timeout recovery when reloading after the token leaves the address bar", async () => {
  const t = token();
  renderToStaticMarkup(await SignIn({ searchParams: Promise.resolve({ t }) }));
  expect(mocked.clearedReason).toBe("timeout");
  const html = renderToStaticMarkup(
    await SignIn({
      searchParams: Promise.resolve({
        reason: mocked.clearedReason ?? undefined,
      }),
    }),
  );
  expect(html).toContain(
    "This sign-in page timed out. Open the link from your message again.",
  );
  expect(html).not.toContain("Your sign-in ended.");
  expect(mocked.clearedReason).toBe("timeout");
  expect(mocked.redeem).not.toHaveBeenCalled();
  const reopened = renderToStaticMarkup(
    await SignIn({ searchParams: Promise.resolve({ t }) }),
  );
  expect(reopened).toContain('name="token"');
});
it("two GET tabs reuse their challenge with no cache or referrer leakage", () => {
  const first = proxy(new NextRequest("https://mdp-showcase.example.invalid/sign-in"));
  const pre = first.cookies.get(PRE_COOKIE)!.value;
  const second = proxy(
    new NextRequest("https://mdp-showcase.example.invalid/sign-in", {
      headers: { cookie: `${PRE_COOKIE}=${pre}` },
    }),
  );
  expect(second.cookies.get(PRE_COOKIE)).toBeUndefined();
  expect(second.headers.get("x-middleware-request-cookie")).toContain(pre);
  expect(first.headers.get("cache-control")).toBe("no-store");
  expect(first.headers.get("referrer-policy")).toBe("no-referrer");
  expect(first.headers.get("set-cookie")).toContain("SameSite=strict");
  expect(first.headers.get("set-cookie")).not.toContain("Domain=");
});
it("rejects missing/foreign origin and mismatched challenge before redemption", async () => {
  for (const req of [
    request(null),
    request("https://other.invalid"),
    request("https://mdp-showcase.example.invalid", mocked.pre, "wrong"),
  ]) {
    const result = await POST(req);
    expect(result.headers.get("location")).toContain("reason=expired");
  }
  expect(mocked.redeem).not.toHaveBeenCalled();
});
it("reports an expired challenge separately and keeps the link unconsumed", async () => {
  const old = preAuthentication(undefined, Date.now() - 601000);
  expect(
    (await POST(request("https://mdp-showcase.example.invalid", old))).headers.get(
      "location",
    ),
  ).toContain("reason=timeout");
  expect(mocked.redeem).not.toHaveBeenCalled();
});
it("sets the host-only session cookie and redirects without rotating or deleting the pre cookie", async () => {
  mocked.redeem.mockResolvedValue(random());
  const result = await POST(request("https://mdp-showcase.example.invalid"));
  expect(result.status).toBe(303);
  const cookie = result.headers.get("set-cookie")!;
  expect(cookie).toContain("__Host-mdp_showcase=");
  expect(cookie).toContain("Secure");
  expect(cookie).toContain("HttpOnly");
  expect(cookie).toContain("SameSite=lax");
  expect(cookie).not.toContain("Domain=");
  expect(cookie).not.toContain("__Host-pre=");
});
it("limits the form body even without Content-Length", async () => {
  const req = new NextRequest("https://mdp-showcase.example.invalid/sign-in", {
    method: "POST",
    headers: {
      origin: "https://mdp-showcase.example.invalid",
      cookie: `${PRE_COOKIE}=${mocked.pre}`,
    },
    body: new URLSearchParams({ token: "x".repeat(9000), pre: mocked.pre }),
  });
  expect((await POST(req)).headers.get("location")).toContain("reason=expired");
  expect(mocked.redeem).not.toHaveBeenCalled();
});

it("keeps used-link and pre-authentication timeout recovery separate", async () => {
  for (const reason of ["expired", "timeout"]) {
    const html = renderToStaticMarkup(
      await SignIn({ searchParams: Promise.resolve({ reason }) }),
    );
    expect(html).toContain(
      reason === "expired"
        ? "This link has expired. Ask for a new one."
        : "This sign-in page timed out. Open the link from your message again.",
    );
    expect(html).not.toContain("Your sign-in ended.");
  }
});
it("uses the configured maximum for the browser cookie", async () => {
  vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", "2");
  vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", "5");
  mocked.redeem.mockResolvedValue(random());
  expect(
    (await POST(request("https://mdp-showcase.example.invalid"))).headers.get(
      "set-cookie",
    ),
  ).toContain("Max-Age=432000");
});
it("validates session settings at startup even when inventory is disabled", async () => {
  vi.stubEnv("NEXT_RUNTIME", "nodejs");
  vi.stubEnv("MDP_SHOWCASE_INVENTORY_ENABLED", "0");
  vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", "0");
  const { register } = await import("../instrumentation");
  await expect(register()).rejects.toThrow(
    "MDP_SHOWCASE_IDLE_DAYS must be a whole number",
  );
});

it("names a refused admin key without ending the session", async () => {
  const html = renderToStaticMarkup(
    await SignIn({ searchParams: Promise.resolve({ reason: "key-refused" }) }),
  );
  expect(html).toContain("The Console refused this person&#x27;s key.");
  expect(html).toContain(
    "Ask the platform operator to check MDP_SHOWCASE_PEOPLE.",
  );
  expect(html).not.toContain("Your sign-in ended");
  expect(mocked.clearedReason).toBe("key-refused");
});
it.each(["{", '[{"admin_key":"private-value"}]'])(
  "refuses invalid people at boot and in health without exposing values",
  async (value) => {
    vi.stubEnv("NEXT_RUNTIME", "nodejs");
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", value);
    const { register } = await import("../instrumentation");
    await expect(register()).rejects.toThrow(
      /MDP_SHOWCASE_PEOPLE.*restart the app/,
    );
    const { GET } = await import("../app/healthz/route");
    const response = GET();
    expect(response.status).toBe(503);
    const body = await response.text();
    expect(body).toContain("showcase_people_invalid");
    expect(body).not.toContain("private-value");
  },
);
it("accepts people without unused email and view fields", async () => {
  vi.stubEnv(
    "MDP_SHOWCASE_PEOPLE",
    JSON.stringify([
      {
        handle: "fixture",
        display_name: "Test viewer",
        admin_key: "test-key",
        api_key_id: "00000000-0000-4000-8000-000000000001",
      },
    ]),
  );
  const { GET } = await import("../app/healthz/route");
  expect(GET().status).toBe(200);
});
