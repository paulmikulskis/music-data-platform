"use client";
import { useState } from "react";
import { useTending, SourceCard } from "./sources";
import { sourceFamilies, type SourceFamily } from "../lib/source-families";
import { Sheet } from "./sheet";
import { FunctionFlow, FunctionName } from "./flow";
import { MarkCredits } from "./stack/credits";
import { reviewedSources, reviewedReaderKeys } from "../lib/reviewed-sources";
import { traceLink } from "../lib/trace";
import { tendedCounters } from "./tending";
import { exampleSources } from "../lib/rights";
import { marks } from "../lib/marks";
export function SourceList({ credits = false }: { credits?: boolean }) {
  const sources = (useTending()?.sources ?? []).filter(
    (source) => !exampleSources.has(source.source_key),
  );
  const families = sourceFamilies(sources);
  const [selected, setSelected] = useState<SourceFamily | null>(null);
  const [showCredits, setShowCredits] = useState(credits);
  const [expanded, setExpanded] = useState(false);
  const totals = tendedCounters(sources, null).filter(
    (counter) => counter.key === "playlists" || counter.key === "charts",
  );
  return (
    <section
      className={`sources-page ${expanded ? "sources-expanded" : "sources-compact"}`}
    >
      <h1>Where it comes from</h1>
      <nav className="chips">
        <a aria-current="true" href="/sources">
          Sources
        </a>
      </nav>
      <div
        className={`reviewed-sources ${sources.length ? "" : "sources-saved"}`}
      >
        {sources.length ? (
          families
            .filter((family) => expanded || family.enabled)
            .map((family) => {
              const source = family.primary;
              return (
                <article
                  key={family.key}
                  data-source-family={family.key}
                  className={`reviewed-source ${family.enabled ? "" : "honesty-off"}`}
                >
                  <FunctionFlow sourceKey={source.source_key} compact />
                  {family.readers.map((reader) => (
                    <FunctionName
                      key={reader.source_key}
                      sourceKey={reader.source_key}
                    />
                  ))}
                  <p>{family.label}</p>
                  {reviewedReaderKeys.has(source.source_key) ? (
                    <a
                      href={traceLink(
                        { entry: `source.${source.source_key}` },
                        "/sources",
                      )}
                    >
                      Trace →
                    </a>
                  ) : null}
                  <button
                    aria-label={`Source details: ${family.name}`}
                    title="Source details"
                    onClick={() => setSelected(family)}
                  >
                    ⓘ
                  </button>
                </article>
              );
            })
        ) : (
          <>
            <p className="source-unavailable">
              Live states unavailable. <a href="/sources">Retry</a>
            </p>
            {reviewedSources.map((node) => (
              <article key={node.id} className="reviewed-source">
                <h2>{node.label}</h2>
                <p>Not measured yet.</p>
                <a
                  href={
                    node.readers[0]
                      ? traceLink(
                          { entry: `source.${node.readers[0]}` },
                          "/sources",
                        )
                      : "/sources"
                  }
                >
                  Source details →
                </a>
              </article>
            ))}
          </>
        )}
      </div>
      {!expanded &&
        (families.some((family) => !family.enabled) || !sources.length) && (
          <button className="more-sources" onClick={() => setExpanded(true)}>
            Show all sources
          </button>
        )}
      <div className="source-totals">
        {totals
          .filter((total) => total.value !== null)
          .map((total) => (
            <p key={total.key}>
              {total.value?.toLocaleString("en-US")} {total.label} · switched-on
              readers
            </p>
          ))}
        <p>
          Each source keeps its own count and reading date.{" "}
        </p>
      </div>
      <p className="mark-credit">
        No affiliation with the companies shown.{" "}
        <button onClick={() => setShowCredits(true)}>Credits →</button>
      </p>
      {selected && (
        <Sheet title={selected.name} close={() => setSelected(null)}>
          <FamilyDetails key={selected.key} family={selected} />
        </Sheet>
      )}
      {showCredits && (
        <Sheet title="Credits" close={() => setShowCredits(false)}>
          <MarkCredits />
          <details>
            <summary>Provider files and rules</summary>
            {Object.entries(marks).map(([key, mark]) => (
              <p key={key}>
                <a href={mark.href} target="_blank" rel="noopener noreferrer">
                  {mark.label} →
                </a>
                {" · "}
                {mark.owner}. {mark.rule} {mark.changed ?? "Unchanged."}{" "}
                <a
                  href={mark.source_url}
                  target={
                    mark.source_url.startsWith("https:") ? "_blank" : undefined
                  }
                  rel="noopener noreferrer"
                >
                  Source file →
                </a>
              </p>
            ))}
            <a href="/brand/third-party/LICENSES.md">Read all mark credits →</a>
          </details>
          <a href="/sources">See sources →</a>
        </Sheet>
      )}
    </section>
  );
}

function FamilyDetails({ family }: { family: SourceFamily }) {
  const [reader, setReader] = useState(family.primary);
  return (
    <>
      {family.readers.length > 1 && (
        <nav className="chips" aria-label="Reader variants">
          {family.readers.map((source) => (
            <button
              key={source.source_key}
              aria-pressed={reader.source_key === source.source_key}
              onClick={() => setReader(source)}
            >
              {source.source_key.endsWith("_weekly")
                ? "Weekly targets"
                : "Daily targets"}
            </button>
          ))}
        </nav>
      )}
      <SourceCard source={reader} />
    </>
  );
}
