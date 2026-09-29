import { honesty } from "./honesty";
import { sourceWording } from "@mdp/contracts/source-wording";
import type { Rights } from "../server/models";
import type { Source } from "../components/sources";
import { functionFlow } from "@mdp/contracts/flows";
import { functionSentence, sourceFunctionState } from "./function-copy";

// Where a source on record stands, from its source record:
// collecting: switched on and brought in entries in the last two weeks;
// quiet: switched on, but nothing brought in for two weeks;
// off: brought in entries in the last two weeks, now switched off;
// waiting: waiting for provider access or credentials, or not set up (no record of a read).
export type RightsState = "collecting" | "quiet" | "off" | "waiting";
export type RightsEntry = Rights["sources"][number] & {
  name: string;
  plain: string | null;
  source: Source | null;
  state: RightsState;
};
export type RightsGroups = Record<
  RightsState | "own" | "all",
  RightsEntry[]
> & {
  // False when the source records could not be read; the sheet then lists all sources.
  measured: boolean;
};
// the platform's own reviewed lists in the rights register (provider: platform). They are
// authored locally, not collected.
export const ownLists = new Set<string>();
// Sources waiting for provider access. Their function has no credentials and never counts as
// read, even when an old sample load sits in the record.
export const awaitingAccess = new Set<string>();
// What any viewer surface says for such a source, with no read date.
export const waitingForAccess =
  "Not connected yet · waiting for provider access";
// Internal example sources a viewer never sees on the Rights screen.
export const exampleSources = new Set([
  "typesafe",
  "jev_instruments",
  "jev_instrument_family",
]);

// A viewer name: the source wording, then the platform's name, then the key in plain words.
export function rightsName(key: string, source?: Source | null) {
  return (
    sourceWording[key]?.name ??
    source?.display_name ??
    key.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase())
  );
}

// Access comes first: a sample load never makes a source read.
// `now` is the reading time; tests and counters pass one so a state never depends on the wall clock.
export function rightsState(
  source: Source | null,
  now = Date.now(),
): RightsState {
  if (!source || honesty(source, now).state === "access") return "waiting";
  if (!source.enabled) return "off";
  return honesty(source, now).state === "current" ? "collecting" : "quiet";
}
export function collecting(source: Source | null, now = Date.now()) {
  return (
    !!source &&
    rightsState(source, now) === "collecting" &&
    !awaitingAccess.has(source.source_key)
  );
}
// The sources a viewer sees as live: collecting, and never an internal example or the platform's
// own list, so Holdings, Home, the tended counters and Rights' Collecting now share one set.
export function liveList(
  sources: Source[] | null | undefined,
  now = Date.now(),
) {
  return (sources ?? []).filter(
    (source) =>
      collecting(source, now) &&
      !exampleSources.has(source.source_key) &&
      !ownLists.has(source.source_key),
  );
}
// Sources live on Holdings and Home: the same count as Rights' Collecting now.
export function liveSources(
  sources: Source[] | null | undefined,
  now = Date.now(),
) {
  return sources?.length ? String(liveList(sources, now).length) : null;
}
// Sources with real reads: every source but those waiting for provider access, whose record
// may hold sample loads. Entries and days of history count these, as the week's total does.
export function heldSources(sources: Source[]) {
  return sources.filter((source) => !awaitingAccess.has(source.source_key));
}

export function rightsGroups(
  rows: Rights["sources"],
  sources: Source[] | null,
): RightsGroups {
  const measured = !!sources?.length;
  const entries = rows
    .filter((row) => !exampleSources.has(row.source_key))
    .map((row): RightsEntry => {
      const source =
        sources?.find((item) => item.source_key === row.source_key) ?? null;
      return {
        ...row,
        name: rightsName(row.source_key, source),
        plain: functionFlow(row.source_key)
          ? functionSentence(row.source_key, sourceFunctionState(source))
          : null,
        source,
        state: rightsState(source),
      };
    });
  // A weekly twin that shares its daily source's name says so.
  for (const entry of entries)
    if (
      entry.source_key.endsWith("_weekly") &&
      entries.some((other) => other !== entry && other.name === entry.name)
    )
      entry.name = `${entry.name}, weekly`;
  const byName = (a: RightsEntry, b: RightsEntry) =>
    a.name.localeCompare(b.name);
  const collected = entries
    .filter((entry) => !ownLists.has(entry.source_key))
    .sort(byName);
  const inState = (state: RightsState) =>
    measured ? collected.filter((entry) => entry.state === state) : [];
  return {
    collecting: inState("collecting"),
    quiet: inState("quiet"),
    off: inState("off"),
    waiting: inState("waiting"),
    own: entries.filter((entry) => ownLists.has(entry.source_key)).sort(byName),
    all: collected,
    measured,
  };
}

// What a source's two permissions allow, in plain words.
export function permissionLine(entry: { learning: boolean; resale: boolean }) {
  if (entry.learning && entry.resale) return "Training and resale cleared.";
  if (!entry.learning && !entry.resale)
    return "Not cleared for training or resale.";
  return entry.learning
    ? "Training cleared. Not cleared for resale."
    : "Resale cleared. Not cleared for training.";
}
