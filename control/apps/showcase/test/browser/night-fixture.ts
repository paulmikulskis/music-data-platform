import { platformNight } from "@mdp/contracts/platform";
import { cycle } from "./fixtures";
export function nightFixture(input: { since: string; until: string }) {
  const at = (minute: number) =>
    new Date(
      Math.min(
        Date.parse(input.until) - 1,
        Date.parse(input.since) + minute * 60000,
      ),
    ).toISOString();
  const warehouse = "00000000-0000-4000-8000-000000000005";
  const coverage = {
    frozen_membership: 48,
    eligible: 48,
    succeeded: 31,
    skipped: 0,
    unit: "playlist",
    evidence_at: at(60),
  };
  return platformNight.parse({
    window: { ...input, queried_at: new Date().toISOString() },
    warehouse_id: warehouse,
    runs: [
      {
        run_id: "00000000-0000-4000-8000-000000000061",
        source_key: "am_playlist",
        kind: "bronze",
        scope: "global",
        cycle_id: cycle,
        warehouse_id: warehouse,
        admitted_at: at(15),
        status: "partial",
        targets: coverage,
        outputs: { dump_count: "2", rows_landed: "40" },
        attempts: [
          {
            attempt_no: 1,
            started_at: at(15),
            ended_at: at(60),
            status: "partial",
            trigger: "scheduled",
            trigger_evidence: {
              dbt_run_id: "fixture-night",
              reason_category: "scheduled",
              runner: "core",
            },
            targets: coverage,
          },
        ],
      },
      {
        run_id: "00000000-0000-4000-8000-000000000062",
        source_key: "sp_playlist",
        kind: "bronze",
        scope: "global",
        cycle_id: cycle,
        warehouse_id: warehouse,
        admitted_at: at(85),
        status: "failed",
        targets: { ...coverage, succeeded: 0 },
        outputs: { dump_count: "0", rows_landed: "0" },
        attempts: [
          {
            attempt_no: 1,
            started_at: at(85),
            ended_at: at(90),
            status: "failed",
            trigger: "unknown",
            trigger_evidence: {
              dbt_run_id: null,
              reason_category: null,
              runner: null,
            },
            targets: { ...coverage, succeeded: 0 },
          },
        ],
      },
    ],
    coverage: [
      {
        source_key: "am_playlist",
        succeeded: 31,
        unit: "playlist",
        evidence_at: at(60),
      },
    ],
    closes: [],
    alerts: [],
    runner: {
      state: "idle",
      busy: false,
      sessions: [],
      next_scheduled_at: null,
      next_step: "Open /runs.",
    },
    next_due: null,
    next_step: "Open source details.",
  });
}
