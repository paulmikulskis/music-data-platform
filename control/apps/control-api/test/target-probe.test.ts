import { beforeEach, expect, it, vi } from "vitest";
const service = vi.hoisted(() => vi.fn());
vi.mock("../src/service.js", () => ({ service }));
import { AppError } from "../src/db.js";
import { probeActivation, probeImport, probeTargets } from "../src/target-probe.js";
const id = "00000000-0000-4000-8000-000000000001";
const unsafe = vi.fn(async (_query: string) => [{ id, kind: "chart", params_json: {} }]);
const db = { unsafe } as never;
beforeEach(() => vi.clearAllMocks());
it("reports a stale import without failing its activation path", async () => {
  service.mockResolvedValue({ results: [{ id, status: "stale_target", http_status: 404, source_key: "fixture" }] });
  const checked = await probeTargets(db, [id]);
  expect(checked.results[0]?.status).toBe("stale_target");
  service.mockClear();
  expect((await probeActivation(db, [id])).results[0]?.status).toBe("not_probed");
  expect(service).not.toHaveBeenCalled();
});
it("records not probed without waiting for an unavailable activation probe", async () => {
  service.mockRejectedValue(new AppError("service_unreachable", "fixture unavailable", 503));
  expect((await probeTargets(db, [id])).results[0]?.status).toBe("probe_unavailable");
  service.mockClear();
  expect((await probeActivation(db, [id])).results[0]?.status).toBe("not_probed");
  expect(service).not.toHaveBeenCalled();
  expect(unsafe).toHaveBeenCalledWith(expect.stringContaining("targets.probePending"), expect.anything());
});

it("queues every import without probing inline", async () => {
  expect((await probeImport(db, {dry_run:false,rows:[{id}]})).rows).toEqual([{id,probe:"not_probed"}]);
  expect(service).not.toHaveBeenCalled();
});
