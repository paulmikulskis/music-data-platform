"use client";
import { z } from "zod";
import { useCallback, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Mover, ArrivalSummary } from "../../server/models";
import type { Provenance } from "../number";
import { Art } from "../movers";
import { Metric } from "../metric";
import { musicFacts } from "../../lib/music-facts";
import { cycleProofLink } from "../../lib/proof-link";
import { traceLink } from "../../lib/trace";
import { SourceBadges } from "../sources";
import { observedKeys } from "../../lib/source-keys";
import { useNight } from "./last-night";
export function CoverStrip({
  movers,
  arrival,
  provenance,
  unavailable,
  colors,
}: {
  movers: Mover[];
  arrival?: ArrivalSummary;
  provenance?: Provenance;
  unavailable: boolean;
  colors: Record<string, string>;
}) {
  const router = useRouter();
  const [palettes, setPalettes] = useState(colors);
  const [visibleArt, setVisibleArt] = useState<Record<string, boolean>>({});
  const artReady = useCallback((song: string) => {
    setVisibleArt((current) => ({ ...current, [song]: true }));
    void fetch(`/art/${encodeURIComponent(song)}?palette=1`)
      .then(async (response) => {
        if (!response.ok) return;
        const palette = z
          .object({ color: z.string().nullable() })
          .parse(await response.json());
        if (palette.color)
          setPalettes((current) => ({
            ...current,
            [song]: palette.color ?? "transparent",
          }));
      })
      .catch(() => {});
  }, []);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const reduced = useReducedMotion();
  const night = useNight();
  const songs = movers.length
    ? movers.slice(0, 4)
    : arrival?.lead
      ? [arrival.lead]
      : [];
  const lead = songs[index] ?? songs[0];
  const mover = movers[index];
  const facts = mover
    ? musicFacts(mover)
    : lead
      ? [{ text: "New on lists and charts.", component: "arrival" }]
      : [];
  if (!lead)
    return (
      <section className="cover-empty">
        <h1>
          {unavailable ? "the music is out of reach." : "a quiet window."}
        </h1>
        {unavailable && <a href="/">Retry</a>}
        <button onClick={() => night.open()}>See last night →</button>
      </section>
    );
  const proof = (component: string) =>
    mover
      ? traceLink({
          entry: `home.${component}`,
          song: mover.song_key,
          ranking: mover.ranking_build,
        })
      : cycleProofLink(provenance?.build, "/sources", {
          song: lead.song_key,
          sources: observedKeys(lead.evidence),
        });
  return (
    <section className="cover-strip" data-card>
      <div
        className="cover-wash"
        style={{
          background: visibleArt[lead.song_key]
            ? (palettes[lead.song_key] ?? "transparent")
            : "transparent",
        }}
        aria-hidden="true"
      />
      <div
        className="cover-row"
        onTouchStart={(e) => {
          e.currentTarget.dataset.start = String(e.touches[0].clientX);
        }}
        onTouchEnd={(e) => {
          const delta =
            Number(e.currentTarget.dataset.start) - e.changedTouches[0].clientX;
          if (Math.abs(delta) > 45) {
            setIndex(
              (i) => (i + (delta > 0 ? 1 : -1) + songs.length) % songs.length,
            );
            setFlipped(false);
          }
        }}
      >
        {[lead, ...songs.filter((s) => s.song_key !== lead.song_key)].map(
          (song, i) => (
            <motion.button
              layout={!reduced}
              transition={{ duration: reduced ? 0 : 0.2 }}
              key={song.song_key}
              className={`strip-cover ${i === 0 ? "lead-cover" : "small-cover"}`}
              aria-label={
                i === 0 ? `Open ${song.title_text}` : `Show ${song.title_text}`
              }
              onClick={() => {
                if (i === 0)
                  router.push(`/s/song/${encodeURIComponent(song.song_key)}`);
                else {
                  setIndex(
                    songs.findIndex((s) => s.song_key === song.song_key),
                  );
                  setFlipped(false);
                }
              }}
            >
              <Art
                sharedLayout={false}
                onReady={artReady}
                song={song.song_key}
                title={song.title_text ?? "Song"}
              />
            </motion.button>
          ),
        )}
      </div>
      <div className="cover-caption">
        <h2>
          <Link
            prefetch={false}
            href={`/s/song/${encodeURIComponent(lead.song_key)}`}
          >
            {lead.title_text ?? "Song"}
          </Link>
        </h2>
        <p className="cover-artist">
          <Link
            prefetch={false}
            href={`/search?q=${encodeURIComponent(lead.artist_text ?? "")}`}
          >
            {lead.artist_text}
          </Link>
        </p>
        <div className="cover-sources">
          <SourceBadges keys={observedKeys(lead.evidence)} />
        </div>
        {flipped ? (
          <div className="cover-facts">
            {facts.map((fact) => (
              <p key={fact.component}>
                {fact.text}{" "}
                <Link prefetch={false} href={proof(fact.component)}>
                  Trace this fact →
                </Link>
              </p>
            ))}
            <button onClick={() => setFlipped(false)}>Back to cover</button>
          </div>
        ) : (
          <>
            {facts[0] && (
              <div className="cover-fact">
                <Metric
                  value={facts[0].text}
                  label=""
                  inline
                  onOpen={() =>
                    router.push(proof(facts[0].component), { scroll: false })
                  }
                  provenance={
                    provenance
                      ? {
                          ...provenance,
                          plain: facts[0].text,
                          next: {
                            label: "Trace this fact",
                            href: proof(facts[0].component),
                          },
                        }
                      : undefined
                  }
                />
              </div>
            )}
            <div className="cover-actions">
              <button data-primary onClick={() => setFlipped(true)}>
                See why
              </button>
              <Link
                prefetch={false}
                href={
                  mover
                    ? traceLink({
                        entry: "home.mover_card",
                        song: mover.song_key,
                        ranking: mover.ranking_build,
                      })
                    : proof("arrival")
                }
              >
                Where it came from →
              </Link>
            </div>
          </>
        )}
      </div>
      <Link prefetch={false} className="all-rising" href="/songs?view=rising">
        All rising songs →
      </Link>
    </section>
  );
}
