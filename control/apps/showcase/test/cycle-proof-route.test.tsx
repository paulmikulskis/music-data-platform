import { beforeEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { martBuild } from "@mdp/data-sdk";
import { build } from "./browser/fixtures";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ session: vi.fn(), resolve: vi.fn() }));
vi.mock("../server/session", () => ({ session: mocks.session }));
vi.mock("../server/proof", () => ({ resolveCycleProof: mocks.resolve }));
vi.mock("../server/proof-token", () => ({ signProof: () => "signed-proof" }));
vi.mock("@mdp/showcase-auth", () => ({
  origin: () => "https://showcase.invalid",
}));
import { GET } from "../app/s/proof/cycle/[cycle]/engine/route";
import { HowSheet } from "../components/how-we-know";
import { cycleProofLink } from "../lib/proof-link";
const stamp = martBuild.parse(build("mart_arrivals_current"));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.session.mockResolvedValue({
    person: { handle: "fixture" },
    handle: "fixture",
  });
});
it.each(["mart_arrivals_current", "mart_song_day"])(
  "takes %s entries to the captured cycle's run",
  async (relation) => {
    const captured = martBuild.parse(build(relation));
    const link = cycleProofLink(captured);
    const html = renderToStaticMarkup(
      <HowSheet
        label="Entries"
        provenance={{
          build: captured,
          queried_at: captured.built_at!,
          scope: "global",
          query: "Entries read",
          sql: "SELECT 1",
        }}
      />,
    );
    expect(html).toContain(link);
    expect(html).not.toContain("/explorer?cycle_id");
    mocks.resolve.mockResolvedValue({ level: "cycle", href: "/runs/fixture" });
    // The link opens the plain summary; its operator console link resolves the run.
    expect(link.startsWith(`/s/proof/cycle/${captured.cycle_id}?`)).toBe(true);
    const engine = link.replace("?", "/engine?");
    const response = await GET(
      new Request(`https://showcase.invalid${engine}`),
      {
        params: Promise.resolve({ cycle: captured.cycle_id! }),
      },
    );
    expect(mocks.resolve).toHaveBeenCalledWith(
      { handle: "fixture" },
      captured.cycle_id,
      relation,
    );
    expect(response.headers.get("location")).toBe(
      "https://showcase.invalid/runs/fixture?showcase_proof=signed-proof",
    );
  },
);
it("requires a session before reading proof", async () => {
  mocks.session.mockResolvedValue(null);
  const response = await GET(
    new Request("https://showcase.invalid/s/proof/cycle/test"),
    { params: Promise.resolve({ cycle: "test" }) },
  );
  expect(response.headers.get("location")).toBe(
    "https://showcase.invalid/sign-in",
  );
  expect(mocks.resolve).not.toHaveBeenCalled();
});
it("sends missing stamps to Rising and rejects invalid cycle ids", async () => {
  expect(cycleProofLink({ ...stamp, stamped: false })).toBe(
    "/songs?view=rising",
  );
  await GET(new Request("https://showcase.invalid/s/proof/cycle/test"), {
    params: Promise.resolve({ cycle: "test" }),
  });
  expect(mocks.resolve).not.toHaveBeenCalled();
});
