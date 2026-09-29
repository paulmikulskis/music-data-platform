import { describe, it, expect } from "vitest";
import {
  bigintWire,
  decimalWire,
  timestampWire,
  encodeWire,
} from "../src/wire.js";
import {
  mart_chart_history,
  encode_mart_chart_history,
} from "../src/generated/marts.zod.js";
import { dataContract } from "../src/data-contract.js";
describe("D17 wire format", () => {
  it("preserves bigint precision and decimal scale", () => {
    expect(bigintWire.parse("9007199254740993")).toBe("9007199254740993");
    expect(bigintWire.safeParse(9007199254740993).success).toBe(false);
    expect(decimalWire.parse("123.4500")).toBe("123.4500");
    expect(encodeWire({ v: 9007199254740993n })).toEqual({
      v: "9007199254740993",
    });
  });
  it("normalizes server Date values to UTC and rejects non-UTC client timestamps", () => {
    expect(encodeWire(new Date("2026-01-01T01:00:00+01:00"))).toBe(
      "2026-01-01T00:00:00.000Z",
    );
    expect(timestampWire.safeParse("2026-01-01T01:00:00+01:00").success).toBe(
      false,
    );
  });
  it("takes nullable columns from the contract and rejects missing fields", () => {
    const required = { chart_name: "hot100", chart_week: "2026-01-01", chart_position: 1,
      learning_eligible: false, resale_permitted: false, source_keys: '["billboard_hot100"]' };
    const row = {
      ...Object.fromEntries(Object.keys(mart_chart_history.shape).map((k) => [k, null])),
      ...required,
    };
    expect(encode_mart_chart_history(row).track_title).toBeNull();
    expect(() => encode_mart_chart_history({ ...row, source_keys: null })).toThrow();
    expect(mart_chart_history.safeParse({}).success).toBe(false);
  });
  it("never accepts tenant identity as an input or filter", () => {
    const schema = dataContract.mart_chart_history["~orpc"].inputSchema;
    if (!schema) throw new Error("Missing schema");
    expect(schema.safeParse({ tenant_id: "injected" }).success).toBe(false);
    expect(
      schema.safeParse({ filters: { tenant_id: "injected" } }).success,
    ).toBe(false);
  });
});
