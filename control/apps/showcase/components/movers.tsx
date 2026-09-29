"use client";
import { CallIt, useCallOffer } from "./call-it";
import { useCallback, useEffect, useState, useRef } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Mover } from "../server/models";
import { musicFacts } from "../lib/music-facts";
import { Sheet } from "./sheet";
import { CardMeta } from "./card-meta";
import { proofLink, shortLabel } from "../lib/presentation";
import { markProofLink } from "../lib/proof-link";
import { ArtistName } from "./artist";
import { Behind } from "./behind-card";
import { SourceBadges, useTending } from "./sources";
import { observedKeys } from "../lib/source-keys";
// A cover shows only once it has loaded. The monogram sits beneath it, so a missing or refused
// cover never shows the browser's broken image, even when it fails before the page is interactive.
export function Art({
  song,
  title,
  className = "",
  sharedLayout = true,
  onReady,
}: {
  song: string;
  title: string;
  className?: string;
  sharedLayout?: boolean;
  onReady?: (song: string) => void;
}) {
  const known = useTending()?.missingArt?.includes(song) ?? false;
  const [state, setState] = useState<"loading" | "loaded" | "failed">(
    known ? "failed" : "loading",
  );
  useEffect(() => {
    if (state === "loaded") onReady?.(song);
  }, [state, song, onReady]);
  const [attempt, setAttempt] = useState(0);
  const [errored, setErrored] = useState(false);
  // A server-rendered image can settle before React attaches its handlers, so read it on attach.
  const settle = useCallback((img: HTMLImageElement | null) => {
    if (!img?.complete) return;
    if (img.naturalWidth > 0) setState("loaded");
    else setErrored(true);
  }, []);
  // Two quiet retries, each after a random one to three seconds, cover a refused burst of
  // requests without asking again all at once; a third failure shows the monogram.
  useEffect(() => {
    if (!errored) return;
    const timer = setTimeout(
      () => {
        if (attempt >= 2) return setState("failed");
        setErrored(false);
        setAttempt(attempt + 1);
      },
      attempt >= 2 ? 0 : 1000 + Math.random() * 2000,
    );
    return () => clearTimeout(timer);
  }, [errored, attempt]);
  return (
    <motion.div
      layoutId={sharedLayout ? `art:${song}` : undefined}
      transition={{ duration: 0.22 }}
      className={`art ${className}`}
      data-art={state}
    >
      <div className="art-missing" aria-hidden={state !== "failed"}>
        <img draggable={false} src="/brand/mdp-monogram.svg" alt="" />
        {state === "failed" && <span>Artwork unavailable</span>}
      </div>
      {state !== "failed" && (
        <img
          key={attempt}
          ref={settle}
          loading="lazy"
          draggable={false}
          src={`/art/${encodeURIComponent(song)}${attempt ? `?retry=${attempt}` : ""}`}
          alt={`Cover for ${title}`}
          onLoad={() => setState("loaded")}
          onError={() => setErrored(true)}
        />
      )}
    </motion.div>
  );
}
export function MoverCard({
  mover,
  stack = false,
}: {
  mover: Mover;
  stack?: boolean;
}) {
  const [flipped, setFlipped] = useState(false);
  const [mark, setMark] = useState<number | null>(null);
  const reduced = useReducedMotion();
  const facts = musicFacts(mover);
  const offer = useCallOffer(mover.song_key);
  const keys = observedKeys(mover.evidence);
  return (
    <motion.article
      className={`mover ${flipped ? "flipped" : ""}`}
      data-card
      layout
      transition={{ duration: 0.22 }}
    >
      <AnimatePresence mode="wait" initial={false}>
        {!flipped ? (
          <motion.div
            key="front"
            className="mover-face"
            initial={{ opacity: 0, rotateY: reduced ? 0 : -12 }}
            animate={{ opacity: 1, rotateY: 0 }}
            exit={{ opacity: 0, rotateY: reduced ? 0 : 12 }}
            transition={{ duration: 0.2 }}
          >
            <Link
              prefetch={false}
              draggable={false}
              className="cover-link"
              href={`/s/song/${encodeURIComponent(mover.song_key)}?ranking=${encodeURIComponent(mover.ranking_build)}`}
              aria-label={`Open ${mover.title_text ?? "song"}`}
            >
              <Art song={mover.song_key} title={mover.title_text ?? "song"} />
            </Link>
            <Behind keys={keys} evidence={mover.evidence} />
            <div className="mover-caption">
              <div className="card-top">
                <CardMeta
                  list={mover.movement_list}
                  days={mover.window_days}
                  basis={mover.age_basis}
                />
                <SourceBadges keys={keys} max={3} />
              </div>
              <p className="artist-line">
                <ArtistName song={mover.song_key} name={mover.artist_text} />
              </p>
              <h2>
                <Link
                  prefetch={false}
                  href={`/s/song/${encodeURIComponent(mover.song_key)}?ranking=${encodeURIComponent(mover.ranking_build)}`}
                >
                  {shortLabel(mover.title_text ?? "Untitled song", 4)}
                </Link>
              </h2>
              <CallIt song={mover.song_key} />
              <button
                className="card-verb"
                data-primary={offer ? undefined : true}
                onClick={() => setFlipped(true)}
              >
                See why <span>↗</span>
              </button>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="back"
            className="mover-back"
            initial={{ opacity: 0, rotateY: reduced ? 0 : -12 }}
            animate={{ opacity: 1, rotateY: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <button
              className="close-flip"
              onClick={() => setFlipped(false)}
              aria-label="Turn cover back"
            >
              ↶
            </button>
            <span className="eyebrow">Movement</span>
            <h2>{mover.title_text}</h2>
            <div className="music-facts">
              {facts.map((fact) => (
                <button
                  key={fact.component}
                  onClick={() =>
                    setMark(
                      mover.evidence.findIndex(
                        (e) => e.component === fact.component,
                      ),
                    )
                  }
                >
                  {fact.text}
                </button>
              ))}
              {!facts.length && <p>{mover.reason_rule}</p>}
            </div>
            <Link
              prefetch={false}
              className="card-verb"
              data-primary
              href={`/s/song/${encodeURIComponent(mover.song_key)}?ranking=${encodeURIComponent(mover.ranking_build)}`}
            >
              Open song <span>↗</span>
            </Link>
          </motion.div>
        )}
      </AnimatePresence>
      {mark !== null && mark >= 0 && (
        <Sheet title="Why it moved" close={() => setMark(null)}>
          <h2>
            {facts.find(
              (fact) => fact.component === mover.evidence[mark].component,
            )?.text ?? "Song movement"}
          </h2>
          <p>
            {proofLink(mover.evidence[mark])
              ? "The lists and charts behind this fact."
              : "Proof unavailable. The source time is missing."}
          </p>
          <a
            className="primary"
            href={markProofLink(mover.song_key, mark, {
              ranking: mover.ranking_build,
            })}
          >
            See proof →
          </a>
        </Sheet>
      )}
      {stack && <span className="stack-edge" aria-hidden="true" />}
    </motion.article>
  );
}
export function MoverStack({ movers }: { movers: Mover[] }) {
  const [index, setIndex] = useState(0);
  const [dragX, setDragX] = useState(0);
  const reduced = useReducedMotion();
  const gesture = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const move = (delta: number) =>
    setIndex((i) => (i + delta + movers.length) % movers.length);
  if (!movers.length)
    return (
      <Empty
        title="a quiet window."
        detail="No qualifying movers for this window."
        href="/songs?view=rising"
        action="Open Rising now"
      />
    );
  return (
    <div
      className="stack"
      onPointerDown={(e) => {
        suppressClick.current = false;
        if (e.target instanceof Element && e.target.closest("button")) return;
        gesture.current = { x: e.clientX, y: e.clientY, moved: false };
      }}
      onPointerMove={(e) => {
        const start = gesture.current;
        if (!start) return;
        const dx = e.clientX - start.x;
        if (Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(e.clientY - start.y)) {
          start.moved = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          setDragX(Math.max(-60, Math.min(60, dx * 0.22)));
        }
      }}
      onPointerUp={(e) => {
        const start = gesture.current;
        gesture.current = null;
        setDragX(0);
        if (!start?.moved) return;
        suppressClick.current = true;
        const dx = e.clientX - start.x;
        if (Math.abs(dx) > 45 && movers.length > 1) move(dx < 0 ? 1 : -1);
      }}
      onPointerCancel={() => {
        gesture.current = null;
        setDragX(0);
      }}
      onClickCapture={(e) => {
        if (suppressClick.current) {
          suppressClick.current = false;
          e.preventDefault();
          e.stopPropagation();
        }
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") move(1);
        if (e.key === "ArrowLeft") move(-1);
      }}
    >
      <AnimatePresence mode="wait">
        <motion.div
          key={movers[index].song_key}
          initial={{ opacity: 0, x: reduced ? 0 : 28 }}
          animate={{ opacity: 1, x: dragX }}
          exit={{ opacity: 0, x: reduced ? 0 : -28 }}
          transition={{ type: "spring", stiffness: 300, damping: 30 }}
        >
          <MoverCard mover={movers[index]} stack />
        </motion.div>
      </AnimatePresence>
      <div className="stack-controls">
        <button onClick={() => move(-1)} aria-label="Previous song">
          ←
        </button>
        <div className="stack-dots">
          {movers.map((m, i) => (
            <button
              key={m.song_key}
              aria-label={`Show ${m.title_text}`}
              aria-pressed={i === index}
              onClick={() => setIndex(i)}
            />
          ))}
        </div>
        <button onClick={() => move(1)} aria-label="Next song">
          →
        </button>
      </div>
    </div>
  );
}
export function Empty({
  title,
  detail,
  href,
  action,
}: {
  title: string;
  detail: string;
  href: string;
  action: string;
}) {
  return (
    <article className="empty" data-card>
      <h2>{title}</h2>
      <p>{detail}</p>
      <Link prefetch={false} className="primary" data-primary href={href}>
        {action} ↗
      </Link>
    </article>
  );
}
