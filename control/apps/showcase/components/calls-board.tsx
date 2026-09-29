"use client";
import { sourceProofLink } from "../lib/proof-link";
import Link from "next/link";
import { useState } from "react";
import type { SavedCall } from "../lib/calls";
import type { CallRead } from "../server/call-reads";
import type { Provenance } from "./number";
import type { Readiness } from "../server/models";
import type { HomeCalls } from "../server/call-offers";
import { earlierWeek, CALL_WEEK_DAY } from "../lib/calls";
import { Art } from "./movers";
import { Sheet } from "./sheet";
import { Number } from "./number";
// What a call is, then why this week is empty and where calls open.
const emptyWeek: Record<
  HomeCalls,
  { text: string; href: (week: string) => string; action: string }
> = {
  open: {
    text: "Save a song you expect to spread.",
    href: () => "/songs?view=rising",
    action: "Open Rising now",
  },
  used: {
    text: "Save a song you expect to spread. This week's pick limit is reached; try again next week.",
    href: (week) => `/songs?view=picks&week=${earlierWeek(week)}`,
    action: "Earlier weeks",
  },
  waiting: {
    text: "Save a song you expect to spread. Picks open after the next daily read.",
    href: () => "/",
    action: "Open Home",
  },
  no_songs: {
    text: "Save a song you expect to spread. Home has no song to pick yet.",
    href: () => "/songs?view=rising",
    action: "Open Rising now",
  },
  unknown: {
    text: "Save a song you expect to spread. Pick this song shows on songs when picks are open.",
    href: () => "/",
    action: "Open Home",
  },
};
export function CallsBoard({
  calls,
  observations,
  handle,
  week,
  current,
  history,
  provenance,
  historyProvenance,
  callable = "unknown",
}: {
  calls: SavedCall[];
  observations: CallRead | null;
  handle: string;
  week: string;
  // True when the board shows this Friday week; an earlier week names its Friday.
  current: boolean;
  history: Readiness[];
  provenance?: Provenance;
  historyProvenance?: Provenance;
  // Whether a song can be called now; an empty week says so.
  callable?: HomeCalls;
}) {
  const [family, setFamily] = useState<Readiness | null>(null);
  const active = calls.filter((c) => !c.undone_at && !c.hidden_at);
  return (
    <section className="calls-room">
      <h1>Your picks</h1>
      {!current && (
        <p className="board-week">
          Week from {CALL_WEEK_DAY}{" "}
          {new Date(`${week}T12:00:00Z`).toLocaleDateString("en-GB", {
            day: "numeric",
            month: "short",
            timeZone: "UTC",
          })}
          .
        </p>
      )}
      {!active.length ? (
        current ? (
          <article data-card className="empty">
            <p>{emptyWeek[callable].text}</p>
            <Link
              prefetch={false}
              data-primary
              className="primary"
              href={emptyWeek[callable].href(week)}
            >
              {emptyWeek[callable].action}
            </Link>
          </article>
        ) : (
          <article data-card className="empty">
            <p>No picks that week.</p>
            <Link
              prefetch={false}
              data-primary
              className="primary"
              href="/songs?view=picks"
            >
              Open this week
            </Link>
          </article>
        )
      ) : (
        <>
          {["yours", "other your picks", "rules"].map((label) => {
            const group = active.filter((c) =>
              label === "rules"
                ? c.author === "rules"
                : label === "yours"
                  ? c.author === handle
                  : c.author !== handle && c.author !== "rules",
            );
            // A group with no calls this week shows nothing, not an empty row.
            if (!group.length) return null;
            return (
              <section key={label} className="call-group">
                <h2>{label}</h2>
                <div className="call-tiles">
                  {group.map((call) => {
                    const row = observations?.rows.find(
                      (r) => r.id === call.id,
                    );
                    return (
                      <Link
                        prefetch={false}
                        className="call-tile"
                        key={call.id}
                        href={`/songs/picks/${call.id}`}
                        aria-label={`Open pick: ${row?.title ?? "song"}`}
                      >
                        <Art
                          sharedLayout={false}
                          song={call.song_key}
                          title={row?.title ?? "song"}
                        />
                        {row && BigInt(row.total) > 0n && <span>new</span>}
                      </Link>
                    );
                  })}
                </div>
              </section>
            );
          })}
          {!observations && (
            <p>
              Places load when the platform answers.{" "}
              <a href="/songs?view=picks">Retry</a>
            </p>
          )}
        </>
      )}
      <div className="board-links">
        <Link prefetch={false} href="/songs?view=friday">
          Weekly picks →
        </Link>
        <Link
          prefetch={false}
          href={`/songs?view=picks&week=${earlierWeek(week)}`}
        >
          Earlier weeks →
        </Link>
        <a href="#calls-history">History ↓</a>
      </div>
      <section className="calls-history" id="calls-history">
        <h2>history.</h2>
        {history
          .filter((r) => ["shazam", "playlists"].includes(r.family ?? ""))
          .map((row) => (
            <button key={row.family} onClick={() => setFamily(row)}>
              <span>{row.family === "shazam" ? "Shazam" : "Playlists"}</span>
              <span className="history-dots" aria-hidden="true">
                {Array.from(
                  { length: Math.min(28, globalThis.Number(row.history_days)) },
                  (_, i) => (
                    <i key={i} />
                  ),
                )}
              </span>
            </button>
          ))}
        <Link prefetch={false} href="/songs?view=rising">
          Open Rising now →
        </Link>
      </section>
      {family && (
        <Sheet
          className="call-sheet"
          title="History"
          close={() => setFamily(null)}
        >
          <Number
            compact
            value={String(family.history_days)}
            label={`${globalThis.Number(family.history_days) === 1 ? "day" : "days"} of ${family.family === "shazam" ? "Shazam charts" : "playlists"} read`}
            provenance={historyProvenance ?? provenance}
          />
          <a
            className="primary"
            href={sourceProofLink(
              family.family === "shazam" ? "sz_chart" : "sp_playlist",
            )}
          >
            See the appearances
          </a>
        </Sheet>
      )}
    </section>
  );
}
