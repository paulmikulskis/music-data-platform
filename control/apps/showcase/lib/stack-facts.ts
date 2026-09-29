import { z } from "zod";
import facts from "../../../../ops/showcase/stack/facts.json";
import apps from "../../../../ops/showcase/stack/apps.json";
// The dated hand facts ship with the app, so cards still render when the deploy artifact is
// missing. The deploy artifact carries the same facts; the collector checks they are equal.
export const datedFact = z
  .object({
    text: z.string().min(1),
    captured_at: z.string().datetime({ offset: true }),
    evidence: z.string().min(1),
  })
  .strict();
export type DatedFact = z.infer<typeof datedFact>;
export const datedFacts: Record<string, DatedFact[]> = z
  .record(z.string(), z.array(datedFact))
  .parse(facts);
export const stackAliases = z
  .array(z.object({ alias: z.string() }).loose())
  .parse(apps)
  .map((app) => app.alias);
// The newest date a fact on this page was captured, for the page's own dated line.
export function latestFactDate(values = datedFacts) {
  return Object.values(values)
    .flat()
    .map((fact) => fact.captured_at)
    .sort()
    .at(-1);
}
