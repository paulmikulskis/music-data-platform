import { functionFlow } from "@mdp/contracts/flows";
import type { platformSource } from "@mdp/contracts/platform";
import type { z } from "zod";

export type FunctionState = {
  enabled?: boolean | null;
  paused?: boolean;
  lastRead?: string | null;
  count?: number | null;
};
type SourceState = Pick<
  z.infer<typeof platformSource>,
  "enabled" | "last_read" | "tracked" | "evidence"
>;

export function sourceFunctionState(
  source?: SourceState | null,
): FunctionState {
  const incident = source?.evidence?.incident;
  return {
    enabled: source?.enabled,
    paused:
      (!!incident && source?.enabled === false) ||
      incident?.class === "provider_credentials_missing",
    lastRead: source?.last_read,
    count: source?.tracked?.count,
  };
}

export function functionIsActive(
  state: Pick<FunctionState, "enabled" | "paused">,
) {
  return state.enabled === true && !state.paused;
}

// Reviewed copy describes current operation only when the recorded state supports it.
export function functionSentence(sourceKey: string, state: FunctionState = {}) {
  const flow = functionFlow(sourceKey);
  if (!flow) return "Open Sources to find this collection job.";
  const what = flow.card.what
    .replaceAll(
      "{count}",
      state.count == null ? "" : state.count.toLocaleString("en-US"),
    )
    .replace(/\s+/g, " ");
  if (state.paused || state.enabled === false)
    return `${state.paused ? "Paused" : "Switched off"}. Waiting on ${flow.card.waiting_on}. When enabled: ${what}`;
  if (!state.lastRead)
    return `Built, waiting on ${flow.card.waiting_on}. Once ready: ${what}`;
  if (!functionIsActive(state))
    return `Current state is not checked. When enabled: ${what}`;
  return what;
}
