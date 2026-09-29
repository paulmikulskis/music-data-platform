"use client";
import type { LinkPreview } from "../lib/links";
import { LinkOut } from "./link-out";
import { createContext, useContext, useState } from "react";
import type { z } from "zod";
import type { platformSource } from "@mdp/contracts";
import type { SongArtist } from "../server/models";
import { sourceWording } from "@mdp/contracts/source-wording";
import { brandIcon, brandName, brandStyle, type Brand } from "../lib/brands";
import { honesty, remediation } from "../lib/honesty";
import { LocalTime, useViewerZone } from "./local-time";
import { clockLabel, dayLabel, isToday, lanePath } from "../lib/presentation";
import { Hover } from "./hover";
import { Sheet } from "./sheet";
import { marks } from "../lib/marks";
import { sourceProofLink } from "../lib/proof-link";
import { functionSentence, sourceFunctionState } from "../lib/function-copy";
export type Source = z.infer<typeof platformSource>;
// What the platform tends, read once per page: each source's counts and the songs it knows.
export type Tending = {
  sources: Source[];
  links?: LinkPreview[];
  songs: string | null;
  artists?: Record<string, SongArtist>;
  missingArt?: string[];
};
const Tended = createContext<Tending | null>(null);
export function TendingProvider({
  value,
  children,
}: {
  value: Tending | null;
  children: React.ReactNode;
}) {
  return <Tended.Provider value={value}>{children}</Tended.Provider>;
}
export function useTending() {
  return useContext(Tended);
}
export function targetsLabel(source: Source) {
  const tracked = source.tracked;
  if (!tracked) return null;
  const unit = tracked.count === 1 ? tracked.unit.slice(0, -1) : tracked.unit;
  return `${tracked.count.toLocaleString("en-US")} ${unit}`;
}
// True only when the source loaded entries on today's UTC date.
export function readToday(source: Source, now = Date.now()) {
  return (
    !!source.last_read &&
    isToday(source.last_read, now) &&
    BigInt(source.entries_today) > 0n
  );
}
// One line per source. A target count says what is tracked, never how many were read: a read of
// 1 of 100 lists still says "100 lists tracked". "read today" only when it read today; a weekly
// source says so; any other read carries its date.
export function sourceLine(source: Source, now = Date.now(), zone?: string) {
  const count = targetsLabel(source);
  const state = honesty(source, now);
  return `${source.display_name} · ${count ? `${count} tracked at ${clockLabel(source.tracked!.as_of, now, zone)} · ` : ""}${state.label}${source.last_read ? ` · read ${clockLabel(source.last_read, now, zone)}` : ""}`;
}
export function SourceLine({ source }: { source: Source }) {
  const zone = useViewerZone();
  const [now] = useState(Date.now);
  return <>{sourceLine(source, now, zone)}</>;
}
export function lastReadDay(source: Source, now = Date.now()) {
  if (!source.last_read) return "not read yet";
  return isToday(source.last_read, now)
    ? "last read today"
    : `last read ${dayLabel(source.last_read)}`;
}
export function Glyph({ brand, size = 14 }: { brand: Brand; size?: number }) {
  const icon = brandIcon(brand);
  if (!icon?.path)
    return (
      <span className="glyph-letters" aria-hidden="true">
        {brandName(brand).slice(0, 2)}
      </span>
    );
  return (
    <svg
      className="glyph"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
    >
      <path d={icon.path} />
    </svg>
  );
}
// The source's brand, else the brand the wording map gives its key.
function brandOf(key: string, source?: Source): Brand | null {
  return source?.brand ?? sourceWording[key]?.brand ?? null;
}
export function Sparkline({
  values,
  label,
}: {
  values: number[];
  label: string;
}) {
  return (
    <svg
      className="sparkline"
      viewBox="0 0 120 24"
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      <path d={lanePath(values, 120, 24)} />
    </svg>
  );
}
export function SourceCard({ source }: { source: Source }) {
  const state = honesty(source);
  const links = useTending()?.links ?? [];
  const preview = (id: string) =>
    links.find(
      (link) =>
        link.id === id &&
        (id !== "sources-reader-code" || link.variant === source.source_key),
    ) ?? null;
  return (
    <>
      <h2>{source.display_name.toLowerCase()}.</h2>
      <p data-function-description>
        {functionSentence(source.source_key, sourceFunctionState(source))}
      </p>
      <p>
        {state.label}
        {state.at && (
          <>
            {" "}
            · <LocalTime at={state.at} relativeDay />
          </>
        )}
      </p>
      <details className="source-read-times">
        <summary>Read times</summary>
        {source.tracked && (
          <p>
            {targetsLabel(source)} · Tracked at{" "}
            <LocalTime at={source.tracked.as_of} />
          </p>
        )}
        {source.last_read && (
          <p>
            Last read <LocalTime at={source.last_read} />
          </p>
        )}
        {state.checkedAt && (
          <p>
            Checked <LocalTime at={state.checkedAt} />
          </p>
        )}
      </details>
      {remediation(source) && <p>{remediation(source)}</p>}
      <a className="primary" href={sourceProofLink(source.source_key)}>
        See source details →
      </a>
      <a href="/stack#readers">See it on Stack →</a>
      <LinkOut preview={preview("sources-reader-code")} />
      <LinkOut preview={preview("sources-catalog")} />
    </>
  );
}
// A small brand glyph. Hover shows one line; a tap opens the source card.
export function SourceBadge({ sourceKey }: { sourceKey: string }) {
  const tending = useTending();
  const [open, setOpen] = useState(false);
  const source = tending?.sources.find((item) => item.source_key === sourceKey);
  const brand = brandOf(sourceKey, source);
  const name =
    source?.display_name ?? sourceWording[sourceKey]?.name ?? "This source";
  if (!brand) return null;
  return (
    <>
      <Hover
        label={name}
        card={
          <p className="hover-line">
            {source ? (
              <SourceLine source={source} />
            ) : (
              `${name} · not measured yet`
            )}
          </p>
        }
      >
        <span className="source-mark-actions">
          {source?.last_read && marks[brand] ? (
            <a
              className="source-badge"
              style={brandStyle(brand)}
              href={marks[brand].href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Open ${brandName(brand)}`}
            >
              <Glyph brand={brand} />
            </a>
          ) : (
            <span>{brandName(brand)}</span>
          )}
          <button
            aria-label={`${brandName(brand)}. Open source`}
            onClick={() => setOpen(true)}
          >
            ›
          </button>
        </span>
      </Hover>
      {open && (
        <Sheet title={brandName(brand)} close={() => setOpen(false)}>
          {source ? (
            <SourceCard source={source} />
          ) : (
            <>
              <h2>{name.toLowerCase()}.</h2>
              <p>Its counts are not measured yet.</p>
              <a className="primary" href="/sources?view=sources">
                See source details →
              </a>
            </>
          )}
        </Sheet>
      )}
    </>
  );
}
// One badge per brand, in the order the keys arrive. Inside a hover card the badges are still glyphs,
// so a second card never opens over the first.
export function SourceBadges({
  keys,
  max = 4,
  still = false,
}: {
  keys: string[];
  max?: number;
  still?: boolean;
}) {
  const tending = useTending();
  const seen = new Set<string>();
  const picked = keys
    .filter((key) => {
      const brand = brandOf(
        key,
        tending?.sources.find((item) => item.source_key === key),
      );
      if (!brand || seen.has(brand)) return false;
      seen.add(brand);
      return true;
    })
    .slice(0, max);
  if (!picked.length) return null;
  if (still)
    return (
      <span className="source-badges still">
        {picked.map((key) => {
          const brand = brandOf(
            key,
            tending?.sources.find((item) => item.source_key === key),
          );
          return brand ? (
            <a
              key={key}
              className="source-badge"
              style={brandStyle(brand)}
              title={brandName(brand)}
              href={marks[brand]?.href}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Glyph brand={brand} />
            </a>
          ) : null;
        })}
      </span>
    );
  return (
    <span className="source-badges">
      {picked.map((key) => (
        <SourceBadge key={key} sourceKey={key} />
      ))}
    </span>
  );
}
