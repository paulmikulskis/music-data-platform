import "server-only";
import type { Person } from "@mdp/showcase-auth";
import { nightWindow } from "@mdp/contracts";
import { z } from "zod";
import { budget } from "./read-budget.js";
import { controlClient, warehouse } from "./clients.js";
import { currentBuilds, relationBuildKey } from "./relation-counts.js";

type Night = Awaited<
  ReturnType<ReturnType<typeof controlClient>["platform"]["night"]>
>;
const nights = budget.cache<Night>();
const builds = budget.cache<Awaited<ReturnType<typeof currentBuilds>>>();
// Current stamps prove completed table replacements, separately from collection closes.
// Replaced stamps cannot reconstruct an earlier night and are not backfilled.
export async function night(
  person: Person,
  input: z.infer<typeof nightWindow>,
) {
  const window = nightWindow.parse(input);
  const result = await nights.read(
    `person:${person.api_key_id}:night:${window.since}:${window.until}`,
    "light",
    () => controlClient(person).platform.night(window),
    60000,
  );
  const stamps = await builds
    .read(
      `global:night-builds:${result.value.warehouse_id}`,
      "heavy",
      () => currentBuilds(warehouse()),
      60000,
    )
    .catch(() => null);
  const ready =
    stamps?.value
      .filter(
        (stamp) =>
          Date.parse(stamp.built_at) >= Date.parse(window.since) &&
          Date.parse(stamp.built_at) < Date.parse(window.until),
      )
      .map((stamp) => ({
        relation: stamp.relation,
        cycle_id: stamp.cycle_id,
        close_no: stamp.close_no,
        built_at: stamp.built_at,
        build_key: relationBuildKey(stamp),
      })) ?? [];
  // An empty saved read has a read time, but no replacement stamp to preserve.
  const readyStamps = stamps?.state === "live" || ready.length ? stamps : null;
  return {
    ...result,
    value: {
      ...result.value,
      ready,
      ready_state: readyStamps?.state ?? "unavailable",
      ready_saved_at: readyStamps?.savedAt ?? null,
    },
  };
}
