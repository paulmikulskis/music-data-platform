import "server-only";
import type { Person } from "@mdp/showcase-auth";
import { controlClient } from "./clients";
import type { Provenance } from "../components/number";
export async function headline(
  person: Person,
): Promise<{ value: string | null; provenance: Provenance }> {
  const status = await controlClient(person).status({});
  const latest = status.cadences.find(
    (c) => c.cadence === "hourly" && c.scope === "global",
  )?.last_closed;
  return {
    value: latest?.close_no ?? null,
    provenance: {
      queried_at: status.checked_at,
      scope: "global",
      cadence: "hourly",
      close_no: latest?.close_no ?? null,
      observed_at: latest?.closed_at ?? null,
      provenance: "live query",
      query:
        "The most recent closed, scheduled hourly cycle for the global platform. This is its close number, not a count of runs. Manual, backfill and canary cycles are excluded. A close records collection; it does not prove that every table has rebuilt.",
      sql: "pnpm --dir control mdp status",
      copyKind: "command",
    },
  };
}
