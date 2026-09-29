import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { createRouterClient } from "@orpc/server";
import type postgres from "postgres";
import { isolatedControl } from "./isolated-control.js";
import * as database from "../src/db.js";
import { router } from "../src/router.js";
import { attention } from "../src/page-data.js";
import { knobs, knobsUsage } from "../../../packages/mdp-cli/src/index.js";

const identity = { actor: "api-key:fixture-operator", admin: true, tenant_id: null, tenant_slug: null };
let db: postgres.Sql;
let close: (() => Promise<void>) | undefined;
const client = () => createRouterClient(router, { context: { identity, db } });

beforeAll(async () => {
  if (process.env.MDP_STATUS_TEST_URL) {
    ({ db, close } = await isolatedControl(process.env.MDP_STATUS_TEST_URL));
    vi.spyOn(database, "database").mockImplementation(() => db);
  }
}, 30000);
afterEach(() => vi.restoreAllMocks());
afterAll(async () => close?.(), 30000);

describe.skipIf(!process.env.MDP_STATUS_TEST_URL)("alerts and operator commands", () => {
  it("counts open and acknowledged alerts separately in every attention read", async () => {
    await db`INSERT INTO control.alert(class,severity,subject_type,subject_id,acknowledged_by,resolved_at)
      VALUES ('partial_coverage','warning','run','open',NULL,NULL),
      ('vendor_4xx','warning','run','quiet','fixture',NULL),
      ('partial_coverage','warning','run','resolved',NULL,now()),
      ('stale_target','warning','target','quiet-target','fixture',NULL)`;
    await db`SET ROLE control_rt`;
    try {
      expect((await attention(db)).map(a => a.subject_id)).toEqual(["open"]);
      expect((await attention(db, false, true)).map(a => a.subject_id).sort()).toEqual(["quiet", "quiet-target"]);
      expect((await attention(db, true)).map(a => a.subject_id)).toEqual(["resolved"]);
      expect((await client().alerts.list({})).map(a => a.subject_id)).toEqual(["open"]);
      expect((await client().alerts.list({ acknowledged: true })).map(a => a.subject_id).sort()).toEqual(["quiet", "quiet-target"]);
      const weekly = await client().screen.weekly({});
      expect(weekly).toMatchObject({ open_alerts: "1", acknowledged_alerts: "2", stale_targets: "0" });
      const status = await client().status({});
      expect(status.alerts.map(a => [a.class, a.count])).toEqual([["partial_coverage", "1"]]);
    } finally {
      await db`RESET ROLE`;
    }
  });

  it("omits the generic audit row for an empty parking pass", async () => {
    vi.spyOn(database, "database").mockImplementation(() => db);
    await db`SET ROLE control_rt`;
    try {
      const before = await db`SELECT id FROM control.audit_log WHERE action='targets.parkStale'`;
      expect(await client().targets.parkStale({})).toEqual({ parked: [], warnings: [] });
      expect(await db`SELECT id FROM control.audit_log WHERE action='targets.parkStale'`).toEqual(before);
    } finally {
      await db`RESET ROLE`;
    }
  });

  it("sets knobs through the CLI as the key holder and preserves omitted settings", async () => {
    vi.spyOn(database, "database").mockImplementation(() => db);
    vi.spyOn(console, "log").mockImplementation(() => {});
    await db`INSERT INTO control.streamline(source_key,layer) VALUES ('fixture_knobs','bronze')`;
    await db`SET ROLE control_rt`;
    try {
      await knobs(client(), "set", ["fixture_knobs", "--enable", "--batch-size", "5", "--max-concurrency", "2"]);
      await knobs(client(), "set", ["fixture_knobs", "--disable"]);
      expect((await db`SELECT enabled,batch_size,max_concurrency FROM control.streamline WHERE source_key='fixture_knobs'`)[0])
        .toMatchObject({ enabled: false, batch_size: 5, max_concurrency: 2 });
      const audits = await db`SELECT actor,after->>'state' AS state FROM control.audit_log WHERE action='streamlines.patchKnobs'`;
      expect(audits).toHaveLength(2);
      expect(audits.every(a => a.actor === identity.actor && a.state === "succeeded")).toBe(true);
      const reader = createRouterClient(router, { context: { identity: { ...identity, admin: false }, db } });
      await expect(knobs(reader, "set", ["fixture_knobs", "--enable"])).rejects.toMatchObject({ status: 403 });
    } finally {
      await db`RESET ROLE`;
    }
  });

  it.each([
    [], ["fixture_knobs"], ["fixture_knobs", "--enable", "--disable"],
    ["fixture_knobs", "--enable", "--batch-size", "0"],
    ["fixture_knobs", "--enable", "--max-concurrency", "1.5"],
    ["fixture_knobs", "--enable", "--batch-size"],
    ["fixture_knobs", "--enable", "--unknown"],
  ])("refuses invalid knobs with the command to use: %j", async (...args) => {
    await expect(knobs(client(), "set", args)).rejects.toThrow(knobsUsage);
  });
});
