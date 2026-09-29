import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mover } from "../server/models";
import { movers } from "./browser/fixtures";
const mocks = vi.hoisted(() => ({
  session: vi.fn(),
  captured: vi.fn(),
  resolve: vi.fn(),
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/session", () => ({ session: mocks.session }));
vi.mock("../server/room", () => ({
  room: () => mocks.session(),
  unavailable: () => null,
}));
vi.mock("../server/proof", () => ({ resolveProof: mocks.resolve }));
vi.mock("../server/proof-store", () => ({
  capturedProof: mocks.captured,
  capturedHistory: () => null,
}));
vi.mock("../server/reads", async (original) => ({
  ...(await original<typeof import("../server/reads")>()),
  movers: async () => null,
}));
vi.mock("../components/shell", () => ({
  Shell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
import { GET } from "../app/s/proof/[key]/[mark]/engine/route";
import SongPage from "../app/s/song/[key]/page";
const origin = "https://showcase.invalid";
const key = "apple:fixture";
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("MDP_SHOWCASE_ORIGIN", origin);
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "fixture-secret");
  mocks.session.mockResolvedValue({
    handle: "fixture",
    person: {},
    csrf_token: "fixture",
  });
  mocks.captured.mockReturnValue(null);
});
afterEach(() => vi.unstubAllEnvs());
it.each(["missing mover", "missing history", "unavailable", "failed"])(
  "gives %s proof one link back to the song",
  async (kind) => {
    if (kind === "unavailable" || kind === "failed")
      mocks.captured.mockReturnValue(mover.parse(movers[0]));
    mocks.resolve.mockResolvedValue({ level: "unavailable", href: "/ops" });
    if (kind === "failed")
      mocks.resolve.mockRejectedValue(new Error("offline"));
    const response = await GET(
      new Request(
        origin +
          "/s/proof/fixture/0" +
          (kind === "missing history" ? "?history=old" : ""),
      ),
      { params: Promise.resolve({ key, mark: "0" }) },
    );
    const target = new URL(response.headers.get("location") ?? origin);
    expect(target.pathname).toBe("/s/song/apple%3Afixture");
    const html = renderToStaticMarkup(
      await SongPage({
        params: Promise.resolve({ key }),
        searchParams: Promise.resolve({
          showcase_proof: target.searchParams.get("showcase_proof") ?? "",
        }),
      }),
    );
    expect(html).toContain("Proof is unavailable.");
    expect(html).toContain('href="/s/song/apple%3Afixture"');
    expect(html).toContain("Open the song");
    expect(html.match(/<a /g)).toHaveLength(1);
  },
);
it("keeps available proof at its contributing run", async () => {
  mocks.captured.mockReturnValue(mover.parse(movers[0]));
  mocks.resolve.mockResolvedValue({ level: "row", href: "/runs/fixture" });
  const response = await GET(new Request(origin + "/s/proof/fixture/0"), {
    params: Promise.resolve({ key, mark: "0" }),
  });
  expect(new URL(response.headers.get("location") ?? origin).pathname).toBe(
    "/runs/fixture",
  );
});
it("keeps a real session end on its existing sign-in page", async () => {
  mocks.session.mockResolvedValue(null);
  const response = await GET(new Request(origin + "/s/proof/fixture/0"), {
    params: Promise.resolve({ key, mark: "0" }),
  });
  expect(response.headers.get("location")).toBe(origin + "/sign-in");
  expect(mocks.resolve).not.toHaveBeenCalled();
});
