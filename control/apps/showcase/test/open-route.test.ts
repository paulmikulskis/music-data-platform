import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const validate = vi.hoisted(() => vi.fn());
vi.mock("../server/session", () => ({ session: validate }));
import { GET } from "../app/s/open/route";
import { verifyProof } from "../server/proof-token";

beforeEach(() => {
  validate.mockReset().mockResolvedValue({ handle: "fixture" });
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
});
afterEach(() => vi.unstubAllEnvs());
const request = (to: string, back = "/stack#warehouse") =>
  new Request(
    `http://showcase.invalid/s/open?${new URLSearchParams({ to, back })}`,
  );

it.each([
  "/ops",
  "/workbench",
  "/explorer",
  "/functions",
  "/runs",
  "/explorer?q=marts.example_table",
])(
  "signs %s for the current person and preserves the return anchor",
  async (to) => {
    const response = await GET(request(to));
    expect(response.status).toBe(303);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const location = new URL(
      response.headers.get("Location")!,
      "http://showcase.invalid",
    );
    expect(location.pathname).toBe(to.split("?")[0]);
    expect(location.searchParams.get("q")).toBe(
      to.includes("?") ? "marts.example_table" : null,
    );
    const token = location.searchParams.get("showcase_proof");
    expect(verifyProof(token, "fixture")).toMatchObject({
      back: "/stack#warehouse",
      level: "source",
    });
    expect(verifyProof(token, "another-fixture")).toBeNull();
  },
);
it.each([
  "https://example.invalid/ops",
  "//example.invalid/ops",
  "/ops/../tenants",
  "/%6fps",
  "/ops#extra",
  "/ops/",
  "/tenants",
  "/rpc/status",
  "/functions/example",
  "/ops?q=x",
  "/explorer?showcase_proof=untrusted",
  "/explorer?q=x&q=y",
  "/explorer?q=x&other=y",
  "/explorer?q=",
  "/explorer?q=x?other=y",
  "/ops\\extra",
])("refuses an unreviewed destination: %s", async (to) => {
  const response = await GET(request(to));
  expect(response.status).toBe(400);
  expect(response.headers.has("Location")).toBe(false);
  expect(await response.text()).toContain("Open /stack");
});
it.each([
  "//example.invalid",
  "/ops",
  "/stack/../ops",
  "/stack%2f..%2fops",
  "/s/open",
  "",
])("refuses an unsafe return path: %s", async (back) => {
  expect((await GET(request("/ops", back))).status).toBe(400);
});
it("refuses duplicate and unknown parameters", async () => {
  for (const extra of ["&to=/runs", "&back=/", "&unknown=true"]) {
    expect((await GET(new Request(request("/ops").url + extra))).status).toBe(
      400,
    );
  }
});
it("requires a session and gives a next step when its check fails", async () => {
  validate.mockResolvedValue(null);
  expect((await GET(request("/ops"))).status).toBe(401);
  validate.mockRejectedValue(new Error("synthetic failure"));
  const response = await GET(request("/ops"));
  expect(response.status).toBe(503);
  expect(await response.text()).toContain("Retry from /stack");
});
