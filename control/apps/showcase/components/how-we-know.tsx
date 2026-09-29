"use client";
import { useState } from "react";
import { cycleProofLink } from "../lib/proof-link";
import type { Provenance } from "./number";
import {
  SourceBadges,
  Sparkline,
  targetsLabel,
  useTending,
  type Source,
} from "./sources";
import { CityMap } from "./city-map";
import { clockLabel } from "../lib/presentation";
import { LocalTime, useViewerZone } from "./local-time";
// A reading time uses the viewer zone after hydration.
export function when(source: Source, now = Date.now(), zone?: string) {
  return source.last_read ? clockLabel(source.last_read, now, zone) : "not yet";
}
function picked(sources: Source[] | undefined, keys: string[] | undefined) {
  return (sources ?? []).filter((source) => keys?.includes(source.source_key));
}
// One trail line: "We track 58 Shazam charts, read today at 03:12 UTC." The tracked count never
// claims every one was read. Without a count: "We read Spotify play counts today at 03:12 UTC."
export function trail(source: Source, now = Date.now(), zone?: string) {
  const count = targetsLabel(source);
  if (!count)
    return `We read ${source.display_name} ${when(source, now, zone)}.`;
  const read = source.last_read
    ? `read ${when(source, now, zone)}`
    : "not read yet";
  const at = source.tracked
    ? clockLabel(source.tracked.as_of, now, zone)
    : "not measured yet";
  return `${source.display_name}: ${count} tracked at ${at}; ${read}.`;
}
// Entries read each day, summed across the number's sources.
function daily(sources: Source[]) {
  const days = sources[0]?.days.map((day) => day.day) ?? [];
  return days.map((day) =>
    sources.reduce(
      (sum, source) =>
        sum +
        Number(source.days.find((item) => item.day === day)?.entries ?? 0),
      0,
    ),
  );
}
// The hover card: badges, one plain sentence, when it was read, and a tiny curve.
export function HowCard({ provenance }: { provenance?: Provenance }) {
  const tending = useTending();
  const sources = picked(tending?.sources, provenance?.sources);
  const time = provenance?.build?.built_at ?? provenance?.queried_at;
  return (
    <div className="how-card">
      {provenance?.sources?.length ? (
        <SourceBadges keys={provenance.sources} still />
      ) : null}
      <p>
        {provenance?.plain ??
          "Where this number comes from is not measured yet."}
      </p>
      <p className="how-meta">
        {time ? (
          <>
            Read <LocalTime at={time} relativeDay />
          </>
        ) : (
          "Read time not measured yet"
        )}
        {sources.length ? (
          <Sparkline
            values={daily(sources)}
            label="appearances read each day, last two weeks"
          />
        ) : null}
      </p>
    </div>
  );
}
// The sheet: the trail in plain words, a map or a timeline, and the ways out.
export function HowSheet({
  label,
  provenance,
}: {
  label: string;
  provenance?: Provenance;
}) {
  const tending = useTending();
  const sources = picked(tending?.sources, provenance?.sources).slice(0, 2);
  const zone = useViewerZone();
  const [now] = useState(Date.now);
  return (
    <>
      <h2>{label}</h2>
      {sources.map((source) => (
        <p key={source.source_key}>{trail(source, now, zone)}</p>
      ))}
      <p>{provenance?.query ?? "Source and time not measured yet."}</p>
      {provenance?.places?.length ? (
        <CityMap places={provenance.places} />
      ) : sources.length ? (
        <Sparkline
          values={daily(sources)}
          label="appearances read each day, last two weeks"
        />
      ) : null}
      <div className="sheet-links">
        <a
          className="primary"
          href={
            provenance?.next?.href ??
            cycleProofLink(provenance?.build, "/songs?view=rising", {
              sources: provenance?.sources,
            })
          }
        >
          {provenance?.next?.label ?? "See the appearances"} →
        </a>
        {provenance?.links?.map((link) => (
          <a
            key={link.href}
            className="out-link"
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
          >
            {link.label} ↗
          </a>
        ))}
      </div>
    </>
  );
}
