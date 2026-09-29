import { database } from "./db.js";
import { pendingProbeTargets, probeTargets } from "./target-probe.js";
import { serviceDeadline } from "./service.js";

// Network checks run outside import and activation transactions. One worker owns a
// short pass; later passes retry unavailable checks and continue from the oldest check.
export async function drainTargetProbes() {
  const deadline = Date.now() + 15000;
  try {
    await serviceDeadline.run(deadline, () => database().begin(async db => {
      await db.unsafe("SET LOCAL statement_timeout='1s'");
      const [lock] = await db.unsafe("SELECT pg_try_advisory_xact_lock(hashtext('target-probes')) AS locked");
      if (!lock?.locked) return;
      for (const target of await pendingProbeTargets(db)) {
        if (Date.now() >= deadline) break;
        const probe = await probeTargets(db, [target.id]);
        const status = probe.results[0]?.status ?? "skipped";
        if (target.seed && status === "ok")
          await db.unsafe("UPDATE control.target SET activated_at=now() WHERE id=$1 AND deactivated_at IS NULL", [target.id]);
        await db.unsafe("INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('target-probe','targets.probeChecked',$1,$2::text::jsonb)",
          [target.id, JSON.stringify({ status })]);
        console.info(`Target probe ${target.id}: ${status}`);
      }
    }));
  } catch {
    console.warn("Target checks unavailable; imports and activation continue. The worker retries next minute.");
  }
}

export function startTargetProbeWorker(drain = drainTargetProbes) {
  let busy = false;
  const tick = () => {
    if (busy) return;
    busy = true;
    void drain().catch(() => console.warn("Target checks unavailable; the worker retries next minute."))
      .finally(() => { busy = false; });
  };
  const timer = setInterval(tick, 60000);
  timer.unref();
  tick();
  return () => clearInterval(timer);
}
