"use client";
import { useEffect, useState } from "react";
import type { SongArtist } from "../server/models";
import { artistLink } from "../lib/brands";
import { shortDate } from "../lib/music-facts";
import { Hover } from "./hover";
import { SourceBadges, useTending } from "./sources";
type Credit = {
  author: string;
  license: string;
  license_url: string | null;
  page: string;
};
const https = (value: unknown, host?: string) => {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (!host || url.hostname === host);
  } catch {
    return false;
  }
};
// A credit counts only with an author, a license and an https Commons file page.
function isCredit(value: unknown): value is { credit: Credit } {
  if (typeof value !== "object" || value === null || !("credit" in value))
    return false;
  const credit = value.credit;
  if (
    typeof credit !== "object" ||
    credit === null ||
    !("author" in credit) ||
    !("license" in credit) ||
    !("page" in credit) ||
    !("license_url" in credit)
  )
    return false;
  return (
    typeof credit.author === "string" &&
    typeof credit.license === "string" &&
    credit.license.length > 0 &&
    https(credit.page, "commons.wikimedia.org") &&
    (credit.license_url === null || https(credit.license_url))
  );
}
export function monogram(name: string) {
  return (
    name
      .split(/[\s,&]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((word) => word.charAt(0).toUpperCase())
      .join("") || "·"
  );
}
// The artist card: a Wikimedia photo with its credit, else a monogram; where they show up; first seen.
// While a Library sheet is still reading, the first-seen line waits instead of saying 'not measured yet'.
export function ArtistCard({
  name,
  artist,
  reading = false,
}: {
  name: string;
  artist?: SongArtist;
  reading?: boolean;
}) {
  const [credit, setCredit] = useState<Credit | null>(null);
  const [failed, setFailed] = useState(!artist?.wikidata_qid);
  const qid = artist?.wikidata_qid;
  useEffect(() => {
    if (!qid) return;
    const controller = new AbortController();
    fetch(`/artist-photo/${encodeURIComponent(qid)}/credit`, {
      signal: controller.signal,
      cache: "no-store",
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((body: unknown) => {
        if (isCredit(body)) setCredit(body.credit);
        else setFailed(true);
      })
      .catch(() => setFailed(true));
    return () => controller.abort();
  }, [qid]);
  const link = artist ? artistLink(artist.platform, artist.artist_id) : null;
  // The photo shows only once its author, license and Commons page have loaded.
  return (
    <div className="artist-card">
      <div className="artist-face">
        {qid && credit && !failed ? (
          <img
            src={`/artist-photo/${encodeURIComponent(qid)}`}
            alt={`Photo of ${name}`}
            onError={() => setFailed(true)}
          />
        ) : (
          <span className="monogram" aria-hidden="true">
            {monogram(name)}
          </span>
        )}
        <div>
          <strong>{name}</strong>
          {credit ? (
            <small className="credit">
              <a href={credit.page} target="_blank" rel="noopener noreferrer">
                Photo: {credit.author}
              </a>{" "}
              ·{" "}
              {credit.license_url ? (
                <a
                  href={credit.license_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {credit.license}
                </a>
              ) : (
                credit.license
              )}
            </small>
          ) : null}
        </div>
      </div>
      {artist?.source_keys.length ? (
        <p className="artist-where">
          showing up on <SourceBadges keys={artist.source_keys} still />
        </p>
      ) : null}
      {reading ? null : (
        <p className="artist-where">
          {artist?.first_seen
            ? `first seen ${shortDate(artist.first_seen)}`
            : "first seen · not measured yet"}
        </p>
      )}
      {link ? (
        <a
          className="card-verb"
          href={link.href}
          target="_blank"
          rel="noopener noreferrer"
        >
          {link.label} <span>↗</span>
        </a>
      ) : null}
    </div>
  );
}
// An artist name. Hover shows the artist card; a click, Enter, Space or press and hold opens it as a sheet.
export function ArtistName({
  song,
  name,
  words = 3,
}: {
  song: string;
  name: string | null;
  words?: number;
}) {
  const tending = useTending();
  const label = name ?? "Artist unknown";
  const short =
    label.trim().split(/\s+/).length > words
      ? `${label.trim().split(/\s+/).slice(0, words).join(" ")}…`
      : label;
  if (!name) return <span className="artist">{label}</span>;
  return (
    <Hover
      label={name}
      card={<ArtistCard name={name} artist={tending?.artists?.[song]} />}
      className="artist-trigger"
      tap
      interactive
    >
      <button
        type="button"
        className="artist"
        aria-label={`${name}. Open artist card`}
      >
        {short}
      </button>
    </Hover>
  );
}
