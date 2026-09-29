import { beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const validate = vi.hoisted(() => vi.fn());
vi.mock("../server/session", () => ({ session: validate }));
vi.mock("../server/peek", () => ({ peek: vi.fn() }));
vi.mock("../server/proof-token", () => ({ signProof: vi.fn() }));
import { GET } from "../app/s/peek/route";
beforeEach(() => {
  validate.mockReset();
});
it("returns a retry response when session validation fails", async () => {
  validate.mockRejectedValue(new Error("Database unavailable"));
  const result = await GET(new Request("http://localhost/s/peek"));
  expect(result.status).toBe(503);
  expect(result.headers.get("Cache-Control")).toBe("no-store");
  expect(await result.json()).toMatchObject({
    message: expect.stringContaining("Retry"),
  });
});
it("refuses a revoked session before reading a preview", async () => {
  validate.mockResolvedValue(null);
  expect((await GET(new Request("http://localhost/s/peek"))).status).toBe(401);
});
