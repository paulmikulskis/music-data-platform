// Core Retry and Replay start a one-off core-runner machine in the cycle's scope. The
// `fly` launcher uses the Fly Machines API with the core-runner app's token and copies the image of the
// cadence's scheduled machine; `record` (MDP_CORE_LAUNCHER=record, local and test stacks) starts
// nothing and returns the machine it would have started.
import { AppError } from "./db.js";
import type { PlatformStatus } from "@mdp/contracts";

const missingTokenMessage = "Set MDP_FLY_MACHINES_TOKEN (core-runner app deploy token) on control-api to start Core Retry and Replay machines";

export function coreLauncherStatus(): PlatformStatus["retry_launcher"] {
  if (process.env.MDP_CORE_LAUNCHER === "record")
    return { state: "configured", message: "Local recording mode: Retry and Replay record a launch without starting a machine." };
  return process.env.MDP_FLY_MACHINES_TOKEN?.trim()
    ? { state: "configured", message: "Core Retry and Replay are configured. Use Retry in Ops → Recovery, or run pnpm --dir control mdp retry <cycle-id>." }
    : { state: "missing", message: missingTokenMessage };
}

export type CoreLaunch = {
  launcher: "fly" | "record";
  machine_id: string | null;
  cadence: "hourly" | "daily" | "weekly";
  scope: string;
  command: string[];
  env: Record<string, string>;
};

export async function launchCore(
  cadence: CoreLaunch["cadence"],
  scope: string,
  tenantSlug: string | null,
  args: string[],
): Promise<CoreLaunch> {
  const command = [cadence, ...args];
  const env: Record<string, string> = { DBT_MDP_SCOPE: scope, MDP_RUN_REASON_CATEGORY: "other" };
  if (tenantSlug) env.MDP_TENANT_SLUG = tenantSlug;
  const launch = { cadence, scope, command, env };
  if (process.env.MDP_CORE_LAUNCHER === "record") return { ...launch, launcher: "record", machine_id: null };
  const token = process.env.MDP_FLY_MACHINES_TOKEN;
  if (!token)
    throw new AppError("launcher_unavailable", missingTokenMessage, 503);
  const app = process.env.MDP_CORE_RUNNER_APP ?? "mdp-core-runner";
  const api = `${process.env.MDP_FLY_MACHINES_API ?? "https://api.machines.dev"}/v1/apps/${app}/machines`;
  const headers = { authorization: `Bearer ${token}`, "content-type": "application/json" };
  const listed = await fetch(api, { headers });
  if (!listed.ok) throw new AppError("launcher_unavailable", `Fly machine list failed (${listed.status})`, 503);
  const machines = (await listed.json()) as { name?: string; region?: string; config?: { image?: string } }[];
  // The scheduled machine of the cadence carries the image the last deploy pushed.
  const scheduled = machines.find((m) => m.name === `mdp-${cadence}`);
  if (!scheduled?.config?.image)
    throw new AppError("launcher_unavailable", `No scheduled mdp-${cadence} machine to copy the image from`, 503);
  const created = await fetch(api, {
    method: "POST",
    headers,
    body: JSON.stringify({
      region: scheduled.region,
      config: {
        image: scheduled.config.image, env, init: { cmd: command }, auto_destroy: true,
        restart: { policy: "no" }, guest: { cpu_kind: "shared", cpus: 2, memory_mb: 2048 },
      },
    }),
  });
  if (!created.ok) throw new AppError("launcher_unavailable", `Fly machine create failed (${created.status})`, 503);
  const machine = (await created.json()) as { id?: string };
  return { ...launch, launcher: "fly", machine_id: machine.id ?? null };
}
