import { expect, it } from "vitest";
import { z } from "zod";
import * as sdk from "@mdp/data-sdk";
import { dataContract } from "@mdp/data-sdk";
import { router } from "../src/generated/router.js";

it("serves exactly the grained marts in the data contract", () => {
  const metadata = z.object({ name: z.string(), grain: z.array(z.string()) });
  const served = Object.entries(sdk)
    .flatMap(([name, value]) => {
      if (!name.startsWith("metadata_")) return [];
      const mart = metadata.parse(value);
      return mart.grain.length && mart.name in dataContract ? [mart.name] : [];
    })
    .sort();
  expect(Object.keys(dataContract).sort()).toEqual(served);
  expect(Object.keys(router).sort()).toEqual(served);
  for (const [name, procedure] of Object.entries(router)) {
    expect(procedure["~orpc"].route.path).toBe(`/marts/${name}`);
  }
});
