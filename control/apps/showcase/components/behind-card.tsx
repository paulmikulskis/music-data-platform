"use client";
import { brandStyle } from "../lib/brands";
import { marks } from "../lib/marks";
import type { Locator } from "../server/models";
import { clusterLine } from "../lib/cluster";
import { useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Glyph,
  lastReadDay,
  readToday,
  targetsLabel,
  useTending,
  type Source,
} from "./sources";
const whole = (value: bigint | number) => value.toLocaleString("en-US");
// Days since the earliest of these sources first collected anything.
export function historyDays(sources: Source[], now = Date.now()) {
  const first = sources
    .map((source) => source.first_collected)
    .filter((time): time is string => !!time)
    .map(Date.parse)
    .sort((a, b) => a - b)[0];
  return first === undefined
    ? null
    : Math.max(1, Math.ceil((now - first) / 86400000));
}
// The steps from the sources to this card, each with a real count or "not measured yet".
// A count says what is tracked, never how many were read; any read that is not today gives its date and no count.
export function behindSteps(
  sources: Source[],
  songs: string | null,
  now = Date.now(),
) {
  const read = sources.map((source) => ({
    key: source.source_key,
    brand: source.brand,
    text:
      readToday(source, now) && targetsLabel(source)
        ? `${source.display_name} · ${targetsLabel(source)} tracked · read today`
        : `${source.display_name} · ${lastReadDay(source, now)}`,
  }));
  const days = historyDays(sources, now);
  return [
    ...read,
    {
      key: "songs",
      brand: null,
      text:
        songs && BigInt(songs) > 0n
          ? `${whole(BigInt(songs))} songs tracked`
          : "songs tracked · not measured yet",
    },
    {
      key: "days",
      brand: null,
      text:
        days === null
          ? "days of history · not measured yet"
          : `${whole(days)} ${days === 1 ? "day" : "days"} of history`,
    },
    { key: "card", brand: null, text: "this card" },
  ];
}
// A quiet button on a card that flips it to the path from the sources to the card.
export function Behind({
  keys,
  evidence = [],
}: {
  keys: string[];
  evidence?: Locator[];
}) {
  const matched = clusterLine(evidence);
  const tending = useTending();
  const [open, setOpen] = useState(false);
  const reduced = useReducedMotion();
  const sources = (tending?.sources ?? [])
    .filter((source) => keys.includes(source.source_key))
    .slice(0, matched ? 1 : 2);
  const allSteps = behindSteps(sources, tending?.songs ?? null);
  const steps = matched
    ? [
        ...allSteps.slice(0, sources.length),
        { key: "card", brand: null, text: matched },
      ]
    : allSteps;
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  // Focus moves into the path when it opens and back to the button when it closes.
  useEffect(() => {
    if (open) panel.current?.focus();
  }, [open]);
  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  return (
    <>
      <button
        ref={button}
        className="behind-button"
        aria-label="Behind this card"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
          <circle cx="4" cy="4" r="2" />
          <circle cx="16" cy="4" r="2" />
          <circle cx="10" cy="16" r="2.4" />
          <path d="M5.3 5.5 9 14M14.7 5.5 11 14" />
        </svg>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            ref={panel}
            id={id}
            tabIndex={-1}
            className="behind"
            data-popover
            role="region"
            aria-label="Behind this card"
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                close();
              }
            }}
            initial={reduced ? false : { opacity: 0, rotateY: -8 }}
            animate={{ opacity: 1, rotateY: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <p className="eyebrow">behind this card</p>
            <ol>
              {steps.map((step, i) => (
                <motion.li
                  key={step.key}
                  className={step.key === "card" ? "here" : ""}
                  initial={reduced ? false : { opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{
                    duration: 0.18,
                    delay: reduced ? 0 : 0.1 + i * 0.12,
                  }}
                >
                  {step.brand ? (
                    <a
                      className="glyph-link"
                      style={brandStyle(step.brand)}
                      href={marks[step.brand]?.href}
                      aria-label={`Open ${marks[step.brand]?.label}`}
                    >
                      <Glyph brand={step.brand} />
                    </a>
                  ) : (
                    <span className="step-dot" aria-hidden="true" />
                  )}
                  <span>{step.text}</span>
                </motion.li>
              ))}
            </ol>
            <button
              className="behind-close"
              aria-label="Back to the card"
              onClick={close}
            >
              Back
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
