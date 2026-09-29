import { z } from "zod";
import generated from "./function-flows.generated.json" with { type: "json" };
import { marks, type MarkTone } from "./marks.js";

export const stageVocabulary = [
  {
    stage: "Watch list",
    mark: "watch_list",
    plain:
      "The playlists, charts and accounts we point a job at. People, scope files and the promote steps fill it.",
  },
  {
    stage: "Source",
    mark: "postgresql",
    plain:
      "The app or site the numbers come from, the same one fans and the industry already use.",
  },
  {
    stage: "Access",
    mark: "access",
    plain:
      "How we get in: a paid data service, the platform's own API, or the public web page anyone can open.",
  },
  {
    stage: "Collection job",
    mark: "python",
    plain:
      "Our code that runs on a clock, reads the source for every item on the watch list, and hands back rows.",
  },
  {
    stage: "Received",
    mark: "postgresql",
    plain:
      "Saved exactly as read, stamped with when and where it came from, before anyone changes it.",
  },
  {
    stage: "Cleaned and matched",
    mark: "dbt",
    plain:
      "Tidied and joined, so one song on Spotify, Apple and Shazam counts once and every artist ties to one id.",
  },
  {
    stage: "Ready to use",
    mark: "postgresql",
    plain:
      "Finished tables people and apps read, each row labelled with where it came from and whether we may learn from it.",
  },
  {
    stage: "Your number",
    mark: "mdp",
    plain:
      "Where a person sees the result: a page in the showcase, the Console, or an app reading the Data API.",
  },
] as const;
export type FlowStage = (typeof stageVocabulary)[number]["stage"];
const stage = z.enum(stageVocabulary.map((item) => item.stage));
const markKey = z
  .string()
  .refine(
    (key) => Object.hasOwn(marks, key),
    "Choose a mark from @mdp/contracts/marks.",
  );
export const functionFlowSchema = z
  .object({
    platform: markKey.nullable(),
    access: z.string().nullable(),
    vendor: markKey.nullable(),
    job_kind: z.enum([
      "Reader",
      "Page reader",
      "Lookup",
      "Import",
      "AI step",
      "Housekeeping",
    ]),
    cadence: z.enum(["hourly", "daily", "weekly"]),
    hosts: z.array(z.string()),
    writes: z.array(z.string()),
    stages: z.array(z.object({ stage, mark: markKey }).strict()),
    card: z
      .object({
        what: z.string().min(1),
        how_often: z.string().min(1),
        reads: z.string().min(1),
        why: z.string().min(1),
        ideas: z.array(z.string().startsWith("Could")).min(1),
        waiting_on: z.string().min(1),
      })
      .strict(),
    hidden: z.boolean(),
  })
  .strict();
export const functionFlows = z
  .record(z.string(), functionFlowSchema)
  .parse(generated);
export type FunctionFlowDefinition = z.infer<typeof functionFlowSchema>;
export function functionFlow(key: string) {
  return Object.hasOwn(functionFlows, key) ? functionFlows[key] : null;
}
export function markFor(key: string, tone: MarkTone = "dark") {
  const mark = Object.hasOwn(marks, key) ? marks[key] : null;
  if (!mark) return null;
  const file = tone === "dark" ? mark.files.onDark : mark.files.onLight;
  return { ...mark, src: file ? `/brand/third-party/${file}` : null };
}
export function stageSentence(stage: FlowStage) {
  return stageVocabulary.find((item) => item.stage === stage)!.plain;
}
