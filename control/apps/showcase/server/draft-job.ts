import "server-only";
import type { Sql } from "postgres";
import { readRunnerState } from "@mdp/contracts/runner-state";
import { callWeek } from "../lib/calls";
import { controlStore } from "./clients";
import { closeDraft } from "./draft-store";
import { ensureDraft } from "./draft-reads";
import { budget } from "./read-budget";

export async function closeDueDrafts(db: Sql) {
  // Each close has its own transaction lock. Two app timers cannot publish twice.
  const due =
    await db`SELECT week_start::text FROM control.showcase_draft WHERE closed_at IS NULL AND closes_at<=now() ORDER BY week_start`;
  for (const row of due)
    await closeDraft(
      db,
      row.week_start,
      `scheduled:${row.week_start}`,
      "scheduler",
    );
}
export function scheduleDraft(tick: () => Promise<void>, interval = 60000) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const run = async () => {
    try {
      await tick();
    } catch {
      console.error(
        "Weekly picks need another try. Open /songs?view=friday and choose Finish weekly picks.",
      );
    } finally {
      if (!stopped) {
        timer = setTimeout(() => void run(), interval);
        timer.unref?.();
      }
    }
  };
  void run();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
export function startDraftJob() {
  globalThis.showcaseDraftStop ??= scheduleDraft(async () => {
    // Close needs only the durable frozen tray, so warehouse downtime never blocks it.
    await budget.run("light", () => closeDueDrafts(controlStore()));
    const runner = await budget.run("light", () =>
      readRunnerState(controlStore()),
    );
    budget.setRunner(runner.state);
    await ensureDraft(callWeek());
    await budget.run("light", () => closeDueDrafts(controlStore()));
  });
}
declare global {
  var showcaseDraftStop: (() => void) | undefined;
}
