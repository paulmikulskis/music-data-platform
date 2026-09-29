import { it, expect } from "vitest";
import { sanitize } from "../src/audit.js";
  it("redacts nested credentials and CSV while keeping correlation keys", () => {
    expect(sanitize({ key: "manual-correlation", csv: "private", config: { token: "private", password: "private" } }))
      .toEqual({ key: "manual-correlation", csv: "[REDACTED]", config: { token: "[REDACTED]", password: "[REDACTED]" } });
  });
