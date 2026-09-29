import Link from "next/link";
import type { ReactNode } from "react";
import type { Arrival } from "../server/models";
import type { Provenance } from "./number";
import { SongCard } from "./arrivals";
import { Status } from "./status";
import { arrivalFact, arrivalSource } from "../lib/music-facts";
import { marketPlace, placeList } from "../lib/places";
import { observedKeys } from "../lib/source-keys";
// A list with nothing to show says why in one quiet line and still offers a way on.
function QuietLine({ children }: { children: ReactNode }) {
  return (
    <article className="coming-line" data-card>
      {children}
      <Link
        prefetch={false}
        className="card-verb"
        data-primary
        href="/songs?view=rising"
      >
        Open Rising now <span>↗</span>
      </Link>
    </article>
  );
}
// Places keeps its two lists apart: new songs from established artists, then Older songs.
// Unplaced songs never reach a viewer screen.
export function Places({
  rows,
  view = "places",
  provenance,
}: {
  rows: Arrival[];
  view?: "places" | "waking";
  provenance?: Provenance;
}) {
  const established = rows.filter(
    (row) => row.movement_list === "established_entries",
  );
  const catalog = rows.filter((row) =>
    view === "waking"
      ? row.movement_list === "catalog_entries"
      : (row.new_markets?.length ?? 0) > 0,
  );
  // Established needs an artist size measure. Until one exists, the list is coming, not empty.
  const measured = rows.some((row) => row.stage_measured);
  const card = (row: Arrival) => (
    <SongCard
      key={row.song_key}
      song={row.song_key}
      title={row.title_text}
      artist={row.artist_text}
      list={row.movement_list}
      days={row.window_days}
      fact={arrivalFact(row)}
      source={arrivalSource}
      basis={row.age_basis}
      sources={observedKeys(row.evidence)}
      evidence={row.evidence}
      places={placeList(row.markets, marketPlace)}
      markets={row.markets}
      newMarkets={row.new_markets}
      provenance={provenance}
    />
  );
  return (
    <section className="places-room">
      <h1>{view === "waking" ? "Waking up" : "New places"}</h1>
      <section id="catalog" className="family-group quiet-group">
        {catalog.length ? (
          <div className="rising-feed">{catalog.map(card)}</div>
        ) : (
          <QuietLine>
            <p className="quiet-line">
              {view === "waking"
                ? "No older song entered a tracked list yet."
                : "No song entered a new place yet."}
            </p>
          </QuietLine>
        )}
      </section>
      <details className="more-song-views">
        <summary>More song views</summary>
        <section className="family-group quiet-group">
          <h2 className="family-name">established artists</h2>
          {established.length ? (
            <div className="rising-feed">{established.map(card)}</div>
          ) : (
            <QuietLine>
              {measured ? (
                <p className="quiet-line">
                  No established artist&apos;s new song entered a list yet.
                </p>
              ) : (
                <Status state="Coming" target="Needs artist audience size" />
              )}
            </QuietLine>
          )}
        </section>
      </details>
    </section>
  );
}
