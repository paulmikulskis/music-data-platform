import { describe, it, expect, vi, afterEach } from "vitest";
import {
  emailRetry,
  emailTransport,
  retryDelaySeconds,
  startEmailWorker,
} from "../src/email.js";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
const noTransport = () => {
  vi.stubEnv("RESEND_API_KEY", "");
  vi.stubEnv("SMTP_URL", "");
  vi.stubEnv("MDP_AUTH_MODE", "production");
  vi.stubEnv("MDP_EMAIL_FROM", "fixture@example.invalid");
  vi.stubEnv("MDP_EMAIL_TO", "fixture@example.invalid");
};
describe("alert email worker", () => {
  it("backs off from 30 s, doubling to a 1 h cap, over a bounded number of attempts", () => {
    const delays = Array.from({ length: emailRetry.maxAttempts - 1 }, (_, i) =>
      retryDelaySeconds(i + 1),
    );
    expect(delays).toEqual([30, 60, 120, 240, 480, 960, 1920, 3600, 3600]);
    expect(emailRetry.maxAttempts).toBe(10);
  });
  it("resolves the transport from configuration, and none without one outside dev", () => {
    noTransport();
    expect(emailTransport()).toBeNull();
    vi.stubEnv("MDP_AUTH_MODE", "dev");
    expect(emailTransport()).toBe("console");
    vi.stubEnv("SMTP_URL", "smtp://fixture.invalid");
    expect(emailTransport()).toBe("smtp");
    vi.stubEnv("RESEND_API_KEY", "fixture");
    expect(emailTransport()).toBe("resend");
  });
  it.each(["MDP_EMAIL_FROM", "MDP_EMAIL_TO"])(
    "does not attempt delivery without %s",
    (key) => {
      noTransport();
      vi.stubEnv("RESEND_API_KEY", "fixture");
      vi.stubEnv(key, " ");
      expect(emailTransport()).toBeNull();
      vi.stubEnv("RESEND_API_KEY", "");
      vi.stubEnv("SMTP_URL", "smtp://fixture.invalid");
      expect(emailTransport()).toBeNull();
    },
  );
  it("logs one structured warning at startup without a transport, however often it drains", async () => {
    noTransport();
    vi.useFakeTimers();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const drain = vi.fn(async () => {});
    const stop = startEmailWorker(drain);
    try {
      await vi.advanceTimersByTimeAsync(35_000);
      expect(drain).toHaveBeenCalledTimes(4);
      expect(warn).toHaveBeenCalledTimes(1);
      expect(JSON.parse(String(warn.mock.calls[0]?.[0]))).toMatchObject({
        event: "alert_email_disabled",
        reason: "not_configured",
      });
    } finally {
      stop();
    }
  });
  it("logs no warning when a transport is configured", () => {
    noTransport();
    vi.stubEnv("RESEND_API_KEY", "fixture");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    startEmailWorker(async () => {})();
    expect(warn).not.toHaveBeenCalled();
  });
});
