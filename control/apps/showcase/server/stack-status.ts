import "server-only";
import type { Person } from "@mdp/showcase-auth";
import { required } from "@mdp/showcase-auth";
import { budget } from "./read-budget";
import { warehouse } from "./clients";
import { night } from "./night";
import { countInputs } from "./relation-counts";
import { lineage } from "../lib/lineage";
import { stackAliases } from "../lib/stack-facts";
import type { StackStatus } from "../lib/stack";

type Service = StackStatus["services"][number];
type Check = { probe: string; run: () => Promise<string> };
const cache = budget.cache<Service>();
const timeout = 1800;
// A round's transform has this long to rebuild its tables before the check reads it as missing.
export const rebuildGraceMs = 30 * 60000;

async function http(url: string) {
  const response = await fetch(url, {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(timeout),
  });
  await response.body?.cancel();
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return "Proves the process answered, nothing more.";
}
function optional(name: string) {
  const value = process.env[name];
  return value && value.length > 0 ? value : null;
}
// The cadences whose rounds rebuild at least one reviewed table. A round of any other cadence
// leaves no stamp this app can read, so its close is counted but not checked for a rebuild.
const reviewed = new Set(countInputs.map((input) => input.relation));
const relationCadences = new Map(
  lineage.nodes.flatMap((node) =>
    node.kind === "relation" &&
    node.relation !== null &&
    reviewed.has(node.relation) &&
    node.cadence !== null
      ? [[node.relation, node.cadence] as const]
      : [],
  ),
);
export const rebuildingCadences = new Set(relationCadences.values());
type Night = Awaited<ReturnType<typeof night>>["value"];
// A reason the clock did not answer, written for the status card. Any other error stays
// unnamed, so a transport message never carries a host or an address to the browser.
export class ClockUnmet extends Error {}
// The clock answers only when three signals agree: the runner's state is readable, a round
// closed in the last day, and each rebuilding cadence has a reviewed table at or beyond its
// newest settled close. Stamps keep only the latest replacement, including restore builds.
// Match the table's declared cadence so an hourly table cannot speak for a daily one.
// A cadence with no round in the window, or a round younger than the grace, is not failed.
function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}
function closeNumber(value: string | null) {
  return value && /^\d+$/.test(value) && BigInt(value) > 0n
    ? BigInt(value)
    : null;
}
function rebuiltAtOrAfter(
  stamp: Night["ready"][number],
  target: Night["closes"][number],
  rounds: Night["closes"],
) {
  if (stamp.cycle_id === target.cycle_id) return true;
  const stamped = closeNumber(stamp.close_no);
  const closed = closeNumber(target.close_no);
  // Close numbers commit in order across all cadences in this global scope. A restore
  // may stamp a weekly table with a later daily cycle, so the stamp's cycle need not match.
  if (stamped !== null && closed !== null) return stamped >= closed;
  // Legacy rounds have no sequence. Only a later closed round of this cadence proves order.
  return rounds.some(
    (round) =>
      round.cycle_id === stamp.cycle_id &&
      Date.parse(round.closed_at) > Date.parse(target.closed_at),
  );
}
export function clockNote(value: Night, now = Date.now()) {
  if (value.runner.state === "unknown")
    throw new ClockUnmet("Runner state unknown.");
  const closes = value.closes
    .filter((close) => close.status === "closed")
    .sort((a, b) => a.closed_at.localeCompare(b.closed_at));
  if (!closes.length)
    throw new ClockUnmet("No completed round in the last day.");
  if (value.ready_state === "unavailable" || !value.ready_saved_at)
    throw new ClockUnmet("Rebuilt tables not read.");
  if (value.ready_state !== "live" && !value.ready.length)
    throw new ClockUnmet("No saved update times. Open night details to retry.");
  const unmet: string[] = [];
  const read: string[] = [];
  for (const cadence of [...rebuildingCadences].sort()) {
    const rounds = closes.filter((close) => close.cadence === cadence);
    const latest = rounds.at(-1);
    if (!latest) {
      read.push(`No ${cadence} round closed in that day.`);
      continue;
    }
    const settled = rounds.filter(
      (close) => Date.parse(close.closed_at) <= now - rebuildGraceMs,
    );
    const target = settled.at(-1) ?? latest;
    if (Date.parse(value.ready_saved_at!) < Date.parse(target.closed_at))
      throw new ClockUnmet(
        "Saved update times precede the last round. Open night details to retry.",
      );
    const stamps = value.ready.filter(
      (stamp) => relationCadences.get(stamp.relation) === cadence,
    );
    const proved = rounds.findLast(
      (round) =>
        Date.parse(round.closed_at) >= Date.parse(target.closed_at) &&
        stamps.some((stamp) => rebuiltAtOrAfter(stamp, round, rounds)),
    );
    const rebuilt = proved
      ? stamps.filter((stamp) => rebuiltAtOrAfter(stamp, proved, rounds)).length
      : 0;
    if (rebuilt) {
      read.push(
        `${proved === latest ? "The last" : "An earlier"} ${cadence} round rebuilt ${plural(rebuilt, "ready table", "ready tables")}.`,
      );
    } else if (settled.length) {
      unmet.push(cadence);
    } else {
      read.push(
        `The last ${cadence} round closed under ${rebuildGraceMs / 60000} minutes ago; its rebuild is not checked yet.`,
      );
    }
  }
  if (unmet.length)
    throw new ClockUnmet(
      `The last ${unmet.join(" and ")} ${unmet.length === 1 ? "round" : "rounds"} closed without rebuilding a ready table.`,
    );
  return `${plural(closes.length, "round", "rounds")} completed in the last day. ${read.join(" ")}`;
}
// Each check proves only what it names. An HTTP answer proves the process answered; a SQL
// answer proves the database answered; the clock's signals are listed above.
export function checks(person: Person): Record<string, Check> {
  const service = optional("MDP_SERVICE_URL");
  const bench = optional("MDP_WORKBENCH_URL");
  return {
    Console: {
      probe: "Console HTTP health",
      run: () =>
        http(new URL("/health", required("MDP_CONTROL_API_URL")).toString()),
    },
    Warehouse: {
      probe: "Warehouse SQL check",
      run: async () => {
        await warehouse()`SELECT 1`;
        return "A read-only SELECT.";
      },
    },
    "Data API": {
      probe: "Data API HTTP health",
      run: () =>
        http(new URL("/health", required("MDP_DATA_API_URL")).toString()),
    },
    ...(service
      ? {
          Readers: {
            probe: "Readers HTTP health",
            run: () => http(new URL("/v1/health", service).toString()),
          },
        }
      : {}),
    ...(bench
      ? {
          Workbench: {
            probe: "Workbench HTTP health",
            run: () => http(new URL("/v1/health", bench).toString()),
          },
        }
      : {}),
    "The clock": {
      probe: "Runner state, last rounds and rebuilt tables",
      run: async () => {
        const until = new Date();
        const since = new Date(until.getTime() - 24 * 3600000);
        // The night read carries the runner state (control-api reads it with readRunnerState)
        // and the current build stamps (catalog.snapshot_stamp through the showcase warehouse).
        const result = await night(person, {
          since: since.toISOString(),
          until: until.toISOString(),
        });
        return clockNote(result.value, until.getTime());
      },
    },
    "This app": {
      probe: "This page",
      run: async () => "You are reading it.",
    },
  };
}
export async function stackStatus(person: Person): Promise<StackStatus> {
  const table = checks(person);
  const services = await Promise.all(
    stackAliases.map(async (alias): Promise<Service> => {
      const check = table[alias];
      if (!check)
        return {
          alias,
          state: "not_checked",
          probe: "Not checked",
          checked_at: null,
          note: "No check runs from this app.",
        };
      const result = await cache
        .read(`stack:${alias}`, "light", async () => {
          const checked_at = new Date().toISOString();
          try {
            return {
              alias,
              state: "answered" as const,
              probe: check.probe,
              checked_at,
              note: await check.run(),
            };
          } catch (error) {
            return {
              alias,
              state: "no_answer" as const,
              probe: check.probe,
              checked_at,
              // The clock's own reasons are plain sentences; every other failure says no answer.
              note: error instanceof ClockUnmet ? error.message : "No answer.",
            };
          }
        })
        .catch(() => null);
      return (
        result?.value ?? {
          alias,
          state: "not_checked",
          probe: check.probe,
          checked_at: null,
          note: "Status not checked.",
        }
      );
    }),
  );
  return { checked_at: new Date().toISOString(), services };
}
