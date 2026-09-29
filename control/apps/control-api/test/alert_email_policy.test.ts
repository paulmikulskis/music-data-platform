import { it, expect } from "vitest";
import { shouldEmail } from "../src/email.js";
  it("emails warning alerts in every required class while leaving digest-only warnings", () => {
    for (const value of ["schema_breaking", "llm_budget_exceeded", "cost_cap_hit", "control_db_unavailable", "warehouse_unavailable", "object_store_unavailable", "litellm_unavailable", "dbt_api_unavailable"])
      expect(shouldEmail("warning", value)).toBe(true);
    expect(shouldEmail("warning", "schema_drift")).toBe(false);
    expect(shouldEmail("critical", "dbt_failure")).toBe(true);
  });
