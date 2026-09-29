import { describe, it, expect } from "vitest";
import { coreExportRefusal } from "../src/manual.js";

// Run Now under Core holds the runner lock for its bind only, so a later Core run can supersede it.
describe("Run Now export outcome under Core", () => {
  it("reports a superseded export as run_now_superseded, not as a failed export", () => {
    expect(coreExportRefusal("superseded")).toMatchObject({ error_class: "run_now_superseded", status: 409 });
  });
  it("keeps export_failed for a failed or partial export and lets success and running through", () => {
    expect(coreExportRefusal("failed")).toMatchObject({ error_class: "export_failed" });
    expect(coreExportRefusal("partial")).toMatchObject({ error_class: "export_failed" });
    expect(coreExportRefusal("succeeded")).toBeNull();
    expect(coreExportRefusal("running")).toBeNull();
  });
});
