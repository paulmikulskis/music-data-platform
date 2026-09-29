import { createHmac } from "node:crypto";
import { afterEach, it, expect, vi } from "vitest";
import { signProof, verifyProof } from "../server/proof-token";
afterEach(() => vi.unstubAllEnvs());
it("binds proof labels to the server decision, person and expiry", () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "x".repeat(32));
  const token = signProof({ level: "cycle", song: "song", handle: "fixture" });
  expect(verifyProof(token, "fixture")?.level).toBe("cycle");
  expect(verifyProof(token, "other")).toBeNull();
  expect(verifyProof(token + "x", "fixture")).toBeNull();
  expect(verifyProof("row", "fixture")).toBeNull();
  vi.useFakeTimers();
  vi.advanceTimersByTime(600001);
  expect(verifyProof(token, "fixture")).toBeNull();
  vi.useRealTimers();
});

it.each([
  { level: "row", song: 3, unstamped: false },
  { level: "row", song: "song", unstamped: "false" },
  { level: "other", song: "song" },
])("rejects signed proof data with invalid field types", (value) => {
  const secret = "x".repeat(32);
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", secret);
  const body = Buffer.from(
    JSON.stringify({ ...value, handle: "fixture", exp: Date.now() + 1000 }),
  ).toString("base64url");
  const mac = createHmac("sha256", secret)
    .update(`proof:${body}`)
    .digest("base64url");
  expect(verifyProof(`${body}.${mac}`, "fixture")).toBeNull();
});
