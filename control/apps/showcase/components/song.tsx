"use client";
import { marks } from "../lib/marks";
import { playsDescription } from "@mdp/contracts/source-wording";
import { musicFacts } from "../lib/music-facts";
import { SongPlaces } from "./song-places";
import type { SongPlace } from "../server/models";
import { CallIt } from "./call-it";
import { useEffect, useState, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { Identity, Mover, Readiness, SongDay } from "../server/models";
import type { Place, Provenance } from "./number";
import { Metric } from "./metric";
import { clusterLine, matchedLine } from "../lib/cluster";
import { Art } from "./movers";
import { Sheet } from "./sheet";
import { dayName, lanePath, shortLabel } from "../lib/presentation";
import { cycleProofLink, markProofLink } from "../lib/proof-link";
import { warmingDays, warmingLabel } from "../lib/music-facts";
import { platformBrand, songLink, brandName, brandStyle } from "../lib/brands";
import { Glyph, useTending } from "./sources";
import { ArtistName } from "./artist";
const lanes = [
  {
    key: "editorial_adds",
    name: "Daily adds",
    color: "var(--accent-3)",
    family: "playlists",
  },
  {
    key: "shazam_cities",
    name: "Shazam cities",
    color: "var(--accent-2)",
    family: "shazam",
  },
  {
    key: "stream_rate",
    name: "Plays",
    color: "var(--accent-1)",
    family: "streams",
  },
] as const;
// A family still collecting its first week shows how far along it is, not an empty lane.
function Warming({ days }: { days: number }) {
  return (
    <small className="warming-lane" title={warmingLabel(days)}>
      building history
      <span className="warm-meter" aria-hidden="true">
        {Array.from({ length: 7 }, (_, i) => (
          <i key={i} className={i < days ? "lit" : ""} />
        ))}
      </span>
    </small>
  );
}
// A lane's unit and its value in plain words, formatted: "1,446,921 plays/day".
export function laneUnit(lane: number, value: number) {
  if (lane === 2) return "plays/day";
  if (lane === 1) return value === 1 ? "city" : "cities";
  return value === 1 ? "playlist" : "playlists";
}
export function laneValue(lane: number, value: number) {
  return `${value.toLocaleString("en-US", { maximumFractionDigits: 0 })} ${laneUnit(lane, value)}`;
}
// Which sources feed each lane, by the family the source wording gives them.
const laneFamilies = {
  playlists: ["playlists"],
  shazam: ["charts"],
  streams: ["streams"],
} as const;
// Every platform copy once, in a stable order, with its public link when the id is public.
export function foundOn(copies: Identity[]) {
  const brands = [
    ...new Set(
      copies
        .map((copy) => platformBrand(copy.platform))
        .filter((brand) => brand !== null),
    ),
  ];
  const links = copies
    .map((copy) => songLink(copy.platform, copy.platform_track_id))
    .filter((link) => link !== null);
  return {
    brands,
    links: links.filter(
      (link, i) => links.findIndex((other) => other.label === link.label) === i,
    ),
  };
}
export function copiesLine(copies: Identity[], resolved: boolean) {
  if (copies.length < 2)
    return copies.length === 1 ? "one copy found" : "copies · not measured yet";
  const platforms = new Set(
    copies.map((copy) => platformBrand(copy.platform) ?? copy.platform),
  ).size;
  return resolved
    ? `Same song on ${platforms} ${platforms === 1 ? "platform" : "platforms"}`
    : "Not matched yet";
}
export function Song({
  song,
  days,
  copies,
  provenance,
  readiness,
  places = [],
  shownPlaces = null,
  callKey,
  placesBuild,
  match,
  playlistProvenance,
}: {
  song: Mover;
  days: SongDay[];
  copies: Identity[];
  provenance: Provenance;
  readiness?: Readiness[];
  places?: Place[];
  // Null when the places read failed: the sheet says so instead of saying there were none.
  shownPlaces?: SongPlace[] | null;
  placesBuild?: Provenance["build"];
  // The room's own song key. Movement may come from its group's representative song.
  callKey?: string;
  // The song's matched copies, counted as its card counts them.
  match?: { copies: number; methods: string[] };
  playlistProvenance?: Provenance;
}) {
  const tending = useTending();
  const drag = useRef({ start: 0, moved: false });
  const reduced = useReducedMotion();
  const [playing, setPlaying] = useState(false);
  const [day, setDay] = useState(Math.max(0, days.length - 1));
  const [touched, setTouched] = useState(false);
  const [mark, setMark] = useState<number | null>(null);
  const [identity, setIdentity] = useState(false);
  const [animate] = useState(
    () =>
      typeof window === "undefined" ||
      !sessionStorage.getItem(`identity:${song.song_key}`),
  );
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      setTouched(true);
      setDay((previous) => {
        if (previous >= days.length - 1) {
          setPlaying(false);
          return previous;
        }
        return previous + 1;
      });
    }, 300);
    return () => clearInterval(timer);
  }, [playing, days.length]);
  useEffect(() => {
    sessionStorage.setItem(`identity:${song.song_key}`, "1");
  }, [song.song_key]);
  // A matched song is one song on its card, so it is one song here too.
  const resolved =
    !!match || (copies.length > 0 && copies.every((c) => c.resolved));
  const current = days[day];
  const found = foundOn(copies);
  // The header keeps the card's count; the identity sheet names the matching rules.
  const copyLine = copiesLine(copies, resolved);
  // A lane names only sources on this song's own platforms; Shazam reads Apple copies.
  const laneSources = (family: keyof typeof laneFamilies) =>
    (tending?.sources ?? [])
      .filter(
        (source) =>
          (family === "streams"
            ? ["sp_playlist", "sp_playlist_weekly"].includes(source.source_key)
            : laneFamilies[family].some((item) => item === source.family)) &&
          (family === "shazam"
            ? source.brand === "shazam" && found.brands.includes("apple_music")
            : source.brand !== null && found.brands.includes(source.brand)),
      )
      .map((source) => source.source_key);
  const laneProvenance = (family: keyof typeof laneFamilies): Provenance => ({
    ...(family === "playlists"
      ? (playlistProvenance ?? provenance)
      : provenance),
    plain:
      family === "shazam"
        ? "Shazam charts this song was on that day."
        : family === "streams"
          ? playsDescription
          : "Distinct playlists with new appearances that day. Find chart lists in New places.",
    sources: laneSources(family),
    links: found.links,
  });
  const markWarm =
    mark === null ? null : warmingDays(readiness, lanes[mark].family);
  const markValue = mark === null ? null : current?.[lanes[mark].key];
  return (
    <section className="song-room">
      <div className="song-heading">
        <p className="eyebrow">Song</p>
        <h1 title={song.title_text ?? undefined}>
          {shortLabel(song.title_text ?? "untitled song", 8)}
        </h1>
        <p className="artist-line">
          <ArtistName song={song.song_key} name={song.artist_text} words={5} />
        </p>
      </div>
      <div className="song-call-actions">
        <CallIt
          key={current?.day}
          song={callKey ?? song.song_key}
          offerKey={
            current?.day
              ? `${callKey ?? song.song_key}:${current.day}`
              : undefined
          }
        />
        <SongPlaces
          places={shownPlaces}
          map={places}
          build={placesBuild}
          song={song.song_key}
        />
      </div>
      <button
        className={`identity-collapse ${resolved ? "resolved" : "unresolved"}`}
        aria-label="Open song identity"
        onClick={() => setIdentity(true)}
      >
        {copies.slice(0, 5).map((copy, i) => (
          <motion.span
            className="identity-copy"
            key={`${copy.platform}:${copy.platform_track_id}`}
            initial={
              animate && !reduced
                ? {
                    x: (i - (Math.min(5, copies.length) - 1) / 2) * 46,
                    rotate: i * 4 - 8,
                    opacity: 0.7,
                  }
                : false
            }
            animate={{
              x: resolved ? 0 : (i - (Math.min(5, copies.length) - 1) / 2) * 42,
              rotate: resolved ? 0 : i * 4 - 8,
              opacity: resolved ? 0.2 : 0.65,
            }}
            transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          >
            <span>{copy.platform}</span>
          </motion.span>
        ))}
        <motion.div
          initial={animate && !reduced ? { opacity: 0.2, scale: 0.92 } : false}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.9 }}
        >
          <Art song={song.song_key} title={song.title_text ?? "song"} />
        </motion.div>
      </button>
      {!resolved && (
        <div className="identity-status">
          <span>Match not confirmed yet</span>
        </div>
      )}
      {copies.length > 0 && (
        <p className="found-on">
          <span
            className="glyph-row"
            aria-label={`Found on ${found.brands.map(brandName).join(", ")}`}
          >
            {found.brands.map((brand) => (
              <a
                key={brand}
                className="glyph-link"
                style={brandStyle(brand)}
                href={marks[brand]?.href}
                aria-label={`Open ${brandName(brand)}`}
              >
                <Glyph brand={brand} />
              </a>
            ))}
          </span>
          {copyLine}
        </p>
      )}
      <div
        className="song-lanes"
        onPointerDown={(e) => {
          drag.current = { start: e.clientX, moved: false };
        }}
        onKeyDown={() => {
          drag.current.moved = false;
        }}
        onPointerCancel={() => {
          drag.current.moved = false;
        }}
        onPointerMove={(e) => {
          if (!e.buttons && e.pointerType !== "touch") return;
          if (Math.abs(e.clientX - drag.current.start) < 8) return;
          drag.current.moved = true;
          const rect = e.currentTarget.getBoundingClientRect();
          setDay(
            Math.min(
              days.length - 1,
              Math.max(
                0,
                Math.round(
                  ((e.clientX - rect.left) / rect.width) * (days.length - 1),
                ),
              ),
            ),
          );
          setTouched(true);
        }}
      >
        {lanes.map((lane, i) => {
          const values = days.map((d) =>
            d[lane.key] === null ? null : Number(d[lane.key]),
          );
          const warm = warmingDays(readiness, lane.family);
          return (
            <button
              key={lane.key}
              className="lane"
              title={
                current?.[lane.key] === null
                  ? warm === null
                    ? "not collected"
                    : warmingLabel(warm)
                  : `Open ${lane.name.toLowerCase()} proof`
              }
              aria-label={`Open ${lane.name} mark`}
              onClick={() => {
                if (drag.current.moved) {
                  drag.current.moved = false;
                  return;
                }
                setMark(i);
              }}
            >
              <span className="lane-name">{lane.name}</span>
              <strong className="lane-value">
                {current?.[lane.key] === null ||
                current?.[lane.key] === undefined ? (
                  warm === null ? (
                    <small>not collected</small>
                  ) : (
                    <Warming days={warm} />
                  )
                ) : i === 0 && current[lane.key] === 0 ? (
                  "No new adds"
                ) : (
                  laneValue(i, Number(current[lane.key]))
                )}
              </strong>
              <svg
                viewBox="0 0 300 64"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                {values.map((value, index) =>
                  value === null ? (
                    <rect
                      key={index}
                      x={((index - 0.5) / Math.max(1, values.length - 1)) * 300}
                      y="0"
                      width={300 / Math.max(1, values.length - 1)}
                      height="64"
                      fill="transparent"
                    >
                      <title>not collected</title>
                    </rect>
                  ) : null,
                )}
                <motion.path
                  d={lanePath(values)}
                  stroke={lane.color}
                  initial={reduced ? false : { pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: 0.25, delay: i * 0.1 }}
                />
                {touched && (
                  <line
                    x1={(day / Math.max(1, days.length - 1)) * 300}
                    x2={(day / Math.max(1, days.length - 1)) * 300}
                    y1="0"
                    y2="64"
                  />
                )}
              </svg>
            </button>
          );
        })}
        <div className="scrub-control">
          <input
            className="scrubber"
            type="range"
            min="0"
            max={Math.max(0, days.length - 1)}
            value={day}
            aria-label="Scrub day"
            onChange={(e) => {
              setDay(Number(e.target.value));
              setTouched(true);
            }}
          />
          <div className="day-bubble" role="status">
            {touched && current ? (
              <time>
                {new Date(current.day).toLocaleDateString("en-GB", {
                  month: "short",
                  day: "numeric",
                  timeZone: "UTC",
                })}
              </time>
            ) : null}
          </div>
        </div>
      </div>

      <p className="touch-hint">
        Slide through the song. Tap a mark.{" "}
        <button
          onClick={() => {
            if (!playing) {
              setDay(0);
              setTouched(true);
            }
            setPlaying(!playing);
          }}
        >
          {playing ? "Pause" : "Play"}
        </button>
      </p>
      {identity && (
        <Sheet
          title={song.title_text ?? "Song"}
          close={() => setIdentity(false)}
        >
          <h2>
            {match
              ? "copies matched."
              : resolved
                ? "every copy, one recording."
                : "copies stay separate."}
          </h2>
          <p>
            {match
              ? "They count as one song here and on its card."
              : resolved
                ? "These copies share a confirmed recording identity."
                : "A shared recording identity is not confirmed."}
          </p>
          <p className="found-on">
            {match ? matchedLine(match.copies, match.methods) : copyLine}
          </p>
          {!match && clusterLine(song.evidence) && (
            <p>For movement: {clusterLine(song.evidence)}</p>
          )}
          <div className="chips">
            {found.brands.map((brand) => (
              <span key={brand}>
                <a
                  className="glyph-link"
                  style={brandStyle(brand)}
                  href={marks[brand]?.href}
                >
                  <Glyph brand={brand} /> {brandName(brand)}
                </a>
              </span>
            ))}
          </div>
          <div className="sheet-links">
            <a
              className="primary"
              href={cycleProofLink(provenance.build, "/songs?view=rising", {
                song: song.song_key,
              })}
            >
              See the appearances →
            </a>
            {found.links.map((link) => (
              <a
                key={link.href}
                className="out-link"
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {link.label} ↗
              </a>
            ))}
          </div>
        </Sheet>
      )}
      {mark !== null && (
        <Sheet title={lanes[mark].name} close={() => setMark(null)}>
          <h2>{mark === 0 ? "new playlist appearances." : "daily reading."}</h2>
          {mark === 0 &&
            musicFacts(song).find(
              (fact) => fact.component === "playlist_adds",
            ) && (
              <p>
                {
                  musicFacts(song).find(
                    (fact) => fact.component === "playlist_adds",
                  )?.text
                }
              </p>
            )}
          {mark === 0 && (
            <p>
              This day only. Cards count distinct playlists across several days.
              A song can stay on a playlist without a new add.
            </p>
          )}
          {markValue === null || markValue === undefined ? (
            <p>
              {markWarm === null ? "not collected" : warmingLabel(markWarm)}
            </p>
          ) : (
            <Metric
              value={Number(markValue).toLocaleString("en-US", {
                maximumFractionDigits: 0,
              })}
              label={laneUnit(mark, Number(markValue))}
              provenance={laneProvenance(lanes[mark].family)}
            />
          )}
          <p>
            {current
              ? `Observed on ${dayName(current.day)}, UTC.`
              : "No observation is available."}
          </p>
          {!provenance.build?.stamped && (
            <p>Proof unavailable. The source time is missing.</p>
          )}
          <a
            className="primary"
            href={markProofLink(song.song_key, mark, {
              history: provenance.build?.built_at ?? "",
              day: current?.day ?? "",
            })}
          >
            See proof →
          </a>
        </Sheet>
      )}
    </section>
  );
}
