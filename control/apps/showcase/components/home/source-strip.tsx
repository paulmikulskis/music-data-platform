"use client";
import { targetsLabel, useTending } from "../sources";
import { FunctionFlow, FunctionName, FunctionCardRows } from "../flow";
import { Hover } from "../hover";
import { LocalTime } from "../local-time";
import { traceLink } from "../../lib/trace";
import { sourceProofLink as savedSourceLink } from "../../lib/proof-link";
import {
  reviewedSources,
  reviewedReaderKeys,
} from "../../lib/reviewed-sources";
const sourceProofLink = (source: string) =>
  reviewedReaderKeys.has(source)
    ? traceLink({ entry: `source.${source}` })
    : savedSourceLink(source);

import {
  sourceFamilies,
  liveProviderFamilies,
} from "../../lib/source-families";
export function SourceStrip() {
  const families = sourceFamilies(useTending()?.sources ?? []);
  const live = liveProviderFamilies(families);
  // The shipped dependency catalog supplies discoverable names when the live read is unavailable.
  const saved = reviewedSources.slice(0, 5);
  return (
    <section className="home-sources">
      <h2>Where it comes from</h2>
      <div className="source-strip">
        {live.length
          ? live.map(({ primary: source, readers, label }) => (
              <div className="source-tile" key={source.source_key}>
                <Hover
                  label={source.display_name}
                  interactive
                  fitViewport
                  card={
                    <>
                      <p>{label}</p>
                      <p>
                        {source.tracked
                          ? `${targetsLabel(source)} tracked.`
                          : "Counts not measured yet."}
                      </p>
                      {source.last_read && (
                        <p>
                          Read <LocalTime at={source.last_read} />
                        </p>
                      )}
                      {readers.map((reader) => (
                        <section key={reader.source_key}>
                          <FunctionName sourceKey={reader.source_key} still />
                          <FunctionCardRows sourceKey={reader.source_key} />
                        </section>
                      ))}
                    </>
                  }
                >
                  <span className="source-flow-family">
                    <FunctionFlow sourceKey={source.source_key} compact />
                    {readers.map((reader) => (
                      <FunctionName
                        key={reader.source_key}
                        sourceKey={reader.source_key}
                        still
                      />
                    ))}
                  </span>
                </Hover>
                <a href={sourceProofLink(source.source_key)}>Trace →</a>
              </div>
            ))
          : saved.map((node) => (
              <a key={node.id} href="/sources">
                {node.label}
              </a>
            ))}
      </div>
      <p className="source-off">
        {families
          .filter((family) => !family.enabled)
          .slice(0, 3)
          .map((family) => (
            <a
              key={family.key}
              href={sourceProofLink(family.primary.source_key)}
            >
              {family.name} · {family.label}
            </a>
          ))}
      </p>
      {!families.length && (
        <p>
          Counts not measured yet.{" "}
          <a href="/sources">View saved source details →</a>
        </p>
      )}
      <a href="/sources">See sources →</a>
      <small className="mark-credit">
        No affiliation with the companies shown.{" "}
        <a href="/sources?credits=1">Credits →</a>
      </small>
    </section>
  );
}
