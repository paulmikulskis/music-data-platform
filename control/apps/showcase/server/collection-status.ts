import "server-only";
import type { ContractRouterClient } from "@orpc/contract";
import type { contract } from "@mdp/contracts";
import type { Person } from "@mdp/showcase-auth";
import { controlClient } from "./clients";
import { budget } from "./read-budget";

type Alerts = ContractRouterClient<typeof contract>["alerts"];

// Acknowledgment silences the operator banner; only resolution clears a failure.
// The list is newest first. One page from each state is enough to find the latest.
export async function readCollectionFailure(alerts: Pick<Alerts, "list">) {
  const groups = await Promise.all(
    [false, true].map((acknowledged) =>
      alerts.list({ resolved: false, acknowledged, class: "cadence_failed" }),
    ),
  );
  const latest = groups
    .flat()
    .filter((alert) => alert.resolved_at === null)
    .sort((a, b) => Date.parse(b.opened_at) - Date.parse(a.opened_at))[0];
  // Only the timestamp reaches the viewer page, never operator details or actions.
  return { failedAt: latest?.opened_at ?? null };
}

export async function collectionFailure(person: Person) {
  const alerts = controlClient(person).alerts;
  return readCollectionFailure({
    list: (input) => budget.run("essential", () => alerts.list(input)),
  });
}
