import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  hash,
  preAuthentication,
  random,
  signLink,
  validPre,
  verifyLink,
  people,
  validMutation,
  sessionLifetimes,
} from "../src/index.js";
const invented = {
  handle: "quartz",
  display_name: "Quartz",
  email: "quartz@example.invalid",
  admin_key: "invented-key",
  api_key_id: "00000000-0000-4000-8000-000000000001",
};
beforeEach(() => {
  vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([invented]));
});
afterEach(() => vi.unstubAllEnvs());
it("verifies signature and expiry without storing or consuming a link", () => {
  const link = {
    handle: "quartz",
    nonce: random(16),
    exp: Math.floor(Date.now() / 1000) + 60,
  };
  const token = signLink(link);
  expect(verifyLink(token)).toEqual(link);
  expect(verifyLink(token)).toEqual(link);
  expect(verifyLink(token + "x")).toBeNull();
  expect(verifyLink(token, Date.now() + 61_000)).toBeNull();
  expect(hash(random())).toHaveLength(64);
});
it("reuses a valid pre-auth challenge in two tabs and rejects delayed submits", () => {
  const now = Date.now(),
    first = preAuthentication(undefined, now);
  expect(preAuthentication(first, now + 20_000)).toBe(first);
  expect(validPre(first, now + 601_000)).toBe(false);
  expect(preAuthentication(first, now + 601_000)).not.toBe(first);
  expect(validPre(first + "x", now)).toBe(false);
});
it("requires the exact origin and session CSRF token", () => {
  const session = {
    id_hash: hash(random()),
    handle: invented.handle,
    csrf_token: random(),
    person: invented,
  };
  expect(
    validMutation(session, "https://mdp-showcase.example.invalid", session.csrf_token),
  ).toBe(true);
  expect(validMutation(session, null, session.csrf_token)).toBe(false);
  expect(
    validMutation(session, "https://other.invalid", session.csrf_token),
  ).toBe(false);
  expect(validMutation(session, "https://mdp-showcase.example.invalid", random())).toBe(
    false,
  );
});
describe("runtime configuration", () => {
  it("rejects repeated handles or keys", () => {
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([invented, invented]));
    expect(people).toThrow();
  });
  it("reads removal on the next request", () => {
    expect(people()).toHaveLength(1);
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", "[]");
    expect(people()).toHaveLength(0);
  });
});

it("uses two weeks idle and sixty days maximum by default", () => {
  vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", undefined);
  vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", undefined);
  expect(sessionLifetimes()).toEqual({ idleDays: 14, maxDays: 60 });
});
it.each(["", "0", "-1", "1.5", "NaN", "Infinity", "366", "1e2", " 14 "])(
  "rejects invalid session days %s",
  (value) => {
    for (const name of ["MDP_SHOWCASE_IDLE_DAYS", "MDP_SHOWCASE_MAX_DAYS"]) {
      vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", "1");
      vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", "365");
      vi.stubEnv(name, value);
      expect(() => sessionLifetimes()).toThrow(
        `${name} must be a whole number from 1 to 365.`,
      );
    }
  },
);
it("accepts the endpoints and refuses idle days above maximum days", () => {
  vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", "1");
  vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", "365");
  expect(sessionLifetimes()).toEqual({ idleDays: 1, maxDays: 365 });
  vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", "365");
  expect(sessionLifetimes()).toEqual({ idleDays: 365, maxDays: 365 });
  vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", "364");
  expect(() => sessionLifetimes()).toThrow(
    "Set idle days at or below maximum days",
  );
});
