"use client";
import { LocalTime } from "./local-time";
import Link from "next/link";
import { CallIt, useCallOffer } from "./call-it";
import { useState } from "react";
import { motion } from "motion/react";
import type { ArrivalSummary, Locator } from "../server/models";
import { cycleProofLink } from "../lib/proof-link";
import type { Provenance } from "./number";
import { Art, Empty } from "./movers";
import { Metric } from "./metric";
import { Sheet } from "./sheet";
import { CardMeta } from "./card-meta";
import { ageSource, countryName, overDays } from "../lib/music-facts";
import { shortLabel } from "../lib/presentation";
import { ArtistName } from "./artist";
import { Behind } from "./behind-card";
import { SourceBadges } from "./sources";
import { Hover } from "./hover";
import { CityMap } from "./city-map";
import { marketPlace, placeList } from "../lib/places";
import type { Place } from "./number";
const songHref = (song: string) => `/s/song/${encodeURIComponent(song)}`;
// Home while no song moves on two sources: how many new songs arrived, led by one song.
export function Arrivals({
  summary,
  provenance,
}: {
  summary: ArrivalSummary;
  provenance: Provenance;
}) {
  const lead = summary.lead;
  const offer = useCallOffer(lead?.song_key ?? "");
  if (!lead || BigInt(summary.songs) === 0n)
    return (
      <Empty
        title="a quiet window."
        detail="No new song entered a tracked list yet."
        href="/songs?view=rising"
        action="Open Rising now"
      />
    );
  const discovery = lead.discovery.length > 0;
  return (
    <article className="arrivals" data-card>
      <Behind keys={provenance.sources ?? []} evidence={lead.evidence} />
      <div className="arrivals-count">
        <Metric
          value={BigInt(summary.songs).toLocaleString("en-US")}
          label="new songs"
          provenance={provenance}
        />
        <div className="card-top">
          <CardMeta
            list={lead.movement_list}
            days={summary.window_days}
            basis={lead.age_basis}
          />
          <SourceBadges keys={provenance.sources ?? []} max={3} />
        </div>
      </div>
      <Link
        prefetch={false}
        draggable={false}
        className="arrival-lead"
        href={songHref(lead.song_key)}
        aria-label={`Open ${lead.title_text ?? "song"}`}
      >
        <Art song={lead.song_key} title={lead.title_text ?? "song"} />
        <span className="arrival-caption">
          <span className="eyebrow">
            {discovery ? "Shazam Discovery" : "Leading arrival"}
          </span>
          <span className="artist">
            {shortLabel(lead.artist_text ?? "Artist unknown", 2)}
          </span>
          <strong>{shortLabel(lead.title_text ?? "Untitled song", 3)}</strong>
          {discovery && (
            <Hover
              label="Shazam Discovery"
              card={
                <CityMap
                  places={placeList(lead.discovery.slice(0, 3), marketPlace)}
                />
              }
            >
              <span className="countries">
                {lead.discovery.slice(0, 3).map(countryName).join(" · ")}
              </span>
            </Hover>
          )}
        </span>
      </Link>
      {offer ? (
        <CallIt song={lead.song_key} />
      ) : (
        <Link
          prefetch={false}
          className="card-verb"
          data-primary
          href="/songs?view=rising"
        >
          Open Rising now <span>↗</span>
        </Link>
      )}
    </article>
  );
}
// One song in a Rising or Places list. The fact carries the list's plain number.
export function SongCard({
  song,
  title,
  artist,
  list,
  days,
  fact,
  source,
  basis,
  sources = [],
  evidence = [],
  places = [],
  markets = [],
  newMarkets = null,
  provenance,
  showWindow = true,
}: {
  song: string;
  title: string | null;
  artist: string | null;
  list: string;
  days: number | null;
  fact: string;
  source: string;
  basis: string | null;
  sources?: string[];
  evidence?: Locator[];
  places?: Place[];
  markets?: string[];
  newMarkets?: string[] | null;
  provenance?: Provenance;
  showWindow?: boolean;
}) {
  const offer = useCallOffer(song);
  const [open, setOpen] = useState(false);
  const time = provenance?.build?.built_at;
  return (
    <motion.article
      className="mover song-card"
      data-card
      initial={false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22 }}
    >
      <div className="mover-face">
        <Link
          prefetch={false}
          draggable={false}
          className="cover-link"
          href={songHref(song)}
          aria-label={`Open ${title ?? "song"}`}
        >
          <Art song={song} title={title ?? "song"} />
        </Link>
        <Behind keys={sources} evidence={evidence} />
        <div className="mover-caption">
          <div className="card-top">
            <CardMeta
              list={list}
              days={days}
              basis={basis}
              showWindow={showWindow}
            />
            <SourceBadges keys={sources} max={3} />
          </div>
          <p className="artist-line">
            <ArtistName song={song} name={artist} />
          </p>
          <h2>
            <Link prefetch={false} href={songHref(song)}>
              {shortLabel(title ?? "Untitled song", 4)}
            </Link>
          </h2>
          {places.length ? (
            <Hover
              label="Where it is spreading"
              card={<CityMap places={places} />}
            >
              <button className="card-fact" onClick={() => setOpen(true)}>
                {fact}
              </button>
            </Hover>
          ) : (
            <button className="card-fact" onClick={() => setOpen(true)}>
              {fact}
            </button>
          )}
          {offer ? (
            <CallIt song={song} />
          ) : (
            <Link
              prefetch={false}
              className="card-verb"
              data-primary
              href={songHref(song)}
            >
              Open song <span>↗</span>
            </Link>
          )}
        </div>
      </div>
      {open && (
        <Sheet title="Where this came from" close={() => setOpen(false)}>
          <h2>{fact}</h2>
          <p>{source}</p>
          {places.length ? (
            <CityMap
              places={places}
              caption={markets.length ? null : undefined}
            />
          ) : null}
          {markets.length > 0 &&
            (newMarkets ? (
              <ul className="market-list">
                {[...new Set(newMarkets)].map((market) => (
                  <li key={market}>
                    {marketPlace(market)?.name ?? countryName(market)} ·{" "}
                    <strong>New</strong>
                  </li>
                ))}
              </ul>
            ) : (
              <p>
                The new markets could not be checked. Open the proof for the
                recorded places.
              </p>
            ))}
          <p>{ageSource(basis)}</p>
          <p>
            {`Collected ${overDays(days)}. `}
            {time ? (
              <>
                Updated <LocalTime at={time} relativeDay />.
              </>
            ) : (
              "Update time not measured yet. Open the proof to check it."
            )}
          </p>
          <a
            className="primary"
            href={cycleProofLink(
              provenance?.build,
              ["established_entries", "catalog_entries"].includes(list)
                ? "/songs?view=places"
                : "/songs?view=rising",
              { song },
            )}
          >
            See proof →
          </a>
        </Sheet>
      )}
    </motion.article>
  );
}
