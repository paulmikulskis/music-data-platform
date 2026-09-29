import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import postgres from "postgres";
import { z } from "zod";
import { platformNight } from "@mdp/contracts";
import { nightSql } from "../src/platform-night.js";

const base = process.env.MDP_TENANTS_TEST_URL;
const evidence = process.env.MDP_NIGHT_EVIDENCE_DIR;
const until = "2026-09-28T00:00:00.000Z";
const repeat =
  "Run pnpm --dir control exec vitest run apps/control-api/test/platform-night-budget.test.ts.";
const fixtureId = (key: string) => {
  const hex = createHash("md5").update(`night-${key}`).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20)}`;
};
const baseline = readFileSync(
  new URL("./platform-night-audit-before.sql", import.meta.url),
  "utf8",
)
  .trim()
  .replace(/;$/, "");
const data = platformNight.pick({ runs: true, coverage: true, closes: true });

const planNode = z.object({
  "Node Type": z.string(),
  "Parent Relationship": z.string().optional(),
  "Subplan Name": z.string().optional(),
  "Relation Name": z.string().optional(),
  "CTE Name": z.string().optional(),
  "Index Name": z.string().optional(),
  "Index Cond": z.string().optional(),
  "Recheck Cond": z.string().optional(),
  Alias: z.string().optional(),
  Filter: z.string().optional(),
  get Plans(): z.ZodOptional<z.ZodArray<typeof planNode>> {
    return z.array(planNode).optional();
  },
});
type PlanNode = z.infer<typeof planNode>;

function walkPlan(
  node: PlanNode,
  ancestors: PlanNode[] = [],
): { node: PlanNode; ancestors: PlanNode[] }[] {
  return [
    { node, ancestors },
    ...(node.Plans ?? []).flatMap((child) =>
      walkPlan(child, [...ancestors, node]),
    ),
  ];
}

describe.skipIf(!base)("night query with daily runs and audit history", () => {
  const database = `night_budget_${randomUUID().replaceAll("-", "")}`;
  let root: postgres.Sql;
  let db: postgres.Sql;
  let reader: postgres.Sql;

  beforeAll(async () => {
    const url = new URL(base!);
    root = postgres(url.toString(), { onnotice: () => {} });
    await root.unsafe(`CREATE DATABASE ${database}`);
    url.pathname = `/${database}`;
    const ddl = execFileSync("pg_dump", [
      "--schema-only",
      "--schema=control",
      "--dbname",
      base!,
    ]);
    execFileSync(
      "psql",
      ["-X", "-v", "ON_ERROR_STOP=1", "--dbname", url.toString()],
      {
        input: ddl,
        stdio: ["pipe", "ignore", "pipe"],
      },
    );
    db = postgres(url.toString(), { max: 1, onnotice: () => {} });
    await db.unsafe(
      readFileSync(
        new URL("./platform-night-seed.sql", import.meta.url),
        "utf8",
      ),
    );
    url.username = "control_rt";
    url.password = "control_rt";
    reader = postgres(url.toString(), { max: 1, onnotice: () => {} });
    if (evidence) {
      mkdirSync(evidence, { recursive: true });
      const volumes = await db.unsafe(`SELECT
        (SELECT count(*)::int FROM control.run) AS runs,
        (SELECT count(*)::int FROM control.run_attempt) AS attempts,
        (SELECT count(*)::int FROM control.cycle) AS cycles,
        (SELECT count(*)::int FROM control.target_export_member) AS members,
        (SELECT count(*)::int FROM control.batch) AS batches,
        (SELECT count(*)::int FROM control.dump) AS dumps,
        (SELECT count(*)::int FROM control.load) AS loads,
        (SELECT count(*)::int FROM control.audit_log) AS audits,
        pg_relation_size('control.audit_log_probe_action_idx')::int AS probe_index_bytes`);
      const counts = z.array(z.record(z.string(), z.number())).parse(volumes);
      writeFileSync(
        join(evidence, "volumes.json"),
        JSON.stringify(counts[0], null, 2) + "\n",
      );
    }
  }, 60000);

  afterAll(async () => {
    await reader?.end({ timeout: 5 });
    await db?.end({ timeout: 5 });
    if (root) {
      await root.unsafe(`DROP DATABASE ${database} WITH (FORCE)`);
      await root.end();
    }
  });

  function params(hours: number) {
    return [
      new Date(Date.parse(until) - hours * 3600000).toISOString(),
      until,
      fixtureId("warehouse"),
    ];
  }

  async function payload(sql: string, hours: number) {
    const result = await reader.unsafe(
      `SELECT payload::text FROM (${sql}) result`,
      params(hours),
    );
    return z.array(z.object({ payload: z.string() })).parse(result)[0]!.payload;
  }

  it.each([1, 12, 24])(
    "returns byte-identical JSON for a %i-hour window",
    async (hours) => {
      const before = await payload(baseline, hours);
      const after = await payload(nightSql, hours);
      expect(after).toBe(before);
      const parsed = data.parse(JSON.parse(after));
      expect(parsed.runs.length).toBe(
        hours === 1 ? 32 : hours === 12 ? 413 : 830,
      );
      for (const excluded of [12, 13, 14]) {
        expect(
          parsed.runs.some(
            (row) => row.run_id === fixtureId(`run-${excluded}`),
          ),
        ).toBe(false);
      }
      expect(
        parsed.runs.some((row) => row.run_id === fixtureId("run-16")),
      ).toBe(true);
      if (hours === 24) expect(parsed.closes).toHaveLength(26);
      const run = (n: number) =>
        parsed.runs.find((row) => row.run_id === fixtureId(`run-${n}`))!;
      expect(run(1).targets.frozen_membership).toBeNull();
      expect(run(3)).toMatchObject({
        kind: "dbt",
        cycle_id: null,
        source_key: null,
      });
      expect(run(4).targets.eligible).toBeNull();
      expect(run(5).attempts).toEqual([]);
      expect(run(6).targets).toMatchObject({
        frozen_membership: 48,
        unit: null,
      });
      expect(run(6).attempts[0]?.targets).toEqual(run(6).targets);
      expect(run(7).attempts.every((attempt) => attempt.targets === null)).toBe(
        true,
      );
      expect(run(7).attempts.map((attempt) => attempt.status)).toContain(
        "failed",
      );
      expect(run(33).targets).toMatchObject({
        frozen_membership: 0,
        unit: null,
      });
      expect(
        parsed.coverage.find((row) => row.source_key === "apple_playlist")
          ?.succeeded,
      ).toBe(41);
      expect(
        parsed.coverage.find((row) => row.source_key === "sp_playlist")
          ?.succeeded,
      ).toBeNull();
      if (hours === 24) {
        expect(
          parsed.runs.some((row) =>
            row.attempts.some((attempt) => attempt.status === "superseded"),
          ),
        ).toBe(true);
      }
    },
    30000,
  );

  it("plans one probe audit read and finds closes by cycle", async () => {
    const result = await reader.unsafe(
      `EXPLAIN (FORMAT JSON) ${nightSql}`,
      params(24),
    );
    const parsed = z
      .array(z.object({ "QUERY PLAN": z.array(z.object({ Plan: planNode })) }))
      .parse(result);
    const plan = parsed[0]!["QUERY PLAN"][0]!.Plan;
    if (evidence) {
      writeFileSync(
        join(evidence, "plan-24h.json"),
        JSON.stringify(result[0]?.["QUERY PLAN"], null, 2) + "\n",
      );
    }
    const nodes = walkPlan(plan);
    const auditReads = nodes.filter(
      ({ node }) => node["Relation Name"] === "audit_log",
    );
    // EXPLAIN has no runtime loop counts. Check the producer's position instead.
    // A direct InitPlan materializes once; run loops can reuse its saved rows.
    expect(
      auditReads.filter(
        ({ node, ancestors }) =>
          node["Node Type"] === "Seq Scan" &&
          ancestors.some((parent) => parent["Node Type"] === "Nested Loop"),
      ),
      `Audit history must not be scanned inside run loops. ${repeat}`,
    ).toEqual([]);
    for (const name of ["probe_audits", "probe_run_ids", "probe_work_keys"]) {
      const producers = nodes.filter(
        ({ node }) => node["Subplan Name"] === `CTE ${name}`,
      );
      expect(producers, `Materialize ${name} once. ${repeat}`).toHaveLength(1);
      expect(producers[0]!.ancestors).toEqual([plan]);
      expect(producers[0]!.node["Parent Relationship"]).toBe("InitPlan");
    }
    expect(auditReads, `Read probe audit history once. ${repeat}`).toHaveLength(1);
    const auditRead = auditReads[0]!;
    expect(auditRead.node["Index Name"]).toBe("audit_log_probe_action_idx");
    expect(
      [...auditRead.ancestors, auditRead.node].some(
        (node) => node["Subplan Name"] === "CTE probe_audits",
      ),
    ).toBe(true);
    // The audit CTE feeds only the two shared exclusion sets, never a run subplan.
    const auditConsumers = nodes.filter(
      ({ node }) => node["CTE Name"] === "probe_audits",
    );
    expect(auditConsumers.length).toBeGreaterThan(0);
    for (const { ancestors } of auditConsumers) {
      expect(ancestors[1]?.["Subplan Name"]).toMatch(
        /^CTE probe_(run_ids|work_keys)$/,
      );
    }
    // Match the cycle alias from its scan, not a planner-assigned alias or subplan number.
    const closeLookups = nodes.filter(({ node, ancestors }) => {
      const cycle = ancestors.find(
        (parent) =>
          parent["Relation Name"] === "cycle" &&
          [parent.Filter, parent["Index Cond"], parent["Recheck Cond"]].some(
            (condition) => condition?.includes("closed_at"),
          ),
      );
      return (
        cycle?.Alias &&
        node["Index Name"] === "run_cycle_idx" &&
        node["Index Cond"] === `(cycle_id = ${cycle.Alias}.id)`
      );
    });
    expect(
      closeLookups,
      `Find close runs through their cycle index. ${repeat}`,
    ).toHaveLength(1);
    console.info(
      `24h plan: one probe audit read; closes use the cycle index. ${repeat}`,
    );
  });

  it("keeps a full-day read below the 1500 ms sanity limit", async () => {
    // Include planning, transfer and JSON decoding in the measured uncached service read.
    await reader.begin(async (tx) => {
      await tx.unsafe("SET LOCAL statement_timeout = '2s'");
      const start = performance.now();
      const result = await tx.unsafe(nightSql, params(24));
      const elapsed = performance.now() - start;
      expect(data.parse(result[0]?.payload).runs).toHaveLength(830);
      expect(elapsed).toBeLessThan(1500);
      const line = `24h uncached read: ${elapsed.toFixed(3)} ms. ${repeat}\n`;
      console.info(line.trim());
      if (evidence) writeFileSync(join(evidence, "budget.txt"), line);
    });
  }, 30000);
});
