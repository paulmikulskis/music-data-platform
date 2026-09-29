import { describe, it, expect, afterAll } from "vitest";
import { randomUUID } from "node:crypto";
import { createRouterClient } from "@orpc/server";
import { router } from "../src/router.js";
import { database } from "../src/db.js";
// A provider budget's request cap: the operator sets it on create and on
// raise, only a provider budget carries one, a provider budget needs one and pauses at it, a vendor
// provider row never loses it, and the column refuses a negative value.
const enabled = process.env.MDP_CONTROL_INTEGRATION === "1";
describe.skipIf(!enabled)("provider request cap", () => {
  const identity = { actor: "provider-cap-test", admin: true, tenant_id: null, tenant_slug: null };
  const client = () => createRouterClient(router, { context: { identity, db: database() } });
  afterAll(async () => { if (enabled) await database().end(); });
  it("sets, replaces and removes a provider budget's request cap and refuses one elsewhere", async () => {
    const scope_id = randomUUID();
    const budget = await client().budgets.create({ scope: "provider", scope_id, period: "monthly", cap_cents: "0",
      ceiling_cents: "0", soft_pct: 80, hard_action: "pause", cap_requests: "2000" });
    const global = await client().budgets.create({ scope: "global", period: `test-${randomUUID()}`, cap_cents: "10",
      ceiling_cents: "20", soft_pct: 80, hard_action: "warn" });
    try {
      expect(budget.cap_requests).toBe("2000");
      expect(global.cap_requests).toBeNull();
      expect((await client().budgets.list({})).find(b => b.id === budget.id)?.cap_requests).toBe("2000");
      // A raise without cap_requests keeps it; with one it replaces it, and null removes it.
      expect((await client().budgets.raise({ id: budget.id, cap_cents: "0" })).cap_requests).toBe("2000");
      expect((await client().budgets.raise({ id: budget.id, cap_cents: "0", cap_requests: "1500" })).cap_requests).toBe("1500");
      // The runtime treats a provider row without a request cap as no row, so it is never removed.
      await expect(client().budgets.raise({ id: budget.id, cap_cents: "0", cap_requests: null }))
        .rejects.toMatchObject({ data: { error_class: "invalid_request" } });
      for (const bad of [{ hard_action: "warn" as const, cap_requests: "10" }, { hard_action: "degrade" as const, cap_requests: "10" },
        { hard_action: "pause" as const, cap_requests: null }])
        await expect(client().budgets.create({ scope: "provider", scope_id: randomUUID(), period: "monthly", cap_cents: "0",
          ceiling_cents: "0", soft_pct: 80, ...bad })).rejects.toBeTruthy();
      await expect(client().budgets.raise({ id: global.id, cap_cents: "10", cap_requests: "5" }))
        .rejects.toMatchObject({ data: { error_class: "invalid_request" } });
      await expect(client().budgets.create({ scope: "global", period: `test-${randomUUID()}`, cap_cents: "1",
        ceiling_cents: "1", soft_pct: 80, hard_action: "warn", cap_requests: "5" })).rejects.toBeTruthy();
      await expect(database()`UPDATE control.budget SET cap_requests=-1 WHERE id=${budget.id}`)
        .rejects.toMatchObject({ constraint_name: "budget_cap_requests_check" });
      // A row with no cents ceiling (created outside the API) still takes a new request cap.
      await database()`UPDATE control.budget SET ceiling_cents=NULL WHERE id=${budget.id}`;
      expect((await client().budgets.raise({ id: budget.id, cap_cents: "0", cap_requests: "900" })).cap_requests).toBe("900");
      await expect(client().budgets.raise({ id: budget.id, cap_cents: "1" }))
        .rejects.toMatchObject({ data: { error_class: "budget_ceiling" } });
    } finally {
      await database()`DELETE FROM control.budget WHERE id IN ${database()([budget.id, global.id])}`;
    }
  });
});
