import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from "vitest";
import postgres from "postgres";
import { randomUUID, createHash } from "node:crypto";
import { z } from "zod";
import { createRouterClient } from "@orpc/server";
import { referenceSourceState } from "@mdp/contracts";
import { ReferenceView, REFRESH_COMMAND } from "../src/reference-page.js";
import { Result } from "../src/pages.js";
import { router } from "../src/router.js";
import { database } from "../src/db.js";
import { authenticate } from "../src/auth.js";
import { app } from "../src/app.js";

const ago = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString();
const GiB = 1024 ** 3;
const nulls = {
  imported_generation: null, export_date: null, replication_sequence: null, imported_at: null,
  import_generation: null, import_state: null, import_phase: null, import_started_at: null,
  import_finished_at: null, import_message: null, landed_generation: null, landed_at: null,
  landed_reconciled: null, mirror_counts: null, landed_counts: null, disk_used_bytes: null,
  disk_total_bytes: null, probed_at: null, probe_error: null, reimport_requested_at: null,
  reimport_requested_by: null,
};
const populated = referenceSourceState.parse({
  source: "musicbrainz", imported_generation: "20260920-001", export_date: "2026-09-20T00:00:00.000Z",
  replication_sequence: "180000", imported_at: ago(3), import_generation: "20260920-001",
  import_state: "promoted", import_phase: "promote", import_started_at: ago(5), import_finished_at: ago(3),
  import_message: "Validated 2 tables", landed_generation: "20260913-001", landed_at: ago(170),
  landed_reconciled: false, mirror_counts: { artist: 2_500_000, recording: 35_000_000 },
  landed_counts: { artist: 2_499_990 }, disk_used_bytes: String(85 * GiB), disk_total_bytes: String(100 * GiB),
  probed_at: ago(0.1), probe_error: null, reimport_requested_at: null, reimport_requested_by: null,
  updated_at: ago(0.1),
  alerts: [{ id: randomUUID(), class: "reference_disk_high", severity: "warning", subject_type: "reference_source",
    subject_id: "musicbrainz", run_id: null, resolved_by: null, resolution_reason: null, acknowledged_by: null, runbook_slug: null, opened_at: ago(1), resolved_at: null }],
});
const neverProbed = referenceSourceState.parse({ source: "musicbrainz", ...nulls, updated_at: ago(0), alerts: [] });
const render = async (sources: z.infer<typeof referenceSourceState>[]) => (await ReferenceView({ sources })).toString();
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("reference page rendering", () => {
  it("renders a populated source: generations, lag, counts, import, disk threshold, alerts and re-import", async () => {
    const html = await render([populated]);
    expect(html).toContain("MusicBrainz");
    expect(html).toContain('data-copy-chip="20260920-001"');
    expect(html).toContain("2026-09-20 00:00 UTC");
    expect(html).toContain("Landed generation 20260913-001 is behind the mirror (20260920-001); the mirror import finished 3h ago.");
    expect(html).toContain("does not reconcile");
    expect(html).toContain("2,500,000");
    expect(html).toContain("2,499,990");
    expect(html).toContain("−10");
    expect(html).toContain("not landed");
    expect(html).toContain("85.0 GiB / 100.0 GiB");
    expect(html).toContain("85.0%");
    expect(html).toContain("at or above 80%");
    expect(html).toContain("promoted");
    expect(html).toContain('href="/runbooks/reference-disk-high"');
    expect(html).toContain('action="/actions/reimport"');
    expect(html).toContain('name="source" value="musicbrainz"');
    expect(html).toContain('name="back" value="/reference"');
    expect(html).toContain(REFRESH_COMMAND);
    expect(html).toContain('href="/reference" aria-current="page"');
  });
  it("renders a source whose probe has never run without errors", async () => {
    const html = await render([neverProbed]);
    for (const text of ["MusicBrainz", "No validated generation in the mirror. Request a re-import below.", "No row counts yet. Run the reference probe.",
      "No import recorded. Request a re-import below.", "Disk use unknown. Run the reference probe.", "No probe yet. Run POST /v1/reference/probe on the functions service.", "No open reference alerts.",
      'action="/actions/reimport"'])
      expect(html).toContain(text);
    expect(html).not.toContain("NaN");
  });
  it("shows the closure trigger's two figures and says when either passes its line", async () => {
    const below = await render([{ ...populated, closure_write_share: 0.124, closure_recordings: "42000", closure_measured_at: ago(20) }]);
    expect(below).toContain("12.4%");
    expect(below).toContain("42,000");
    expect(below).toContain("(line 30%)");
    expect(below).toContain("(line 100,000)");
    expect(below).toContain("Both below their lines");
    const past = await render([{ ...populated, closure_write_share: 0.31, closure_recordings: "1000", closure_measured_at: ago(20) }]);
    expect(past).toContain("31.0%");
    expect(past).toContain("ship the narrowed release step before the next import");
    const recordings = await render([{ ...populated, closure_write_share: 0.05, closure_recordings: "100001", closure_measured_at: ago(20) }]);
    expect(recordings).toContain("ship the narrowed release step before the next import");
    expect(await render([neverProbed])).toContain("Spine closure not measured. Run mb_spine to measure it.");
  });
  it("says when landed matches the mirror and renders no sources plainly", async () => {
    const html = await render([{ ...populated, landed_generation: populated.imported_generation, landed_reconciled: true }]);
    expect(html).toContain("Landed generation matches the mirror.");
    expect(html).toContain(">reconciled<");
    expect(await render([])).toContain("No reference sources are registered.");
  });
  it("summarizes a re-import result with its audit link", async () => {
    const auditId = randomUUID();
    const html = await Result({ result: { action: "reference.reimport", audit_id: auditId, state: "succeeded", result: { source: "musicbrainz" } } })!.toString();
    expect(html).toContain("Re-import requested for musicbrainz; the next refresh run picks it up.");
    expect(html).toContain(`/audit/${auditId}`);
  });
  it("refuses a foreign-origin re-import before authentication or any write", async () => {
    vi.stubEnv("MDP_AUTH_MODE", "dev"); vi.stubEnv("CLERK_SECRET_KEY", "");
    for (const [path, body, type] of [
      ["/actions/reimport", new URLSearchParams({ source: "musicbrainz", back: "/reference" }).toString(), "application/x-www-form-urlencoded"],
      ["/rpc/reference/reimport", JSON.stringify({ json: { source: "musicbrainz" } }), "application/json"],
    ] as const) {
      const response = await app.request(path, { method: "POST", body, headers: { origin: "https://invalid.example", "content-type": type } });
      expect(response.status).toBe(403);
      expect(await response.json()).toMatchObject({ error_class: "forbidden" });
    }
  });
});

const enabled = process.env.MDP_CONTROL_INTEGRATION === "1";
describe.skipIf(!enabled)("reference state and re-import on the isolated database", () => {
  const identity = { actor: "reference-test", admin: true, tenant_id: null, tenant_slug: null };
  const client = () => createRouterClient(router, { context: { identity, db: database() } });
  const suffix = randomUUID().replaceAll("-", "").slice(0, 10);
  // functions_rt cannot delete reference rows, so each run writes its own test_* sources.
  const full = `test_full_${suffix}`, blank = `test_blank_${suffix}`;
  let producer: ReturnType<typeof postgres>;
  beforeAll(async () => {
    const url = process.env.MDP_FUNCTIONS_TEST_URL;
    if (!url) throw new Error("Set MDP_FUNCTIONS_TEST_URL for the reference probe writer");
    producer = postgres(url, { max: 1 });
    await producer`INSERT INTO control.reference_source(source,imported_generation,export_date,imported_at,import_generation,import_state,
        import_phase,import_started_at,import_finished_at,import_message,landed_generation,landed_at,landed_reconciled,
        mirror_counts,landed_counts,disk_used_bytes,disk_total_bytes,probed_at,probe_error)
      VALUES (${full},'20260920-001','2026-09-20T00:00:00Z',now()-interval '3 hours','20260921-001','failed','validate',
        now()-interval '2 hours',now()-interval '1 hour','row counts did not validate','20260913-001',now()-interval '7 days',false,
        ${producer.json({ artist: 10, recording: 20 })},${producer.json({ artist: 9 })},${50 * GiB},${100 * GiB},now(),'mirror unreachable once')`;
    await producer`INSERT INTO control.reference_source(source) VALUES (${blank})`;
    await producer`INSERT INTO control.alert(class,severity,subject_type,subject_id) VALUES
      ('reference_import_failed','critical','reference_source',${full}),('reference_packet_failed','warning','reference_source',${full})`;
  });
  afterAll(async () => {
    await database()`UPDATE control.alert SET resolved_at=now() WHERE subject_type='reference_source' AND subject_id=${full}`;
    await producer.end();
    await database().end();
  });
  it("lists each source with only its open reference-class alerts, and pages both a populated and a never-probed row", async () => {
    const sources = await client().reference.list({});
    const row = sources.find((s) => s.source === full), empty = sources.find((s) => s.source === blank);
    expect(row).toMatchObject({ import_state: "failed", landed_reconciled: false, mirror_counts: { artist: 10, recording: 20 },
      disk_used_bytes: String(50 * GiB), probe_error: "mirror unreachable once" });
    expect(row?.alerts.map((a) => a.class)).toEqual(["reference_import_failed"]);
    expect(empty).toMatchObject({ ...nulls, source: blank, alerts: [] });
    vi.stubEnv("MDP_AUTH_MODE", "dev"); vi.stubEnv("CLERK_SECRET_KEY", "");
    const page = await app.request("/reference");
    expect(page.status).toBe(200);
    const html = await page.text();
    expect(html).toContain(`Test full ${suffix}`);
    expect(html).toContain('href="/runbooks/reference-import-failed"');
    expect(html).toContain("row counts did not validate");
    expect(html).toContain("Probe error: mirror unreachable once");
    expect(html).toContain(`Test blank ${suffix}`);
    expect(html).toContain("No probe yet. Run POST /v1/reference/probe on the functions service.");
    const api = await app.request("/api/reference");
    expect(api.status).toBe(200);
    expect(z.array(referenceSourceState).parse(await api.json()).some((s) => s.source === blank)).toBe(true);
  });
  it("records the re-import request and a succeeded audit row through the form, then shows the result", async () => {
    vi.stubEnv("MDP_AUTH_MODE", "dev"); vi.stubEnv("CLERK_SECRET_KEY", "");
    const response = await app.request("/actions/reimport", { method: "POST", body: new URLSearchParams({ source: full, back: "/reference" }).toString(),
      headers: { "content-type": "application/x-www-form-urlencoded" } });
    expect(response.status).toBe(303);
    const location = response.headers.get("location") ?? "";
    const auditId = z.uuid().parse(new URL(location, "http://local").searchParams.get("result"));
    expect(location.startsWith("/reference?result=")).toBe(true);
    const [state] = await database()`SELECT reimport_requested_at,reimport_requested_by,imported_generation FROM control.reference_source WHERE source=${full}`;
    expect(state?.reimport_requested_by).toBe("dev-user");
    expect(state?.reimport_requested_at).not.toBeNull();
    expect(state?.imported_generation).toBe("20260920-001");
    const [audit] = await database()`SELECT actor,action,subject,before,after FROM control.audit_log WHERE id=${auditId}`;
    expect(audit).toMatchObject({ actor: "dev-user", action: "reference.reimport", subject: full });
    expect(audit?.before[0]).toMatchObject({ source: full, reimport_requested_at: null });
    expect(audit?.after).toMatchObject({ state: "succeeded", subject_type: "reference_source", subject_id: full,
      input: { source: full }, result: { source: full, reimport_requested_by: "dev-user" } });
    const shown = await (await app.request(location)).text();
    expect(shown).toContain(`Re-import requested for ${full}`);
    expect(shown).toContain(`/audit/${auditId}`);
  });
  it("refuses a reader key on the router and over HTTP and leaves the request untouched", async () => {
    const secret = randomUUID(), hash = createHash("sha256").update(secret).digest("hex");
    await database()`INSERT INTO control.api_key(key_hash,label,role) VALUES (${hash},'reference-test','reader')`;
    try {
      const machine = await authenticate(new Request("http://localhost/reference", { headers: { "x-api-key": secret } }));
      expect(machine.admin).toBe(false);
      await expect(createRouterClient(router, { context: { identity: machine, db: database() } }).reference.reimport({ source: blank }))
        .rejects.toMatchObject({ status: 403 });
      const response = await app.request("/rpc/reference/reimport", { method: "POST", body: JSON.stringify({ json: { source: blank } }),
        headers: { "x-api-key": secret, "content-type": "application/json" } });
      expect(response.status).toBe(403);
      const [state] = await database()`SELECT reimport_requested_at FROM control.reference_source WHERE source=${blank}`;
      expect(state?.reimport_requested_at).toBeNull();
    } finally { await database()`DELETE FROM control.api_key WHERE key_hash=${hash}`; }
  });
  it("refuses a foreign origin without writing and audits an unknown source as failed", async () => {
    vi.stubEnv("MDP_AUTH_MODE", "dev"); vi.stubEnv("CLERK_SECRET_KEY", "");
    const response = await app.request("/actions/reimport", { method: "POST", body: new URLSearchParams({ source: blank }).toString(),
      headers: { origin: "https://invalid.example", "content-type": "application/x-www-form-urlencoded" } });
    expect(response.status).toBe(403);
    const [state] = await database()`SELECT reimport_requested_at FROM control.reference_source WHERE source=${blank}`;
    expect(state?.reimport_requested_at).toBeNull();
    const missing = `test_missing_${suffix}`;
    await expect(client().reference.reimport({ source: missing })).rejects.toMatchObject({ status: 404 });
    const [audit] = await database()`SELECT after FROM control.audit_log WHERE action='reference.reimport' AND subject=${missing} ORDER BY at DESC LIMIT 1`;
    expect(audit?.after).toMatchObject({ state: "failed", error_class: "not_found", subject_type: "reference_source" });
  });
});
