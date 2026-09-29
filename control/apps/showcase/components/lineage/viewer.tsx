"use client";
import { readSheet } from "./read";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  traceView,
  type TraceSelection,
  type TraceView,
} from "../../lib/trace";
import { Number as RecordedNumber } from "../number";
import { Mark, MarkImage, Monogram } from "../mark";
import {
  functionFlow,
  stageSentence,
  type FlowStage,
} from "@mdp/contracts/flows";
import { marks } from "@mdp/contracts/marks";
import { FunctionName, FunctionCardRows } from "../flow";
import { functionIsActive } from "../../lib/function-copy";
import { Hover } from "../hover";
import { LocalTime, useViewerZone } from "../local-time";
import {
  chains,
  stages,
  stageOf,
  readerForNode,
  nearestCountedStep,
} from "./layout";
import { Peek } from "./peek";
import { Sheet } from "../sheet";
const flowStages: Record<string, FlowStage> = {
  source: "Source",
  readers: "Collection job",
  collected: "Received",
  cleaned: "Cleaned and matched",
  ready: "Ready to use",
  screen: "Your number",
};
const stageMarks: Record<string, string> = {
  source: "postgresql",
  readers: "python",
  collected: "postgresql",
  cleaned: "dbt",
  ready: "postgresql",
  screen: "mdp",
};
function Schedule({ node }: { node: TraceView["nodes"][number] }) {
  const zone = useViewerZone();
  if (node.cadence === "hourly") return <>Every hour</>;
  if (!node.scheduled_at || !zone)
    return (
      <>
        {node.cadence === "daily"
          ? "Every day"
          : node.cadence === "weekly"
            ? "Every week"
            : "Schedule not recorded"}
      </>
    );
  const at = new Date(node.scheduled_at);
  const hour = Number(
    new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      hourCycle: "h23",
      timeZone: zone,
    }).format(at),
  );
  const time = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
    ...(node.cadence === "weekly" ? { weekday: "short" as const } : {}),
  }).format(at);
  const frequency =
    node.cadence === "weekly"
      ? "Every week"
      : hour < 6 || hour >= 20
        ? "Every night"
        : "Every day";
  return (
    <>
      {frequency}, after {time}
    </>
  );
}
export default function Viewer({ selection }: { selection: TraceSelection }) {
  const ref = useRef<HTMLDialogElement>(null);
  const router = useRouter();
  const pathname = usePathname();
  const [data, setData] = useState<TraceView | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const [active, setActive] = useState(0);
  const [peek, setPeek] = useState<TraceView["nodes"][number] | null>(null);
  const [details, setDetails] = useState<TraceView["nodes"][number] | null>(
    null,
  );
  const close = () => {
    const url = new URL(window.location.href);
    for (const key of ["trace", "song", "ranking", "source"])
      url.searchParams.delete(key);
    router.replace(url.pathname + url.search, { scroll: false });
  };
  useEffect(() => {
    const opener = document.activeElement;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams(
      Object.entries(selection).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string",
      ),
    );
    readSheet(`/s/trace?${query}`, controller.signal)
      .then(async (result) => {
        if (result.status === 401 || result.status === 403) {
          setData(null);
          router.replace("/sign-in");
          return;
        }
        if (!result.ok) throw new Error("Read unavailable");
        setData(traceView.parse(await result.json()));
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [selection, router, attempt]);
  const paths = useMemo(() => (data ? chains(data) : []), [data]);
  const path = paths[active] ?? [];
  const nodes = data?.nodes.filter((node) => path.includes(node.id)) ?? [];
  const stageNodes = stages.map((stage) =>
    nodes.filter((node) => stageOf(node.stage) === stage.id),
  );
  const cardFor = (node: TraceView["nodes"][number]) => {
    const stage = stageOf(node.stage);
    const reader = readerForNode(node, nodes, data?.edges ?? []);
    const counted = nearestCountedStep(node, nodes, path);
    const countedStage = stages.find(
      (candidate) => candidate.id === stageOf(counted?.stage ?? ""),
    );
    return (
      <>
        <p data-stage-sentence>
          {stageSentence(flowStages[stage] ?? "Source")}
        </p>
        {stage === "readers" && node.source && (
          <FunctionCardRows
            sourceKey={node.source}
            links={node.links}
            state={{
              enabled: node.enabled,
              paused: node.paused,
              lastRead: node.last_read,
            }}
          />
        )}
        {node.count ? (
          <p>
            {BigInt(node.count.value).toLocaleString("en-US")} stored{" "}
            <LocalTime at={node.count.captured_at} relativeDay />
          </p>
        ) : (
          <p>
            {counted && countedStage ? (
              <button
                onClick={() => {
                  setPeek(null);
                  setDetails(counted);
                }}
              >
                Counted at {countedStage.label} →
              </button>
            ) : (
              "Count not checked."
            )}
          </p>
        )}
        {(reader || stage === "collected" || stage === "source") && (
          <p>
            {reader?.paused
              ? "Paused · "
              : reader?.enabled === false && "Switched off · "}
            {reader?.last_read ? (
              <>
                Last read <LocalTime at={reader.last_read} />
              </>
            ) : (
              "Last read: not checked"
            )}
            .
          </p>
        )}
        <p>Not cleared for training/resale.</p>
        {node.preview ? (
          <button
            onClick={() => {
              setDetails(null);
              setPeek(node);
            }}
          >
            See rows →
          </button>
        ) : node.relation ? (
          <>
            <p>Records are not previewed here.</p>
            <a href="/sources">Source details →</a>
          </>
        ) : stage === "readers" ? (
          <a href="/stack#readers">See it on Stack →</a>
        ) : (
          <button onClick={() => setPeek(node)}>See details →</button>
        )}
      </>
    );
  };
  return (
    <>
      <dialog
        ref={ref}
        className={`sheet trace-viewer ${peek ? "with-peek" : ""}`}
        aria-label="Data source viewer"
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
      >
        <header className="trace-header">
          <a href={pathname === "/sources" ? "/sources" : "/"}>
            {pathname === "/sources" ? "Sources" : "Home"}
          </a>
          <span>› {data?.title ?? "Data sources"}</span>
          <button onClick={close} aria-label="Close viewer">
            ×
          </button>
        </header>
        {data?.fact && <h2 className="trace-fact">{data.fact}</h2>}
        <p className="trace-legend">
          Can feed this · dashed means no saved evidence.
        </p>
        {failed && (
          <p>
            Saved details are unavailable.{" "}
            <button
              onClick={() => {
                setFailed(false);
                setAttempt(attempt + 1);
              }}
            >
              Retry
            </button>
            , or <a href="/sources">pick a source →</a>
          </p>
        )}
        {!data && !failed && (
          <p>
            Reading the path. <button onClick={close}>Back</button>
          </p>
        )}
        {data?.state === "unknown" && (
          <p>
            No path for that link. <a href="/sources">Pick a source below →</a>
          </p>
        )}
        {data?.state === "unavailable" && (
          <p>
            This result is no longer available.{" "}
            <a href="#trace-graph">See what can feed it →</a>
          </p>
        )}
        {data &&
          paths.length > 1 &&
          (paths.length <= 6 ? (
            <nav className="trace-path-chips" aria-label="Choose a source path">
              {paths.map((path, i) => (
                <button
                  key={path.join(":")}
                  aria-pressed={active === i}
                  onClick={() => {
                    setActive(i);
                    setPeek(null);
                  }}
                >
                  {data.nodes.find((node) => node.id === path[0])?.label}
                </button>
              ))}
            </nav>
          ) : (
            <label className="trace-path-choice">
              Path
              <select
                aria-label="Choose a source path"
                value={active}
                onChange={(event) => {
                  setActive(Number(event.target.value));
                  setPeek(null);
                }}
              >
                {paths.map((path, i) => (
                  <option value={i} key={path.join(":")}>
                    {data.nodes.find((node) => node.id === path[0])?.label}
                  </option>
                ))}
              </select>
            </label>
          ))}
        <div className="trace-body">
          <div
            id="trace-graph"
            className="trace-graph"
            aria-label="Can feed this"
          >
            {stages.map((stage, index) => {
              const candidates = stageNodes[index];
              const node =
                candidates.find((node) => node.lit) ??
                candidates.find((node) => node.preview) ??
                candidates[0];
              if (!node) return null;
              const previous = stageNodes[index - 1] ?? [];
              const connected = previous.some((prior) =>
                data?.edges.some(
                  (edge) =>
                    edge.from === prior.id && edge.to === node.id && edge.lit,
                ),
              );
              const reader = readerForNode(node, nodes, data?.edges ?? []);
              const flow = reader?.source ? functionFlow(reader.source) : null;
              const sentence = stageSentence(flowStages[stage.id] ?? "Source");
              return (
                <section
                  key={stage.id}
                  className={`trace-stage ${connected ? "lit-edge" : ""}`}
                  data-stage={stage.id}
                >
                  <h3>{stage.label}</h3>
                  <Hover
                    key={`${node.id}:${peek?.id ?? ""}:${details?.id ?? ""}`}
                    fitViewport
                    interactive
                    tap
                    label={node.label}
                    card={cardFor(node)}
                  >
                    <div
                      className={`trace-node ${node.lit ? "is-lit" : ""}`}
                      data-node={node.id}
                      data-hover-sentence={sentence}
                      role="group"
                      aria-label={node.label}
                    >
                      {stage.id === "source" ? (
                        <Mark
                          name={
                            flow?.stages.find((item) => item.stage === "Source")
                              ?.mark ??
                            node.brand ??
                            "postgresql"
                          }
                        />
                      ) : (
                        <MarkImage
                          name={
                            flow?.stages.find(
                              (item) => item.stage === flowStages[stage.id],
                            )?.mark ??
                            stageMarks[stage.id] ??
                            "postgresql"
                          }
                        />
                      )}
                      {stage.id === "readers" && flow && (
                        <div className="reader-chips">
                          {flow.access && (
                            <span>
                              {flow.vendor &&
                                (marks[flow.vendor].monogram ? (
                                  <Monogram name={flow.vendor} />
                                ) : (
                                  <MarkImage name={flow.vendor} size={21} />
                                ))}
                              {flow.vendor
                                ? marks[flow.vendor].label
                                : flow.access}
                            </span>
                          )}
                          <span>{flow.job_kind}</span>
                        </div>
                      )}
                      {stage.id === "readers" && node.source && (
                        <FunctionName
                          sourceKey={node.source}
                          lastRead={node.last_read}
                          enabled={node.enabled}
                          paused={node.paused}
                          still
                        />
                      )}
                      {stage.id === "ready" && (
                        <small className="rights-badge">Rights labelled</small>
                      )}
                      <button
                        className="trace-node-action"
                        aria-label={node.label}
                      >
                        <span>
                          {stage.id === "source" && node.brand
                            ? "Details →"
                            : stage.id === "readers" && flow
                              ? "Details →"
                              : node.short}
                        </span>
                        {stage.id === "readers" && (
                          <small>
                            {!functionIsActive(node) && "When enabled: "}
                            <Schedule node={node} />
                          </small>
                        )}
                        {stage.id === "readers" &&
                          node.last_read &&
                          functionIsActive(node) && (
                            <small>Runs on the platform&apos;s servers</small>
                          )}
                        {node.count && stage.id !== "screen" && (
                          <RecordedNumber
                            compact
                            value={BigInt(node.count.value).toLocaleString(
                              "en-US",
                            )}
                            label="stored"
                            provenance={{
                              queried_at: node.count.captured_at,
                              scope: "global",
                              build: node.count.build,
                              query: node.relation ?? "",
                              sql: "",
                            }}
                          />
                        )}
                        {node.count && stage.id !== "screen" && (
                          <small>
                            <LocalTime
                              at={node.count.captured_at}
                              relativeDay
                            />
                          </small>
                        )}
                      </button>
                    </div>
                  </Hover>
                  {node.preview && stage.id !== "screen" && (
                    <button
                      className="trace-peek-button"
                      onClick={() => setPeek(node)}
                    >
                      See rows
                    </button>
                  )}
                </section>
              );
            })}
          </div>
          {peek && (
            <Peek key={peek.id} node={peek} close={() => setPeek(null)} />
          )}
        </div>
        <footer className="trace-footer">
          <span>Not cleared for training/resale.</span>
          <a href="/sources?credits=1">Mark credits →</a>
        </footer>
      </dialog>
      {details && (
        <Sheet title={details.label} close={() => setDetails(null)}>
          <div data-popover>{cardFor(details)}</div>
        </Sheet>
      )}
    </>
  );
}
