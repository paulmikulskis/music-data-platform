import type postgres from "postgres";
import { readRunnerState } from "@mdp/contracts/runner-state";
import { budget } from "./read-budget.js";
import { readInventory } from "./inventory.js";

type Connections = {
  control: postgres.Sql;
  warehouse: Parameters<typeof readInventory>[0];
};
export async function captureInventory(
  { control, warehouse: wh }: Connections,
  now = new Date(),
) {
  try {
    const state = await budget.run("light", () => readRunnerState(control));
    budget.setRunner(state.state);
    if (state.state !== "idle") return "deferred";
    return await budget.run("heavy", async () => {
      if (budget.runnerBusy) return "deferred";
      return control.begin(async (tx) => {
        const [production] = await tx<
          { id: string }[]
        >`SELECT id FROM control.warehouse WHERE is_production`;
        if (!production)
          throw new Error("The production warehouse is missing. Open /ops.");
        const warehouseId = production.id;
        const [lock] = await tx<
          { locked: boolean }[]
        >`SELECT pg_try_advisory_xact_lock(hashtext('showcase_inventory_capture')) AS locked`;
        if (!lock?.locked) return "locked";
        const day = now.toISOString().slice(0, 10);
        // A second instance never replaces a capture made by the first one that day.
        const existing =
          await tx`SELECT 1 FROM control.showcase_inventory WHERE day=${day} AND warehouse=${warehouseId} LIMIT 1`;
        if (existing.length) return "recorded";
        const layers = await readInventory(wh);
        if (!layers.length)
          throw new Error(
            "No storage layers are available. Open /runbooks/showcase-inventory-failed.",
          );
        for (const layer of layers)
          await tx`
          INSERT INTO control.showcase_inventory(day,warehouse,layer,relations,rows_est,bytes,captured_at,complete)
          VALUES (${day},${warehouseId},${layer.layer},${layer.relations},${layer.rows_est},${layer.bytes},${now.toISOString()},${layer.complete})
          ON CONFLICT(day,warehouse,layer) DO UPDATE SET relations=EXCLUDED.relations,rows_est=EXCLUDED.rows_est,
          bytes=EXCLUDED.bytes,captured_at=EXCLUDED.captured_at,complete=EXCLUDED.complete`;
        return "captured";
      });
    });
  } catch {
    throw new Error(
      "Storage capture failed. Open /runbooks/showcase-inventory-failed.",
    );
  }
}
export function nextInventoryTime(now: Date) {
  const next = new Date(now);
  next.setUTCHours(0, 30, 0, 0);
  if (next.getTime() <= now.getTime()) next.setUTCDate(next.getUTCDate() + 1);
  return next;
}
export function scheduleInventory(
  task: () => Promise<string>,
  now = () => new Date(),
) {
  let timer: ReturnType<typeof setTimeout>;
  let stopped = false;
  const schedule = () => {
    if (stopped) return;
    const next = nextInventoryTime(now());
    timer = setTimeout(
      () => void run(next.toISOString().slice(0, 10)),
      next.getTime() - now().getTime(),
    );
    timer.unref?.();
  };
  const run = async (day: string) => {
    if (day !== now().toISOString().slice(0, 10)) {
      schedule();
      return;
    }
    try {
      if ((await task()) === "deferred") {
        if (!stopped) {
          timer = setTimeout(() => void run(day), 60000);
          timer.unref?.();
        }
        return;
      }
    } catch {
      console.error(
        "Storage capture needs attention. Open /runbooks/showcase-inventory-failed.",
      );
    }
    schedule();
  };
  schedule();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
