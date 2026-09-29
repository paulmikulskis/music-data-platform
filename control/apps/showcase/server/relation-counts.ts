import { createHash } from "node:crypto";
import type postgres from "postgres";
import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
import { readRunnerState } from "@mdp/contracts/runner-state";
import inventory from "../../../../ops/showcase/queries.json" with { type: "json" };
import { budget } from "./read-budget.js";

export const buildStamp = z.object({
  relation: z.string(),
  cycle_id: z.uuid(),
  close_no: z.coerce.string().nullable(),
  // PostgreSQL stamps have microseconds. Keep them in the key even within one millisecond.
  built_at: z.iso.datetime({ offset: true }).transform((value) => {
    const fraction = /\.(\d+)/.exec(value)?.[1] ?? "";
    return (
      new Date(value).toISOString().slice(0, 19) +
      "." +
      fraction.padEnd(6, "0") +
      "Z"
    );
  }),
});
export type BuildStamp = z.infer<typeof buildStamp>;
export function relationBuildKey(stamp: BuildStamp) {
  return JSON.stringify([
    stamp.relation,
    stamp.cycle_id,
    stamp.close_no,
    stamp.built_at,
  ]);
}
const review = z.object({ relation: z.string(), columns: z.array(z.string()) });
// The query review is the allowlist. No raw or tenant relation is eligible.
export const countInputs = [
  ...new Set(
    review
      .array()
      .parse(inventory)
      .map((row) => row.relation),
  ),
]
  .filter((relation) =>
    /^(marts|intermediate|staging)\.[a-z][a-z0-9_]*$/.test(relation),
  )
  .sort()
  .map((relation) => {
    const projection = relation.startsWith("marts.")
      ? relation
      : `explore_${relation}`;
    const columns = [
      ...new Set(
        review
          .array()
          .parse(inventory)
          .filter((row) => row.relation === relation)
          .flatMap((row) => row.columns),
      ),
    ].sort();
    const filter = "true";
    const input_hash = createHash("sha256")
      .update(
        JSON.stringify({
          relation,
          projection,
          columns,
          filter,
          parameters: [],
        }),
      )
      .digest("hex");
    return { relation, projection, columns, filter, input_hash };
  });
export type CountInput = (typeof countInputs)[number];
type Connections = {
  control: postgres.Sql;
  warehouse: Pick<postgres.Sql, "begin">;
};

export async function currentBuilds(
  wh: Connections["warehouse"],
  inputs = countInputs,
) {
  if (!inputs.length) return [];
  return wh.begin("read only", async (tx) => {
    await tx.unsafe("SET LOCAL statement_timeout='2s'");
    // Missing or unreadable relations stay unknown; one missing table must not hide the others.
    const found = await tx<
      { projection: string }[]
    >`SELECT relation AS projection FROM unnest(${inputs.map((row) => row.projection)}::text[]) r(relation)
      WHERE to_regclass(relation) IS NOT NULL AND has_table_privilege(current_user,to_regclass(relation),'SELECT')`;
    if (!found.length) return [];
    const stamps =
      await tx`SELECT catalog.snapshot_stamp(relation) AS stamp FROM unnest(${found.map((row) => row.projection)}::text[]) r(relation)`;
    return z
      .array(z.object({ stamp: buildStamp.nullable() }))
      .parse(encodeWire(stamps))
      .flatMap((row) => (row.stamp ? [row.stamp] : []));
  });
}

// A relation swap and its stamp commit together. Lock first, then read both in one snapshot.
export async function exactCount(
  wh: Connections["warehouse"],
  input: CountInput,
  expected: BuildStamp,
) {
  return wh.begin("isolation level repeatable read read only", async (tx) => {
    await tx.unsafe("SET LOCAL statement_timeout='4s'");
    await tx.unsafe("SET LOCAL lock_timeout='1s'");
    await tx.unsafe(`LOCK TABLE ${input.projection} IN ACCESS SHARE MODE`);
    const stamps =
      await tx`SELECT catalog.snapshot_stamp(${input.projection}) AS stamp`;
    const stamp = z
      .object({ stamp: buildStamp.nullable() })
      .parse(encodeWire(stamps[0])).stamp;
    if (!stamp || relationBuildKey(stamp) !== relationBuildKey(expected))
      return null;
    const counted = await tx.unsafe(
      `SELECT count(*)::text AS row_count FROM ${input.projection} WHERE ${input.filter}`,
    );
    const { row_count } = z
      .object({ row_count: z.string().regex(/^\d+$/) })
      .parse(counted[0]);
    return { stamp, row_count, captured_at: new Date().toISOString() };
  });
}

type SkipReason =
  | "missing_or_unstamped"
  | "cycle_not_closed"
  | "capture_locked"
  | "already_captured"
  | "build_changed"
  | "capture_failed";
type CaptureReport = {
  outcome: "captured" | "deferred" | "skipped";
  captured: number;
  reason?: string;
  stage: string;
  skipped: { relation: string; reason: SkipReason }[];
};
function logCapture(report: CaptureReport) {
  console.info(
    JSON.stringify({
      event: "showcase_relation_count_capture",
      ...report,
      next_step:
        "Open /ops to check the runner and source details. The next pass retries in one minute.",
    }),
  );
}

export async function captureRelationCounts(
  { control, warehouse }: Connections,
  inputs = countInputs,
) {
  const report: CaptureReport = {
    outcome: "skipped",
    captured: 0,
    stage: "runner",
    skipped: [],
  };
  const defer = (reason: string) => {
    report.outcome = "deferred";
    report.reason = reason;
    return report;
  };
  try {
    const runner = await budget.run("light", () => readRunnerState(control));
    budget.setRunner(runner.state);
    if (runner.state !== "idle") return defer(`runner_${runner.state}`);
    report.stage = "warehouse";
    const [production] = await budget.run(
      "light",
      () => control<{ id: string; database: string }[]>`
        SELECT id,database FROM control.warehouse WHERE is_production`,
    );
    if (!production) return defer("warehouse_missing");
    const matches = await budget.run("light", () =>
      warehouse.begin("read only", async (tx) => {
        const [row] = await tx<{ database: string }[]>`
          SELECT current_database() AS database`;
        return row?.database === production.database;
      }),
    );
    if (!matches) return defer("warehouse_mismatch");
    report.stage = "stamps";
    const stamps = await budget.run("light", () =>
      currentBuilds(warehouse, inputs),
    );
    for (const input of inputs) {
      if (!stamps.some((stamp) => stamp.relation === input.relation)) {
        report.skipped.push({
          relation: input.relation,
          reason: "missing_or_unstamped",
        });
      }
    }
    for (const stamp of stamps) {
      const input = inputs.find((row) => row.relation === stamp.relation);
      if (!input) continue;
      report.stage = "cycle";
      // A close alone never schedules a capture: a committed relation stamp is also required.
      const [closed] = await budget.run(
        "light",
        () => control`SELECT 1 FROM control.cycle WHERE id=${stamp.cycle_id} AND scope='global'
          AND status='closed' AND closed_at <= ${stamp.built_at}`,
      );
      if (!closed) {
        report.skipped.push({
          relation: input.relation,
          reason: "cycle_not_closed",
        });
        continue;
      }
      report.stage = "runner";
      const state = await budget.run("light", () => readRunnerState(control));
      budget.setRunner(state.state);
      if (budget.runnerBusy) return defer(`runner_${budget.runnerState}`);
      report.stage = "count";
      try {
        const result = await budget.run("heavy", async () => {
          if (budget.runnerBusy) return "runner_changed";
          return control.begin(async (tx) => {
            const key = relationBuildKey(stamp);
            const [lock] = await tx<{ locked: boolean }[]>`
              SELECT pg_try_advisory_xact_lock(hashtextextended(${JSON.stringify([production.id, input.relation, key])},0)) AS locked`;
            if (!lock?.locked) return "capture_locked";
            const existing =
              await tx`SELECT 1 FROM control.showcase_relation_count
              WHERE warehouse_id=${production.id} AND relation=${input.relation} AND build_key=${key}`;
            if (existing.length) return "already_captured";
            const capture = await exactCount(warehouse, input, stamp);
            if (!capture) return "build_changed";
            const inserted =
              await tx`INSERT INTO control.showcase_relation_count(warehouse_id,relation,build_key,captured_at,row_count,latest_at,basis,input_hash)
              VALUES (${production.id},${input.relation},${key},${capture.captured_at},${capture.row_count},NULL,'exact',${input.input_hash})
              ON CONFLICT (warehouse_id,relation,build_key) DO NOTHING RETURNING 1`;
            return inserted.length ? "captured" : "already_captured";
          });
        });
        if (result === "runner_changed")
          return defer(`runner_${budget.runnerState}`);
        if (result === "captured") {
          report.captured++;
          report.outcome = "captured";
        } else {
          report.skipped.push({ relation: input.relation, reason: result });
        }
      } catch {
        // A timeout or replacement publishes nothing. Do not log SQL, row values or errors.
        report.skipped.push({
          relation: input.relation,
          reason: "capture_failed",
        });
      }
    }
    report.stage = "complete";
    return report;
  } catch {
    return defer("read_failed");
  } finally {
    logCapture(report);
  }
}

export function scheduleRelationCounts(
  task: () => Promise<unknown>,
  intervalMs = 60000,
) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const run = async () => {
    try {
      await task();
    } catch {
      logCapture({
        outcome: "deferred",
        captured: 0,
        reason: "setup_failed",
        stage: "setup",
        skipped: [],
      });
    }
    if (!stopped) {
      timer = setTimeout(() => void run(), intervalMs);
      timer.unref?.();
    }
  };
  void run();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
