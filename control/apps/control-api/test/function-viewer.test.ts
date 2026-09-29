import { describe, expect, it } from "vitest";
import {
  functionVerdict,
  healthState,
  rejectLabel,
} from "../src/console-semantics.js";
import {
  groupFailures,
  rejectedByReason,
  functionTargetHealth,
} from "../src/console-data.js";
import { leadColumns } from "../src/page-data.js";
import { FailureBanner } from "../src/primitives.js";
import postgres from "postgres";
import { randomUUID } from "node:crypto";

const source = {
  enabled: true,
  source_key: "fixture_playlist",
  writes: ["raw.playlist_snapshots"],
};
const run = { id: "run", status: "succeeded", error_class: null };
const full = { succeeded: 36, total: 36, floor: 0.9, failures: [] };
describe("function verdict", () => {
  it.each([
    {
      status: "running",
      enabled: false,
      coverage: full,
      parked: 2,
      drift: 1,
      label: "Running",
      action: null,
    },
    {
      status: "failed",
      enabled: false,
      coverage: full,
      parked: 2,
      drift: 1,
      label: "Paused",
      action: "Resume",
    },
    {
      status: "failed",
      enabled: true,
      coverage: full,
      parked: 2,
      drift: 1,
      label: "Failing",
      action: "Retry run",
    },
    {
      status: "succeeded",
      enabled: true,
      coverage: {
        ...full,
        succeeded: 30,
        failures: [{ id: "target", name: "Fixture", reason: "HTTP 500" }],
      },
      parked: 2,
      drift: 1,
      label: "Needs a look",
      action: "Review 1 failed targets",
    },
    {
      status: "succeeded",
      enabled: true,
      coverage: full,
      parked: 2,
      drift: 1,
      label: "Needs a look",
      action: "Release 2 parked inputs",
    },
    {
      status: "succeeded",
      enabled: true,
      coverage: full,
      parked: 0,
      drift: 1,
      label: "Needs a look",
      action: "Review schema change",
    },
    {
      status: "succeeded",
      enabled: true,
      coverage: full,
      parked: 0,
      drift: 0,
      label: "Healthy",
      action: "See rows",
    },
  ])(
    "orders $label before lower priority states",
    ({ status, enabled, coverage, parked, drift, label, action }) => {
      expect(
        functionVerdict(
          { ...source, enabled },
          { ...run, status },
          coverage,
          parked,
          drift,
          [],
        ),
      ).toMatchObject({ label, action });
    },
  );
  it("offers a dry probe before the first run and a reviewed relation afterward", () => {
    expect(
      functionVerdict(source, undefined, undefined, 0, 0, []),
    ).toMatchObject({ label: "Not run yet", command: "probe" });
    expect(functionVerdict(source, run, full, 0, 0, []).href).toBe(
      "/workbench?relation=explore_raw.playlist_snapshots",
    );
    expect(
      functionVerdict(
        source,
        { ...run, status: "failed", error_class: "service_unreachable" },
        full,
        0,
        0,
        [],
      ).runbook,
    ).toBe("/runbooks/service-unreachable");
  });
});
it("chooses legible columns and labels rejects", () => {
  const columns = [
    { name: "_run_id", type: "uuid" },
    { name: "playlist_id", type: "text" },
    { name: "title", type: "text" },
    { name: "platform", type: "text" },
    { name: "followers", type: "bigint" },
    { name: "track_count", type: "integer" },
    { name: "position", type: "int" },
    { name: "payload", type: "jsonb" },
    { name: "observed_at", type: "timestamp" },
    { name: "content_hash", type: "text" },
  ];
  expect(leadColumns(columns)).toEqual([
    "title",
    "platform",
    "followers",
    "track_count",
    "observed_at",
  ]);
  expect(rejectLabel("unsupported_item:episode")).toBe(
    "Podcast episode, not a track",
  );
  expect(rejectLabel("new_reason")).toBe("New reason");
  expect(
    rejectedByReason([
      { reason: "validation_error:a" },
      { reason: "validation_error:b" },
    ]),
  ).toMatchObject([
    {
      code: "validation_error",
      count: 2,
      sample: { reason: "validation_error:a" },
    },
  ]);
});
it("groups repeated failures and hides diagnostics in Details", async () => {
  const failures = [1, 2].map((n) => ({
    source_key: "fixture_duration",
    error_class: "cadence_failed",
    title: "Failed",
    message:
      "Traceback plpy.Error: service_unreachable: Cannot reach service CONTEXT: compiled code at line 1",
    run_id: `run-${n}`,
    cycle_id: `cycle-${n}`,
    at: `2026-09-28T0${n}:00:00Z`,
    runbook: "/runbooks/cadence-failed",
  }));
  expect(groupFailures(failures)).toMatchObject([
    {
      count: 2,
      runbook: "/runbooks/service-unreachable",
      run_ids: ["run-1", "run-2"],
      cycle_ids: ["cycle-1", "cycle-2"],
    },
  ]);
  const html = await FailureBanner({ failures })!.toString();
  expect(html.match(/class="failure-group"/g)).toHaveLength(1);
  expect(html).toContain("Service unreachable × 2");
  expect(html.split("<details>")[0]).not.toMatch(
    /Traceback|plpy|compiled code at/,
  );
  expect(html).toContain('value="cycle-2"');
});
it("shows the invoke deadline and its catalog recovery guide", async () => {
  const html = String(
    await FailureBanner({
      failures: [
        {
          source_key: "fixture_duration",
          title: "Failed",
          error_class: "invoke_timeout",
          message:
            "plpy.Error: invoke_timeout: run fixture-run still running. Open /runs/fixture-run",
          run_id: "fixture-run",
          cycle_id: "open-cycle",
          runbook: "/runbooks/invoke-timeout",
        },
      ],
    }),
  );
  expect(html).toContain("fixture_duration · Invoke timeout");
  expect(html).toContain("The run did not finish before its deadline.");
  expect(html).toContain("/runbooks/invoke-timeout");
  expect(html).not.toContain("Service unreachable");
  expect(html.split("<details>")[0]).not.toContain("plpy");
});
it("keeps unchanged reads distinct", () => {
  expect(
    healthState({
      id: "target",
      name: "Fixture",
      active: true,
      resolved: true,
      http_status: 304,
      result: "unchanged",
      at: "2026-09-28",
    }),
  ).toBe("unchanged");
});

const url = process.env.MDP_TENANTS_TEST_URL;
describe.skipIf(!url)("scoped target health in Postgres", () => {
  it("keeps pending targets unread and ignores a newer superseded run", async () => {
    const db = postgres(url!, { onnotice: () => {} });
    try {
      await db
        .begin(async (tx) => {
          const key = `health_${randomUUID()}`;
          const [stream] =
            await tx`INSERT INTO control.streamline(source_key,layer,cadence_tag)
          VALUES (${key},'bronze','daily') RETURNING id`;
          const [warehouse] =
            await tx`SELECT id FROM control.warehouse LIMIT 1`;
          const [set] = await tx`INSERT INTO control.target_set(kind,name)
          VALUES (${key},'Fixture lists') RETURNING id`;
          const targets =
            await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at)
          SELECT ${set!.id},'spotify',name,'resolved',now()
          FROM unnest(ARRAY['pending','unchanged','failed','recovered']) name RETURNING id,platform_account_id`;
          const target = (name: string) =>
            String(targets.find((t) => t.platform_account_id === name)!.id);
          const [yesterday] =
            await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status,created_at)
          VALUES ('invoke',${`${key}:yesterday`},'global',${stream!.id},${warehouse!.id},'succeeded',now()-interval '1 day') RETURNING id`;
          const [today] =
            await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status,created_at)
          VALUES ('invoke',${`${key}:today`},'global',${stream!.id},${warehouse!.id},'running',now()-interval '1 hour') RETURNING id`;
          await tx`INSERT INTO control.batch(run_id,index,target_ids)
          VALUES (${yesterday!.id},0,${targets.map((t) => String(t.id))}),(${today!.id},0,${targets.map((t) => String(t.id))})`;
          await tx`INSERT INTO control.run_attempt(run_id,attempt_no,deadline_at)
          VALUES (${today!.id},1,now()),(${today!.id},2,now()+interval '1 hour')`;
          await tx`INSERT INTO control.call_ledger(run_id,target_id,vendor,endpoint,attempt,http_status,created_at) VALUES
          (${yesterday!.id},${target("pending")},'fixture','/fixture',1,200,now()-interval '1 day'),
          (${today!.id},${target("unchanged")},'fixture','/fixture',1,304,now()-interval '10 minutes'),
          (${today!.id},${target("failed")},'fixture','/fixture',1,200,now()-interval '10 minutes'),
          (${today!.id},${target("recovered")},'fixture','/fixture',1,500,now()-interval '10 minutes'),
          (${today!.id},${target("recovered")},'fixture','/fixture',2,200,now()-interval '1 minute')`;
          await tx`INSERT INTO control.dead_letter(run_id,streamline_id,target_id,reason,first_seen_at) VALUES
          (${yesterday!.id},${stream!.id},${target("unchanged")},'old failure',now()-interval '1 day'),
          (${today!.id},${stream!.id},${target("failed")},'current failure',now()-interval '5 minutes'),
          (${today!.id},${stream!.id},${target("recovered")},'first attempt failed',now()-interval '5 minutes')`;
          const before = await functionTargetHealth(
            tx,
            String(stream!.id),
            String(today!.id),
          );
          expect(
            before.rows.find((t) => t.id === target("pending")),
          ).toMatchObject({ state: "not-read", reason: "No request recorded" });
          expect(
            before.rows.find((t) => t.id === target("pending"))?.at,
          ).toBeUndefined();

          const [superseded] =
            await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status,created_at)
          VALUES ('invoke',${`${key}:superseded`},'global',${stream!.id},${warehouse!.id},'superseded',now()) RETURNING id`;
          await tx`INSERT INTO control.batch(run_id,index,target_ids) VALUES (${superseded!.id},0,${targets.map((t) => String(t.id))})`;
          await tx`INSERT INTO control.call_ledger(run_id,target_id,vendor,endpoint,attempt,http_status,created_at) VALUES
          (${superseded!.id},${target("pending")},'fixture','/fixture',1,200,now()),
          (${superseded!.id},${target("unchanged")},'fixture','/fixture',1,500,now())`;
          await tx`INSERT INTO control.dead_letter(run_id,streamline_id,target_id,reason,first_seen_at)
          VALUES (${superseded!.id},${stream!.id},${target("recovered")},'superseded failure',now())`;
          for (const selected of [String(today!.id), undefined]) {
            const health = await functionTargetHealth(
              tx,
              String(stream!.id),
              selected,
            );
            expect(health).toEqual(before);
            expect(health).toMatchObject({
              total: 4,
              read: 2,
              counts: { read: 1, unchanged: 1, failed: 1, "not-read": 1 },
            });
            expect(
              health.rows.find((t) => t.id === target("failed"))?.reason,
            ).toBe("current failure");
            expect(
              health.rows.find((t) => t.id === target("recovered"))?.state,
            ).toBe("read");
          }
          const old = await functionTargetHealth(
            tx,
            String(stream!.id),
            String(yesterday!.id),
          );
          expect(old.rows.find((t) => t.id === target("pending"))?.state).toBe(
            "read",
          );
          throw new Error("fixture rollback");
        })
        .catch((error) => {
          if (!(error instanceof Error) || error.message !== "fixture rollback")
            throw error;
        });
    } finally {
      await db.end();
    }
  });
  it("counts 304 as read and ignores another function's newer failure", async () => {
    const db = postgres(url!, { onnotice: () => {} });
    try {
      await db
        .begin(async (tx) => {
          const id = randomUUID();
          const streams =
            await tx`INSERT INTO control.streamline(source_key,layer,cadence_tag) VALUES (${`health_a_${id}`},'bronze','daily'),(${`health_b_${id}`},'bronze','daily') RETURNING id`;
          const warehouses = await tx`SELECT id FROM control.warehouse LIMIT 1`;
          const sets =
            await tx`INSERT INTO control.target_set(kind,name,tenant_id) VALUES (${`health_${id}`},'Fixture lists',null) RETURNING id`;
          const targets =
            await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) VALUES (${sets[0]!.id},'spotify',${id},'resolved',now()) RETURNING id`;
          const target = String(targets[0]!.id);
          const a = String(streams[0]!.id),
            b = String(streams[1]!.id);
          const first =
            await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status) VALUES ('invoke',${`a:${id}`},'global',${a},${warehouses[0]!.id},'succeeded') RETURNING id`;
          const second =
            await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status) VALUES ('invoke',${`b:${id}`},'global',${b},${warehouses[0]!.id},'failed') RETURNING id`;
          await tx`INSERT INTO control.batch(run_id,index,target_ids,status) VALUES (${first[0]!.id},0,ARRAY[${target}::uuid],'succeeded')`;
          await tx`INSERT INTO control.call_ledger(run_id,target_id,vendor,endpoint,attempt,http_status,created_at) VALUES (${first[0]!.id},${target},'fixture','/fixture',1,304,now()-interval '1 minute'),(${second[0]!.id},${target},'fixture','/fixture',1,500,now())`;
          const health = await functionTargetHealth(
            tx,
            a,
            String(first[0]!.id),
          );
          expect(health).toMatchObject({
            total: 1,
            read: 1,
            same_platform: 1,
            counts: { unchanged: 1, failed: 0 },
            rows: [{ id: target, platform: "spotify", state: "unchanged" }],
          });
          throw new Error("fixture rollback");
        })
        .catch((error) => {
          if (!(error instanceof Error) || error.message !== "fixture rollback")
            throw error;
        });
    } finally {
      await db.end();
    }
  });
});
