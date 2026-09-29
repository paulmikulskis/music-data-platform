import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type postgres from "postgres";
import { createRouterClient } from "@orpc/server";
import { isolatedControl } from "./isolated-control.js";

let db: postgres.Sql;
const service = vi.hoisted(() => vi.fn());
vi.mock("../src/db.js", async original => ({ ...await original<typeof import("../src/db.js")>(), database: () => db }));
vi.mock("../src/service.js", async original => ({ ...await original<typeof import("../src/service.js")>(), service }));
import { drainTargetProbes } from "../src/target-probe-worker.js";
import { pendingProbeTargets } from "../src/target-probe.js";
import { router } from "../src/router.js";

const url = process.env.MDP_STATUS_TEST_URL;
describe.skipIf(!url)("generic promoter import", () => {
  let close: (() => Promise<void>) | undefined;
  beforeAll(async () => { ({ db, close } = await isolatedControl(url!)); });
  afterAll(async () => close?.());
  it("queues a probe without calling the network on the import path", async () => {
    service.mockRejectedValue(new Error("probe service unavailable"));
    const identity = { actor: "probe-import-test", admin: false, promoter: true, tenant_id: null, tenant_slug: null };
    const client = createRouterClient(router, { context: { identity, db } });
    const set = await client.targets.createSet({ kind: "playlist", name: "probe fixture", tenant_id: null });
    const imported = await client.targets.importTargets({ target_set_id: set.id, dry_run: false,
      csv: "platform,platform_account_id\nspotify,fixture-playlist\n" });
    expect(service).not.toHaveBeenCalled();
    expect(imported.rows[0]).toMatchObject({ probe: "not_probed", resolution_status: "pending" });
    const pending = await db`SELECT subject FROM control.audit_log WHERE action='targets.probePending'`;
    const id = String(imported.rows[0]!.id);
    expect(pending.map(row => row.subject)).toEqual([id]);
    expect(await pendingProbeTargets(db)).toEqual([]); // Resolution still belongs to the caller.
    await db`UPDATE control.target SET resolution_status='resolved',activated_at=now() WHERE id=${id}`;
    await drainTargetProbes(); // Unavailable checks remain queued and never undo activation.
    expect(await pendingProbeTargets(db)).toEqual([{id,seed:false}]);
    service.mockResolvedValue({results:[{id,status:"ok",http_status:200,source_key:"fixture"}]});
    await drainTargetProbes();
    expect(await pendingProbeTargets(db)).toEqual([]);
    expect((await db`SELECT activated_at FROM control.target WHERE id=${id}`)[0]?.activated_at).not.toBeNull();
  });
});
