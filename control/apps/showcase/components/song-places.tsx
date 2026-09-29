"use client";
import type { Build, Place } from "./number";
import { cycleProofLink, sourceProofLink } from "../lib/proof-link";
import { useState } from "react";
import type { SongPlace } from "../server/models";
import { placeCounts, placeName } from "../lib/places";
import { Sheet } from "./sheet";
import { CityMap } from "./city-map";
// Every Shazam chart the song reached in the last 28 days: a map, one count line and the names.
// The Cities lane counts one day; this sheet counts the whole window and says so.
export function SongPlaces({
  places,
  map = [],
  build,
  song,
}: {
  // Null when the read failed.
  places: SongPlace[] | null;
  map?: Place[];
  build?: Build;
  song?: string;
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  return (
    <>
      <button onClick={() => setOpen(true)}>Places →</button>
      {open && (
        <Sheet
          className="call-sheet"
          title="Places"
          close={() => setOpen(false)}
        >
          {places === null ? (
            <p>
              Places · not measured yet.{" "}
              <a href={song ? `/s/song/${encodeURIComponent(song)}` : ""}>
                Retry
              </a>
            </p>
          ) : (
            <p>{placeCounts(places)}</p>
          )}
          {map.length > 0 && <CityMap places={map} caption={null} />}
          <div className="place-names">
            {(places ?? []).slice(page * 6, page * 6 + 6).map((p, i) => (
              <span
                key={i}
                data-call-place={JSON.stringify({
                  country: p.country,
                  city: p.city,
                })}
              >
                {placeName(p)}
              </span>
            ))}
          </div>
          {page > 0 && (
            <button onClick={() => setPage(page - 1)}>Previous places</button>
          )}
          {(places?.length ?? 0) > (page + 1) * 6 && (
            <button onClick={() => setPage(page + 1)}>More places</button>
          )}
          <a
            className="primary"
            href={cycleProofLink(build, sourceProofLink("sz_chart"), { song })}
          >
            See the charts
          </a>
        </Sheet>
      )}
    </>
  );
}
