import { z } from "zod";
import registry from "./source-registry.generated.json";
import type { Source } from "../components/sources";
import { honesty } from "./honesty";

const declarations = z
  .record(
    z.string(),
    z.object({
      layer: z.string(),
      external: z.boolean(),
      source: z.boolean(),
    }),
  )
  .parse(registry);

// Undeclared jobs have no current reader contract. They stay in the operator view.
export function isMusicSource(source: Pick<Source, "source_key">) {
  return declarations[source.source_key]?.source === true;
}

// The readers behind Home's music come before catalog and matching services.
const importance = ["spotify", "apple_music", "shazam", "billboard"];
function priority(source: Source) {
  const index = ["playlists", "charts", "social", "streams"].includes(
    source.family,
  )
    ? importance.indexOf(source.brand ?? "")
    : -1;
  return index < 0 ? importance.length : index;
}
export type SourceFamily = {
  key: string;
  name: string;
  readers: Source[];
  primary: Source;
  enabled: boolean;
  label: string;
};
export function sourceFamilies(
  sources: Source[],
  now = Date.now(),
): SourceFamily[] {
  const readers = sources.filter(isMusicSource);
  const keys = new Set(readers.map((source) => source.source_key));
  const groups = new Map<string, Source[]>();
  for (const source of readers) {
    const base = source.source_key.replace(/_weekly$/, "");
    const key = keys.has(base) ? base : source.source_key;
    const family = groups.get(key) ?? [];
    family.push(source);
    groups.set(key, family);
  }
  return Array.from(groups, ([key, members]) => {
    const ordered = members.toSorted(
      (a, b) =>
        Number(b.enabled) - Number(a.enabled) ||
        Number(!!b.last_read) - Number(!!a.last_read) ||
        a.source_key.localeCompare(b.source_key),
    );
    const primary = ordered[0];
    return {
      key,
      name:
        members.find((reader) => reader.source_key === key)?.display_name ??
        primary.display_name,
      readers: ordered,
      primary,
      enabled: members.some((reader) => reader.enabled),
      label: [
        ...new Set(ordered.map((reader) => honesty(reader, now).label)),
      ].join(" · "),
    };
  }).sort(
    (a, b) =>
      Number(b.enabled) - Number(a.enabled) ||
      priority(a.primary) - priority(b.primary) ||
      a.name.localeCompare(b.name),
  );
}

export function liveProviderFamilies(families: SourceFamily[]) {
  const brands = new Set<string>();
  return families.filter((family) => {
    const source = family.primary;
    if (
      !source.enabled ||
      !source.last_read ||
      !source.brand ||
      brands.has(source.brand)
    )
      return false;
    brands.add(source.brand);
    return true;
  });
}
