import { it, expect, vi, afterEach } from "vitest";
import { createApp } from "../../data-api/src/app.js";
import postgres from "postgres";
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
  it("preserves rejected authentication status on the data HTTP surface", async () => {
    vi.stubEnv("MDP_AUTH_MODE", "dev"); vi.stubEnv("CLERK_SECRET_KEY", "");
    const db = postgres("postgresql://localhost/unused");
    try {
      const response = await createApp(db, db).request("/api/marts/mart_chart_history", { headers: { "x-mdp-dev-user": "rejected" } });
      expect(response.status).toBe(401);
      expect(await response.json()).toMatchObject({ code: "DATA", data: { error_class: "unauthorized" } });
    } finally { await db.end(); }
  });
