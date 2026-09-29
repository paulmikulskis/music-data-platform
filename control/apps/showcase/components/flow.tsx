"use client";
import { functionFlow, stageSentence } from "@mdp/contracts/flows";
import { marks, type MarkTone } from "@mdp/contracts/marks";
import { sourceWording } from "@mdp/contracts/source-wording";
import type { LinkPreview } from "../lib/links";
import { traceLink } from "../lib/trace";
import { Hover } from "./hover";
import { LinkOut } from "./link-out";
import { Mark } from "./mark";
import { useTending } from "./sources";
import { LocalTime } from "./local-time";
import {
  functionIsActive,
  functionSentence,
  sourceFunctionState,
  type FunctionState,
} from "../lib/function-copy";
export { functionSentence } from "../lib/function-copy";
export function FunctionCardRows({
  sourceKey,
  links,
  state,
}: {
  sourceKey: string;
  links?: LinkPreview[];
  state?: FunctionState;
}) {
  const tending = useTending();
  const current =
    state ??
    sourceFunctionState(
      tending?.sources.find((source) => source.source_key === sourceKey),
    );
  const flow = functionFlow(sourceKey);
  if (!flow) return <a href="/sources">See sources →</a>;
  const consoleLink = (links ?? tending?.links ?? []).find(
    (link) => link.id === "proof-open-console" && link.variant === sourceKey,
  );
  return (
    <div className="function-card" data-function-card={sourceKey}>
      <dl>
        <dt>How often</dt>
        <dd>
          {functionIsActive(current)
            ? flow.card.how_often
            : `When enabled: ${flow.card.how_often.toLowerCase()}`}
        </dd>
        <dt>What it reads</dt>
        <dd>{flow.card.reads}</dd>
        <dt>Why it matters</dt>
        <dd>{flow.card.why}</dd>
        <dt>Ideas</dt>
        <dd>
          <ul>
            {flow.card.ideas.map((idea) => (
              <li key={idea}>{idea}</li>
            ))}
          </ul>
        </dd>
      </dl>
      <a href={traceLink({ entry: `source.${sourceKey}` })}>See the rows →</a>
      {consoleLink && (
        <LinkOut
          preview={{ ...consoleLink, label: "Open the function" }}
          showPreview={false}
        />
      )}
    </div>
  );
}
export function FunctionName({
  sourceKey,
  still = false,
  lastRead,
  count,
  enabled,
  paused,
}: {
  sourceKey: string;
  still?: boolean;
  lastRead?: string | null;
  count?: number | null;
  enabled?: boolean | null;
  paused?: boolean;
}) {
  const source = useTending()?.sources.find(
    (source) => source.source_key === sourceKey,
  );
  const flow = functionFlow(sourceKey);
  if (!flow || flow.hidden) return null;
  const name =
    sourceWording[sourceKey]?.name ?? source?.display_name ?? "Collection job";
  const state = {
    ...sourceFunctionState(source),
    ...(lastRead !== undefined ? { lastRead } : {}),
    ...(count !== undefined ? { count } : {}),
    ...(enabled !== undefined ? { enabled } : {}),
    ...(paused !== undefined ? { paused } : {}),
  };
  const sentence = functionSentence(sourceKey, state);
  const content = (
    <span className="function-name" data-function={sourceKey}>
      <strong>{name}</strong>
      <span>{sentence}</span>
      {!functionIsActive(state) && state.lastRead && (
        <small>
          Last read <LocalTime at={state.lastRead} />
        </small>
      )}
    </span>
  );
  if (still) return content;
  return (
    <Hover
      label={name}
      fitViewport
      tap
      interactive
      card={<FunctionCardRows sourceKey={sourceKey} state={state} />}
    >
      <button className="function-trigger" aria-label={`About ${name}`}>
        {content}
      </button>
    </Hover>
  );
}
export function FunctionFlow({
  sourceKey,
  tone = "dark",
  compact = false,
}: {
  sourceKey: string;
  tone?: MarkTone;
  compact?: boolean;
}) {
  const source = useTending()?.sources.find(
    (source) => source.source_key === sourceKey,
  );
  const flow = functionFlow(sourceKey);
  if (!flow || flow.hidden) return null;
  const stages = compact
    ? flow.stages.filter(({ stage }) =>
        ["Source", "Access", "Collection job"].includes(stage),
      )
    : flow.stages;
  return (
    <ol
      className={`function-flow ${compact ? "compact" : ""}`}
      aria-label="Collection steps"
    >
      {stages.map(({ stage, mark }) => {
        const label =
          stage === "Access"
            ? flow.vendor
              ? marks[flow.vendor].label
              : (flow.access ?? "Access")
            : stage === "Collection job"
              ? `Python ${flow.job_kind.toLowerCase()}`
              : stage === "Source" && !flow.platform
                ? flow.card.reads
                : undefined;
        return (
          <li
            className="flow-node"
            key={stage}
            data-stage={stage}
            title={stageSentence(stage)}
            data-hover-sentence={stageSentence(stage)}
          >
            <Mark
              name={mark}
              label={label}
              tone={tone}
              monogram={
                stage === "Access" &&
                !!flow.vendor &&
                !!marks[flow.vendor].monogram
              }
            />
            {!compact && (
              <small>
                {stage}
                {stage === "Watch list" && source?.tracked
                  ? ` · ${source.tracked.count.toLocaleString("en-US")}`
                  : ""}
              </small>
            )}
            {stage === "Ready to use" && (
              <small className="rights-badge">Rights labelled</small>
            )}
          </li>
        );
      })}
    </ol>
  );
}
