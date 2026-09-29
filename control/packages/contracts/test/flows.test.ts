import { describe, expect, it } from "vitest";
import {
  functionFlows,
  functionFlow,
  functionFlowSchema,
  markFor,
} from "../src/flows.js";
import { marks } from "../src/marks.js";
import { sourceBrand } from "../src/source-wording.js";

describe("shared function flows", () => {
  it("parses every generated flow and resolves every stage mark", () => {
    expect(Object.keys(functionFlows)).toHaveLength(36);
    for (const flow of Object.values(functionFlows)) {
      expect(functionFlowSchema.safeParse(flow).success).toBe(true);
      for (const stage of flow.stages)
        expect(markFor(stage.mark, "dark")).not.toBeNull();
    }
    expect(functionFlow("missing")).toBeNull();
  });
  it("provides both tones or a plate and covers every source brand", () => {
    for (const mark of Object.values(marks)) {
      if (Object.values(mark.files).length)
        expect(
          !!(mark.files.onDark && mark.files.onLight) || !!mark.plate,
        ).toBe(true);
    }
    for (const key of sourceBrand.options) expect(marks[key]).toBeDefined();
    expect(markFor("spotify", "dark")?.src).toContain("spotify-dark.svg");
    expect(markFor("spotify", "light")?.src).toContain("spotify-light.svg");
    expect(markFor("billboard", "light")?.src).toBeNull();
  });
});
