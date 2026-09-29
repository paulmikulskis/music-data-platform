"use client";
import { useState } from "react";
import type { EarlySignal, Mover } from "../server/models";
import type { Provenance } from "./number";
import { Metric } from "./metric";
import { Empty, MoverCard } from "./movers";
import { SongCard } from "./arrivals";
import { earlyFact, earlySource, familyName } from "../lib/music-facts";
import { observedKeys } from "../lib/source-keys";
import Link from "next/link";
const filters = [
  ["all", "All"],
  ["playlist", "Playlists"],
  ["shazam", "Shazam cities"],
  ["stream", "Plays"],
];
const families = ["playlists", "shazam", "streams"];
// Early signals stay visible while fewer than six songs move on two sources.
export type Early = {
  rows: EarlySignal[];
  ranking: string | null;
  provenance?: Provenance;
};
export function Rising({
  movers,
  days,
  early,
  stageCoverage,
  stageProvenance,
  playsReady = false,
}: {
  movers: Mover[];
  days: 7 | 28;
  early?: Early;
  stageCoverage?: string | null;
  stageProvenance?: Provenance;
  playsReady?: boolean;
}) {
  const [filter, setFilter] = useState("all");
  const shown = movers.filter(
    (m) =>
      filter === "all" || m.evidence.some((e) => e.component.includes(filter)),
  );
  const fallback = movers.length < 6 && early ? early : null;
  const hasEarly = fallback?.rows.some(
    (row) => filter === "all" || row.family.includes(filter),
  );
  return (
    <section className="rising-room">
      <h1>Rising now</h1>
      <div className="chips">
        <Link
          prefetch={false}
          aria-current={days === 7 ? "page" : undefined}
          href="/songs?view=rising"
        >
          Now
        </Link>
        <Link
          prefetch={false}
          aria-current={days === 28 ? "page" : undefined}
          aria-label="Recent: last 28 days"
          href="/songs?view=rising&days=28"
        >
          Recent
        </Link>
        {filters
          .filter(([key]) => key !== "stream" || playsReady)
          .map(([key, label]) => (
            <button
              aria-pressed={filter === key}
              key={key}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
      </div>
      {stageCoverage && (
        <p className="quiet-line">
          <Metric
            value={stageCoverage}
            label=""
            provenance={stageProvenance}
            inline
          />
        </p>
      )}
      {(shown.length > 0 || !hasEarly) && (
        <div className="rising-feed">
          {shown.length ? (
            shown.map((m) => <MoverCard key={m.song_key} mover={m} />)
          ) : (
            <Empty
              title="a quiet window."
              detail={
                filter === "stream"
                  ? "No song has enough recent play growth to appear here."
                  : "No songs match this filter yet."
              }
              href="/songs?view=rising"
              action="Show all movers"
            />
          )}
        </div>
      )}
      {fallback && hasEarly && (
        <div className="early-feed">
          <EarlyFeed
            early={fallback}
            filter={filter}
            showWindow={!stageCoverage}
          />
        </div>
      )}
    </section>
  );
}
function EarlyFeed({
  early,
  filter,
  showWindow,
}: {
  early: Early;
  filter: string;
  showWindow: boolean;
}) {
  const groups = families
    .filter((family) => filter === "all" || family.includes(filter))
    .map((family) => ({
      family,
      rows: early.rows.filter((row) => row.family === family),
    }))
    .filter((group) => group.rows.length);
  if (!groups.length)
    return (
      <div className="rising-feed">
        <Empty
          title="a quiet window."
          detail="No new song moves on one source yet."
          href="/songs?view=rising"
          action="Refresh"
        />
      </div>
    );
  return (
    <>
      <p className="quiet-line">rising on one kind of list so far</p>
      {groups.map((group) => (
        <section key={group.family} className="family-group">
          <h2 className="family-name">{familyName(group.family)}</h2>
          <div className="rising-feed">
            {group.rows.map((row) => (
              <SongCard
                key={row.song_key}
                song={row.song_key}
                title={row.title_text}
                artist={row.artist_text}
                list={row.movement_list}
                days={row.window_days}
                showWindow={showWindow}
                fact={earlyFact(row)}
                source={earlySource(row.component)}
                basis={row.age_basis}
                sources={observedKeys(row.evidence)}
                evidence={row.evidence}
                provenance={early.provenance}
              />
            ))}
          </div>
        </section>
      ))}
      {early.ranking && <p className="ranking-line">{early.ranking}</p>}
    </>
  );
}
