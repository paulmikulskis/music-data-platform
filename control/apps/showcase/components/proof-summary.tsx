import { readLinks } from "../server/links";
import { LinkOut } from "./link-out";
import Link from "next/link";
import type { ProofView } from "../server/proof-summary";
import { SourceLine } from "./sources";

// The plain proof summary a viewer sees first: the exact entries, when and where they were read,
// Back to the screen they came from, and the operator console as a quiet onward link.
export function ProofSummary({
  view,
  back,
  sourceKey,
}: {
  view: ProofView;
  back: string;
  sourceKey?: string;
}) {
  return (
    <section className="proof-room">
      <p className="eyebrow">Proof · {view.subject}</p>
      <h1>{view.headline}</h1>
      {view.items.length ? (
        <ul className="proof-items">
          {view.items.map((item, index) => (
            <li key={`${item.name}:${index}`}>
              <span>{item.name}</span>
              {item.note ? <small>{item.note}</small> : null}
            </li>
          ))}
          {view.more ? (
            <li className="proof-more">
              <span>{view.more.toLocaleString("en-US")} more</span>
            </li>
          ) : null}
        </ul>
      ) : null}
      {view.lines.map((line) => (
        <p key={line} className="proof-line">
          {line}
        </p>
      ))}
      {view.sources?.map((source) => (
        <p key={source.source_key} className="proof-line">
          <SourceLine source={source} />
        </p>
      ))}
      <div className="proof-actions">
        <Link prefetch={false} className="primary" data-primary href={back}>
          Back
        </Link>
        {view.engine && sourceKey ? (
          <LinkOut
            preview={readLinks().get("proof-open-console", sourceKey)}
            signedHref={view.engine}
          />
        ) : view.engine ? (
          <a className="out-link" href={view.engine}>
            Open in Console ↗
          </a>
        ) : null}
      </div>
    </section>
  );
}
