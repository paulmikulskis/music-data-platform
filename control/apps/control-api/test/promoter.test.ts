import { describe, it, expect, afterAll } from "vitest";
import { randomUUID, createHash } from "node:crypto";
import { createRouterClient } from "@orpc/server";
import { router } from "../src/router.js";
import postgres from "postgres";
import { database, required } from "../src/db.js";
import { authenticate } from "../src/auth.js";
import { app } from "../src/app.js";

// The mdp-functions promoters' key (role 'promoter') reaches only the target commands ControlTargets
// calls; every other procedure and page needs an admin identity.
const promoter = { actor: "api-key:promoter-test", tenant_id: null, tenant_slug: null, admin: false, promoter: true };

describe("promoter identity", () => {
  it("is refused before any read outside the target commands", async () => {
    // A refused read never queries, so the client never connects (a refused write records its
    // refusal in the audit log; the database suite below covers those).
    process.env.MDP_CONTROL_RT_URL ??= "postgresql://unused@127.0.0.1:1/unused";
    const client = createRouterClient(router, { context: { identity: promoter, db: database() } });
    await expect(client.tenants.list({})).rejects.toMatchObject({ status: 403 });
    await expect(client.apiKeys.list({})).rejects.toMatchObject({ status: 403 });
    await expect(client.streamlines.list({})).rejects.toMatchObject({ status: 403 });
  });
});

const enabled = process.env.MDP_CONTROL_INTEGRATION === "1";
describe.skipIf(!enabled)("promoter key on the isolated database", () => {
  const secret = randomUUID();
  const hash = createHash("sha256").update(secret).digest("hex");
  const admin = { actor: "promoter-test-admin", tenant_id: null, tenant_slug: null, admin: true };
  afterAll(async () => {
    if (!enabled) return;
    await database()`DELETE FROM control.api_key WHERE key_hash=${hash}`;
    await database().end();
  });
  it("authenticates as a promoter and reaches the target commands only, on global and tenant track sets", async () => {
    await database()`INSERT INTO control.api_key(key_hash,label,role) VALUES (${hash},'promoter-test','promoter')`;
    const identity = await authenticate(new Request("http://localhost/api/targets/sets", { headers: { "x-api-key": secret } }));
    expect(identity).toMatchObject({ admin: false, promoter: true, tenant_id: null });
    const headers = { "x-api-key": secret };
    expect((await app.request("/api/targets/sets", { headers })).status).toBe(200);
    // The paged listing is not the promoter's: it reads by key.
    expect((await app.request("/api/targets", { headers })).status).toBe(403);
    expect((await app.request("/ops", { headers })).status).toBe(403);
    expect((await app.request("/api/tenants", { headers })).status).toBe(403);
    const client = createRouterClient(router, { context: { identity, db: database() } });
    const operator = createRouterClient(router, { context: { identity: admin, db: database() } });
    await expect(client.streamlines.patchKnobs({ source_key: "fixture_accounts", enabled: false })).rejects.toMatchObject({ status: 403 });
    const tenant = await operator.tenants.create({ slug: `promoter-${randomUUID().slice(0, 8)}`, name: "Promoter tenant" });
    try {
      await expect(client.targets.createSet({ kind: "account", name: "No", tenant_id: tenant.id })).rejects.toMatchObject({ status: 403 });
      const subject = (await client.targets.listSets({})).find((set) => set.tenant_id === tenant.id && set.kind === "artist_page");
      expect(subject).toBeDefined();
      await expect(client.targets.importTargets({ target_set_id: subject!.id, csv: "platform,handle\nspotify,x", dry_run: false }))
        .rejects.toMatchObject({ status: 403 });
      await expect(client.targets.lookup({ target_set_id: subject!.id, platform: "spotify", platform_account_id: "x" }))
        .rejects.toMatchObject({ status: 403 });
      // tenants.create made its track set; the promoter reads and writes it.
      const tracks = (await client.targets.listSets({})).find((set) => set.tenant_id === tenant.id && set.kind === "track");
      expect(await client.targets.lookup({ target_set_id: tracks!.id, platform: "charts", platform_account_id: "9000143004909855216" })).toEqual([]);
    } finally {
      await database()`DELETE FROM control.target WHERE target_set_id IN (SELECT id FROM control.target_set WHERE tenant_id=${tenant.id})`;
      await database()`DELETE FROM control.target_set WHERE tenant_id=${tenant.id}`;
      await database()`DELETE FROM control.tenant WHERE id=${tenant.id}`.catch(() => undefined);
    }
  });

  it("activates a new target with its spec, moves only its own imports, and reactivates only its own deactivations", async () => {
    const identity = await authenticate(new Request("http://localhost/api/targets/sets", { headers: { "x-api-key": secret } }));
    const client = createRouterClient(router, { context: { identity, db: database() } });
    const operator = createRouterClient(router, { context: { identity: admin, db: database() } });
    // The promoter writes the global playlist set, never a test kind.
    await expect(client.targets.createSet({ kind: `test-${randomUUID()}`, name: "No", tenant_id: null })).rejects.toMatchObject({ status: 403 });
    const set = (await operator.targets.listSets({})).find((s) => s.kind === "playlist" && !s.tenant_id)
      ?? await operator.targets.createSet({ kind: "playlist", name: "Public playlist baseline", tenant_id: null });
    const tag = randomUUID().slice(0, 8);
    const created: string[] = [];
    const importAs = async (who: typeof client, handle: string) => {
      const [row] = (await who.targets.importTargets({ target_set_id: set.id, csv: `platform,handle\nspotify,${handle}`, dry_run: false })).rows;
      created.push(String(row!.id));
      return String(row!.id);
    };
    try {
      // A seeded list: the operator's import, with the seed's reason.
      const seedId = await importAs(operator, `seeded-${tag}`);
      await operator.targets.resolve({ id: seedId, platform_account_id: `US:seeded-${tag}` });
      await operator.targets.setSpec({ id: seedId, resource_kind: "playlist", canonical_key: `sp:playlist:US:seeded-${tag}`, params_json: {}, promotion_reason: "seed", activate: true });
      // The promoter's own list: import, resolve, then spec and activation in one call.
      const ownId = await importAs(client, `promoted-${tag}`);
      await client.targets.resolve({ id: ownId, platform_account_id: `US:promoted-${tag}` });
      await expect(client.targets.setSpec({ id: ownId, resource_kind: "playlist", canonical_key: "x", params_json: {} }))
        .rejects.toMatchObject({ status: 403 });
      const spec = await client.targets.setSpec({ id: ownId, resource_kind: "playlist", canonical_key: `sp:playlist:US:promoted-${tag}`, params_json: {}, promotion_reason: "subject_list_promote", activate: true });
      expect(spec.promotion_reason).toBe("subject_list_promote");
      const [found] = await client.targets.lookup({ target_set_id: set.id, platform: "spotify", platform_account_id: `US:promoted-${tag}` });
      expect(found).toMatchObject({ id: ownId, has_spec: true, promotion_reason: "subject_list_promote", promoter_import: true, person_deactivated: false, deactivated_at: null });
      expect(found!.activated_at).not.toBeNull();
      const [seedFound] = await client.targets.lookup({ target_set_id: set.id, platform: "spotify", platform_account_id: `US:seeded-${tag}` });
      expect(seedFound).toMatchObject({ promoter_import: false, promotion_reason: "seed" });
      // The seed is not the promoter's: no reasonless move, no respec, no move under its own reason.
      await expect(client.targets.bulkActivate({ ids: [seedId], active: false })).rejects.toMatchObject({ status: 403 });
      await expect(client.targets.setSpec({ id: seedId, resource_kind: "playlist", canonical_key: `sp:playlist:US:seeded-${tag}`, params_json: {}, promotion_reason: "subject_list_promote" }))
        .rejects.toMatchObject({ status: 409 });
      expect(await client.targets.bulkActivate({ ids: [seedId, ownId], active: false, promotion_reason: "subject_list_promote" })).toMatchObject([{ id: ownId }]);
      // Its own deactivation: it may reactivate, also after an edit-only patch by a person.
      await operator.targets.patch({ id: ownId, display_name: "Renamed by a person" });
      expect(await client.targets.bulkActivate({ ids: [ownId], active: true, promotion_reason: "subject_list_promote" })).toMatchObject([{ id: ownId }]);
      // A person's deactivation stands, through either command.
      await operator.targets.bulkActivate({ ids: [ownId], active: false });
      expect(await client.targets.bulkActivate({ ids: [ownId], active: true, promotion_reason: "subject_list_promote" })).toEqual([]);
      await operator.targets.patch({ id: ownId, active: true });
      await operator.targets.patch({ id: ownId, active: false });
      expect(await client.targets.bulkActivate({ ids: [ownId], active: true, promotion_reason: "subject_list_promote" })).toEqual([]);
      // A person's spec-less import is not the promoter's to resolve, respec or adopt.
      const looseId = await importAs(operator, `loose-${tag}`);
      await expect(client.targets.resolve({ id: looseId, platform_account_id: `US:loose-${tag}` })).rejects.toMatchObject({ status: 403 });
      await operator.targets.resolve({ id: looseId, platform_account_id: `US:loose-${tag}` });
      await expect(client.targets.bulkActivate({ ids: [looseId], active: true })).rejects.toMatchObject({ status: 403 });
      await expect(client.targets.setSpec({ id: looseId, resource_kind: "playlist", canonical_key: `sp:playlist:US:loose-${tag}`, params_json: {}, promotion_reason: "subject_list_promote", activate: true }))
        .rejects.toMatchObject({ status: 403 });
      // Its own spec-less import moves without a reason, but never back over a person's deactivation.
      const mineId = await importAs(client, `mine-${tag}`);
      await client.targets.resolve({ id: mineId, platform_account_id: `US:mine-${tag}` });
      expect(await client.targets.bulkActivate({ ids: [mineId], active: true })).toMatchObject([{ id: mineId }]);
      await operator.targets.bulkActivate({ ids: [mineId], active: false });
      const [mine] = await client.targets.lookup({ target_set_id: set.id, platform: "spotify", platform_account_id: `US:mine-${tag}` });
      expect(mine).toMatchObject({ promoter_import: true, has_spec: false, person_deactivated: true });
      await expect(client.targets.bulkActivate({ ids: [mineId], active: true })).rejects.toMatchObject({ status: 403 });
    } finally {
      // The global playlist set keeps them, inactive, like any retired target.
      if (created.length) await operator.targets.bulkActivate({ ids: created, active: false });
    }
  });

  it("shows a function's parked inputs while its inputs_parked alert is open, and only an operator releases them", async () => {
    const identity = await authenticate(new Request("http://localhost/api/targets/sets", { headers: { "x-api-key": secret } }));
    const client = createRouterClient(router, { context: { identity, db: database() } });
    const operator = createRouterClient(router, { context: { identity: admin, db: database() } });
    // What derived.parked_alert writes, on a streamline of the test's own: the read's input_snapshot
    // count and one open alert.
    const owner = postgres(required("MDP_CONTROL_ADMIN_URL").replace("/postgres?", "/control?"), { max: 1 });
    const key = `parked_${randomUUID().slice(0, 8)}`;
    await owner`INSERT INTO control.streamline(source_key,layer) VALUES (${key},'gold')`;
    const [run] = await owner`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status)
      SELECT 'invoke', ${"parked-test:" + randomUUID()}, 'global', s.id, (SELECT id FROM control.warehouse LIMIT 1), 'partial'
      FROM control.streamline s WHERE s.source_key=${key} RETURNING id`;
    try {
      // One transaction, as snapshot_inputs writes them.
      await owner.begin(async (tx) => {
        await tx`INSERT INTO control.run_event(run_id,level,event_type,message,attrs)
          VALUES (${run!.id},'info','input_snapshot','Pending inputs read in parts',${tx.json({ parked: 2 })})`;
        await tx`INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id)
          VALUES ('inputs_parked','info','run',${run!.id},${run!.id})`;
      });
      expect((await operator.streamlines.get({ source_key: key })).parked_inputs).toBe(2);
      expect((await operator.streamlines.list({})).find((st) => st.source_key === key)?.parked_inputs).toBe(2);
      await expect(client.streamlines.unpark({ source_key: key })).rejects.toMatchObject({ status: 403 });
      expect(await operator.streamlines.unpark({ source_key: key })).toEqual({ source_key: key, released_alerts: 1 });
      // Released: the count reads zero until a later read parks inputs again and opens a new alert.
      expect((await operator.streamlines.get({ source_key: key })).parked_inputs).toBe(0);
      expect(await operator.streamlines.unpark({ source_key: key })).toEqual({ source_key: key, released_alerts: 0 });
    } finally {
      await owner`DELETE FROM control.alert WHERE run_id=${run!.id}`;
      await owner`DELETE FROM control.run_event WHERE run_id=${run!.id}`;
      await owner`DELETE FROM control.run WHERE id=${run!.id}`;
      await owner`DELETE FROM control.streamline WHERE source_key=${key}`;
      await owner.end();
    }
  });

  it("proposes into a tenant track set: import and spec, never resolve or activate", async () => {
    const identity = await authenticate(new Request("http://localhost/api/targets/sets", { headers: { "x-api-key": secret } }));
    const client = createRouterClient(router, { context: { identity, db: database() } });
    const operator = createRouterClient(router, { context: { identity: admin, db: database() } });
    const tenant = await operator.tenants.create({ slug: `sounds-${randomUUID().slice(0, 8)}`, name: "Sound tenant" });
    try {
      const tracks = (await client.targets.listSets({})).find((set) => set.tenant_id === tenant.id && set.kind === "track")!;
      const [row] = (await client.targets.importTargets({ target_set_id: tracks.id, csv: "platform,platform_account_id,handle\nfixture,9000273170393910457,sound", dry_run: false })).rows;
      const id = String(row!.id);
      await client.targets.setSpec({ id, resource_kind: "track", canonical_key: "sp:track:0000000000000000000001", params_json: {}, promotion_reason: "fixture_track_promote" });
      await expect(client.targets.resolve({ id, platform_account_id: "9000273170393910457" })).rejects.toMatchObject({ status: 403 });
      await expect(client.targets.setSpec({ id, resource_kind: "track", canonical_key: "sp:track:0000000000000000000001", params_json: {}, promotion_reason: "fixture_track_promote", activate: true }))
        .rejects.toMatchObject({ status: 403 });
      await expect(client.targets.bulkActivate({ ids: [id], active: true, promotion_reason: "fixture_track_promote" })).rejects.toMatchObject({ status: 403 });
    } finally {
      await database()`DELETE FROM control.target_spec WHERE target_id IN (SELECT t.id FROM control.target t JOIN control.target_set s ON s.id=t.target_set_id WHERE s.tenant_id=${tenant.id})`;
      await database()`DELETE FROM control.target WHERE target_set_id IN (SELECT id FROM control.target_set WHERE tenant_id=${tenant.id})`;
      await database()`DELETE FROM control.target_set WHERE tenant_id=${tenant.id}`;
      await database()`DELETE FROM control.tenant WHERE id=${tenant.id}`.catch(() => undefined);
    }
  });
});
