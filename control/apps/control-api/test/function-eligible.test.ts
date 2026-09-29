import { readFileSync } from "node:fs";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type postgres from "postgres";
import { z } from "zod";
import { functionPage, runDto } from "@mdp/contracts";
import { functionTargetHealth, runCoverage } from "../src/console-data.js";
import { functionPart, functionView } from "../src/function-view.js";
import { rows } from "../src/db.js";
import { isolatedControl } from "./isolated-control.js";

const url = process.env.MDP_TENANTS_TEST_URL;
let db: postgres.Sql;
let close: (() => Promise<void>) | undefined;
beforeAll(async () => {
  if (url) ({ db, close } = await isolatedControl(url));
});
afterAll(async () => close?.());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const template = z
  .object({ response: functionPage })
  .parse(
    JSON.parse(
      readFileSync(
        new URL(
          "../../../packages/contracts/test/fixtures/_v1_functions_source_key__get.json",
          import.meta.url,
        ),
        "utf8",
      ),
    ),
  ).response;

describe.skipIf(!url)("eligible function targets", () => {
  it.each(["sp_playlist", "fixture_reader"])(
    "%s counts only the 36 eligible targets in a shared 139-target batch",
    async (key) => {
      const rollback = new Error("fixture rollback");
      await db
        .begin(async (tx) => {
          const [stream] =
            await tx`INSERT INTO control.streamline(source_key,layer,cadence_tag)
          VALUES (${key},'bronze','daily') RETURNING id`;
          const [set] = await tx`INSERT INTO control.target_set(kind,name)
          VALUES ('playlist','Fixture playlists') RETURNING id`;
          const targets =
            await tx`INSERT INTO control.target(target_set_id,platform,platform_account_id,display_name,resolution_status,activated_at)
          SELECT ${set!.id},CASE WHEN n<=36 THEN 'spotify' WHEN n<=71 THEN 'apple'
            WHEN n<=105 THEN 'bandcamp' ELSE 'soundcloud' END,n::text,'Fixture '||n,'resolved',now()
          FROM generate_series(1,139) n RETURNING id,platform`;
          const eligible = targets
            .filter((t) => t.platform === "spotify")
            .map((t) => String(t.id));
          const skipped = targets
            .filter((t) => t.platform !== "spotify")
            .map((t) => String(t.id));
          const [run] =
            await tx`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status)
          SELECT 'invoke',${key},'global',${stream!.id},id,'succeeded'
          FROM control.warehouse WHERE is_production RETURNING id`;
          const id = String(run!.id);
          await tx`INSERT INTO control.batch(run_id,index,target_ids,status,cursor_checkpoint)
          VALUES (${id},0,${targets.map((t) => String(t.id))},'succeeded',
            ${tx.json({ completed_targets: [...eligible, ...skipped.map((t) => `skipped:${t}`)] })})`;
          await tx`INSERT INTO control.call_ledger(run_id,target_id,vendor,endpoint,attempt,http_status)
          SELECT ${id},target_id,'fixture','/fixture',1,200 FROM unnest(${eligible}::uuid[]) target_id`;
          const health = await functionTargetHealth(tx, String(stream!.id), id);
          expect(health).toMatchObject({
            total: 36,
            read: 36,
            counts: { read: 36, "not-read": 0 },
          });
          expect(health.rows).toHaveLength(36);
          expect(health.rows.every((t) => t.platform === "spotify")).toBe(true);
          expect((await runCoverage(tx, [id]))[id]).toMatchObject({
            succeeded: 36,
            total: 36,
          });

          const runs = await rows(
            tx,
            runDto,
            "SELECT * FROM control.run WHERE id=$1",
            [id],
          );
          vi.stubEnv("MDP_SERVICE_URL", "http://service.fixture");
          vi.stubEnv("MDP_SERVICE_TOKEN", "fixture");
          vi.stubGlobal(
            "fetch",
            vi.fn<typeof fetch>(async () =>
              Response.json({
                ...template,
                source_key: key,
                last_runs: runs,
                receipts: [],
                output_preview: [],
                rejected_sample: [],
              }),
            ),
          );
          const context = {
            db: tx,
            identity: {
              actor: "fixture",
              admin: true,
              staff: false,
              tenant_id: null,
              tenant_slug: null,
            },
          };
          const html = String(await functionView(context, key));
          expect(html).toContain("Read 36 of 36 targets");
          expect(html).toContain("All 36 →");
          expect(html).toContain('data-count="36">36 read');
          expect(html).not.toContain("not read yet");
          const strip = html.split('id="target-strip"')[1]!.split("</div>")[0]!;
          expect(strip.match(/role="gridcell"/g)).toHaveLength(36);
          expect(html.match(/data-platform="spotify"/g)).toHaveLength(36);
          expect(html).not.toMatch(
            /data-platform="(apple|bandcamp|soundcloud)"/,
          );
          const drilldown = String(await functionPart(context, key, "targets"));
          expect(drilldown.match(/data-target-row/g)).toHaveLength(36);
          expect(drilldown).toContain("Show all 36");
          for (const target of skipped)
            expect(drilldown).not.toContain(`/targets/${target}`);

          // Declared platforms exclude other readers before checkpoints arrive. A skipped
          // Spotify member still stays out, for example when its weekly bucket is not due.
          if (key === "sp_playlist") {
            await tx`UPDATE control.batch SET cursor_checkpoint=${tx.json({
              completed_targets: [...eligible, `skipped:${eligible[0]}`],
            })} WHERE run_id=${id}`;
            expect(
              await functionTargetHealth(tx, String(stream!.id), id),
            ).toMatchObject({ total: 35, read: 35 });
            expect((await runCoverage(tx, [id]))[id]).toMatchObject({
              total: 35,
              succeeded: 35,
            });
          }
          throw rollback;
        })
        .catch((error) => {
          if (error !== rollback) throw error;
        });
    },
  );
});
