"use client";
import { useEffect, useRef, useState } from "react";
import { animate, useInView, useReducedMotion } from "motion/react";
import { Hover } from "./hover";
import { useTending, type Source } from "./sources";
import { historyDays } from "./behind-card";
import { heldSources, liveList } from "../lib/rights";
type Counter = {
  key: string;
  label: string;
  value: number | null;
  line: string;
};
const sum = (sources: Source[], pick: (source: Source) => number) =>
  sources.reduce((total, source) => total + pick(source), 0);
// What we tend every day, counted from the sources' own reads.
export function tendedCounters(
  sources: Source[],
  songs: string | null,
  now = Date.now(),
): Counter[] {
  // Sources live match Holdings and Rights; entries and history leave out sample loads.
  const live = liveList(sources, now);
  const held = heldSources(sources);
  const targets = (family: string) => {
    const counted = sources.filter(
      (source) => source.enabled && source.family === family,
    );
    const measured = counted.filter(
      (source) => source.tracked?.unit === family,
    );
    return {
      value: measured.length
        ? sum(measured, (source) => source.tracked?.count ?? 0)
        : null,
      missing: [
        ...new Set(
          counted
            .filter((source) => source.tracked?.unit !== family)
            .map((source) => source.display_name),
        ),
      ],
    };
  };
  const playlists = targets("playlists");
  const charts = targets("charts");
  const omitted = (names: string[]) =>
    names.length ? ` Not counted: ${names.join(", ")}.` : "";
  return [
    {
      key: "sources",
      label: "sources live",
      value: sources.length ? live.length : null,
      line: "Sources switched on and read within their schedule. Open Sources for dates.",
    },
    {
      key: "playlists",
      label: "playlists tracked",
      value: playlists.value,
      line: `Frozen playlist membership.${omitted(playlists.missing)} Open Sources for each count’s date.`,
    },
    {
      key: "charts",
      label: "charts tracked",
      value: charts.value,
      line: `${sum(
        held.filter((source) => source.family === "charts"),
        (source) => Number(source.entries_today),
      ).toLocaleString(
        "en-US",
      )} chart appearances read today.${omitted(charts.missing)} Open Sources for each read.`,
    },
    {
      key: "songs",
      label: "songs",
      value: songs ? Number(songs) : null,
      line: "Tracked songs and unmatched copies. Open Library to find a song.",
    },
    {
      key: "entries",
      label: "appearances today",
      value: held.length
        ? sum(held, (source) => Number(source.entries_today))
        : null,
      line: "appearances read since midnight UTC.",
    },
    {
      key: "days",
      label: "days of history",
      value: historyDays(held, now),
      line: "Days since the first source was read.",
    },
  ];
}
// Counts up once, just before the counter scrolls into view.
function Count({ value }: { value: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px 120px 0px" });
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(value);
  useEffect(() => {
    if (!seen || reduced) return;
    const animation = animate(0, value, {
      duration: 0.6,
      onUpdate: (next) => setShown(Math.round(next)),
      onComplete: () => setShown(value),
    });
    return () => animation.stop();
  }, [seen, reduced, value]);
  return (
    <span ref={ref} className="count">
      {(seen && !reduced ? shown : value).toLocaleString("en-US")}
    </span>
  );
}
export function Tending() {
  const tending = useTending();
  const counters = tendedCounters(
    tending?.sources ?? [],
    tending?.songs ?? null,
  );
  return (
    <section className="tending" aria-label="What we tend every day">
      <p className="eyebrow">what we tend every day</p>
      <div className="tending-grid">
        {counters.map((counter) => (
          <Hover
            key={counter.key}
            label={counter.label}
            card={<p className="hover-line">{counter.line}</p>}
            tap
          >
            <button
              type="button"
              className="tending-counter"
              data-counter={counter.key}
            >
              {counter.value === null ? (
                <small>not measured yet</small>
              ) : (
                <Count value={counter.value} />
              )}
              <span>{counter.label}</span>
            </button>
          </Hover>
        ))}
      </div>
    </section>
  );
}
