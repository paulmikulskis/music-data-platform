import { it, expect, vi, afterEach } from "vitest";
import { app } from "../src/app.js";
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
  it("serves a favicon before authentication", async () => {
    const response = await app.request("/favicon.ico");
    expect(response.status).toBe(200); expect(response.headers.get("content-type")).toContain("image/svg+xml");
  });
