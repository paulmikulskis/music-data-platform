import { afterEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RequestCookiesAdapter } from "next/dist/server/web/spec-extension/adapters/request-cookies";
import { cookies } from "next/headers";
import { ended, expired, noLink, SESSION_COOKIE } from "@mdp/showcase-auth";
import { sessionForToken } from "../server/session";
import SignIn from "../app/sign-in/page";

vi.mock("../server/session", () => ({
  sessionForToken: vi.fn(async () => null),
}));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("../components/clear-link", () => ({ ClearLink: () => null }));
afterEach(() => vi.resetAllMocks());

async function page(reason?: string, session = false) {
  const jar = await import("next/dist/compiled/@edge-runtime/cookies");
  const headers = new Headers();
  if (session) headers.set("cookie", `${SESSION_COOKIE}=old-session`);
  vi.mocked(cookies).mockResolvedValue(
    RequestCookiesAdapter.seal(new jar.RequestCookies(headers)),
  );
  return renderToStaticMarkup(
    await SignIn({ searchParams: Promise.resolve({ reason }) }),
  );
}

it("gives a new visitor the sign-in link step", async () => {
  const html = await page();
  expect(html).toContain(noLink);
  expect(html).not.toContain(ended);
});
it.each(["ended", "expired", "timeout", "key-refused"])(
  "keeps recovery for %s",
  async (reason) => {
    const html = await page(reason);
    expect(html).not.toContain(noLink);
    if (reason === "ended") expect(html).toContain(ended);
    if (reason === "expired") expect(html).toContain(expired);
  },
);
it("recognizes a previous session cookie", async () => {
  expect(await page(undefined, true)).toContain(ended);
});

it("keeps key-refused recovery even with a live session cookie", async () => {
  expect(await page("key-refused", true)).not.toContain(noLink);
  expect(sessionForToken).not.toHaveBeenCalled();
});
