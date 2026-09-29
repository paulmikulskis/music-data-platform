import { platformNight, platformSources } from "@mdp/contracts/platform";
import sourceData from "./fixtures/synthetic-sources.json";
import nightData from "./fixtures/synthetic-night.json";
import { nightResponse } from "../lib/night";

export const syntheticSources = platformSources.parse(sourceData);
export const syntheticNight = platformNight.parse(nightData);
// The synthetic control response has closes, but no warehouse table stamps.
export const syntheticNightResponse = nightResponse.parse({
  state: "live",
  savedAt: syntheticNight.window.queried_at,
  value: {
    ...syntheticNight,
    ready: [],
    ready_saved_at: null,
    ready_state: "unavailable",
  },
});

// Keep the synthetic ages stable when CI replays these states on a later day.
export function syntheticSourcesAt(now: number, captured = syntheticSources) {
  const offset = now - Date.parse(captured.queried_at);
  const shift = (at: string) => new Date(Date.parse(at) + offset).toISOString();
  const shifted = structuredClone(captured);
  shifted.queried_at = shift(shifted.queried_at);
  for (const source of shifted.sources) {
    if (source.first_collected)
      source.first_collected = shift(source.first_collected);
    if (source.last_read) source.last_read = shift(source.last_read);
    if (source.tracked) source.tracked.as_of = shift(source.tracked.as_of);
    const evidence = source.evidence;
    if (!evidence) continue;
    evidence.declared_at = shift(evidence.declared_at);
    evidence.configured_at = shift(evidence.configured_at);
    evidence.checked_at = shift(evidence.checked_at);
    if (evidence.last_success)
      evidence.last_success = shift(evidence.last_success);
    if (evidence.attempt) evidence.attempt.at = shift(evidence.attempt.at);
    if (evidence.incident) evidence.incident.at = shift(evidence.incident.at);
  }
  return shifted;
}
