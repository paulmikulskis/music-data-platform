import "server-only";
import { required } from "@mdp/showcase-auth";
import { z } from "zod";
import { controlStore, warehouse } from "./clients";
import { captureInventory, scheduleInventory } from "./inventory-snapshot";
import {
  captureRelationCounts,
  scheduleRelationCounts,
} from "./relation-counts";
import { budget } from "./read-budget";
export async function sendInventoryAlert(warehouseId?: string) {
  const response = await fetch(
    new URL(
      "/v1/alerts/showcase_inventory_failed",
      required("MDP_SERVICE_URL"),
    ),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${required("MDP_SERVICE_TOKEN")}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ warehouse: warehouseId }),
      signal: AbortSignal.timeout(5000),
    },
  );
  if (!response.ok) {
    await response.arrayBuffer();
    throw new Error(
      "Storage alert could not be delivered. Open /runbooks/showcase-inventory-failed.",
    );
  }
  return z.object({ warehouse: z.uuid() }).parse(await response.json())
    .warehouse;
}
export function startScheduledInventory(
  capture = () =>
    captureInventory({ control: controlStore(), warehouse: warehouse() }),
  alert = sendInventoryAlert,
) {
  let subject: string | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let delay = 30000;
  let stopped = false;
  let sending = false;
  const report = async () => {
    if (stopped || sending || retry) return;
    sending = true;
    try {
      // Functions resolves the subject independently, even when capture cannot open control.
      subject = await budget.run("light", () => alert(subject));
      delay = 30000;
    } catch {
      console.error(
        "Storage alert delivery needs attention. Open /runbooks/showcase-inventory-failed.",
      );
      if (!stopped) {
        retry = setTimeout(() => {
          retry = undefined;
          void report();
        }, delay);
        retry.unref?.();
        delay = Math.min(delay * 2, 3600000);
      }
    } finally {
      sending = false;
    }
  };
  const stop = scheduleInventory(async () => {
    try {
      return await capture();
    } catch (error) {
      await report();
      throw error;
    }
  });
  return () => {
    stopped = true;
    stop();
    clearTimeout(retry);
  };
}
export function startInventoryJob() {
  if (globalThis.showcaseInventoryStop) return;
  const stopInventory = startScheduledInventory();
  const stopCounts = scheduleRelationCounts(() =>
    captureRelationCounts({ control: controlStore(), warehouse: warehouse() }),
  );
  globalThis.showcaseInventoryStop = () => {
    stopInventory();
    stopCounts();
  };
}

declare global {
  var showcaseInventoryStop: (() => void) | undefined;
}
