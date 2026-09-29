"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  chartLabel,
  libraryItem,
  libraryItemPath,
  type LibraryItem,
} from "../lib/library";
import type { SearchRow } from "../lib/search";
import { ArtistCard } from "./artist";
import { CityMap } from "./city-map";
import { Sheet } from "./sheet";
import { SourceLine } from "./sources";

const titles = {
  artist: "Artist",
  playlist: "Playlist",
  chart: "Chart",
  source: "Source",
  song: "Song",
  account: "Account",
} as const;
// A Library result's viewer view: the facts the platform holds for it, in plain words.
// Songs inside open their pages; only a source offers the operator console, as a quiet link.
export function LibraryItemSheet({
  row,
  close,
  leave,
}: {
  row: SearchRow;
  close: () => void;
  // Closes the whole Library before a song page opens.
  leave: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const path = libraryItemPath(row);
  const [attempt, setAttempt] = useState(0);
  const [item, setItem] = useState<LibraryItem | null>(null);
  const [state, setState] = useState<
    "loading" | "ready" | "missing" | "failed"
  >(path ? "loading" : "missing");
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    fetch(path, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (response.status === 401 || response.status === 403) {
          router.push("/sign-in?reason=ended");
          return;
        }
        if (response.status === 404) {
          setState("missing");
          return;
        }
        if (!response.ok) throw new Error("Library item unavailable.");
        const parsed = libraryItem.parse(await response.json());
        if (!controller.signal.aborted) {
          setItem(parsed);
          setState("ready");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setState("failed");
      });
    return () => controller.abort();
  }, [path, attempt, router]);
  const name =
    row.kind === "chart" ? chartLabel(row) : (item?.title ?? row.display_text);
  return (
    <Sheet title={titles[row.kind]} close={close} className="library-item">
      {row.kind === "artist" ? (
        <ArtistCard
          name={row.display_text}
          reading={state === "loading"}
          artist={
            row.context.platform && row.context.artist_id
              ? {
                  song_key: row.context.key,
                  platform: row.context.platform,
                  artist_id: row.context.artist_id,
                  wikidata_qid: row.context.wikidata_qid ?? null,
                  mb_artist_name: row.display_text,
                  first_seen: item?.first_seen ?? null,
                  // Search rows name every source upstream of the index, not where this artist shows up.
                  source_keys: [],
                }
              : undefined
          }
        />
      ) : (
        <h2>{name}</h2>
      )}
      {item?.subtitle ? <p className="library-sub">{item.subtitle}</p> : null}
      {item?.facts.length ? (
        <p className="library-facts">
          {item.facts.map((fact) => (
            <span key={fact.label}>
              <strong>{fact.value}</strong> {fact.label}
            </span>
          ))}
        </p>
      ) : null}
      {item?.places.length ? <CityMap places={item.places} /> : null}
      {state === "loading" && <p className="library-state">Reading.</p>}
      {state === "missing" && (
        <p className="library-state">
          No longer in the Library.{" "}
          <button onClick={close}>Search again</button>
        </p>
      )}
      {state === "failed" && (
        <p className="library-state">
          This is out of reach.{" "}
          <button
            onClick={() => {
              setState("loading");
              setAttempt((value) => value + 1);
            }}
          >
            Retry
          </button>
        </p>
      )}
      {item?.songs_label ? (
        <p className="eyebrow library-label">{item.songs_label}</p>
      ) : null}
      {item?.songs.length ? (
        <ol className="library-songs">
          {item.songs.map((song, index) => (
            <li key={`${song.key ?? song.title}:${index}`}>
              {song.key ? (
                <Link
                  prefetch={false}
                  href={`/s/song/${encodeURIComponent(song.key)}`}
                  onClick={leave}
                >
                  {song.title}
                </Link>
              ) : (
                <span>{song.title}</span>
              )}
              {song.note ? <small>{song.note}</small> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {item?.empty ? (
        <p className="library-state">
          {item.empty}
          {/* An artist with no songs here still has one way on: back to the results. */}
          {item.kind === "artist" ? (
            <>
              {" "}
              <button onClick={close}>Search again</button>
            </>
          ) : null}
        </p>
      ) : null}
      {item ? (
        <p className="how-meta">
          {item.source ? <SourceLine source={item.source} /> : item.read}
        </p>
      ) : null}
      {item?.link || item?.engine || item?.source ? (
        <div className="sheet-links">
          {item.source && (
            <Link
              prefetch={false}
              className="primary"
              href={`/s/proof/source/${encodeURIComponent(item.source.source_key)}?${new URLSearchParams({ from: pathname })}`}
              onClick={leave}
            >
              See source details →
            </Link>
          )}
          {item.link ? (
            <a
              className="primary"
              href={item.link.href}
              target="_blank"
              rel="noopener noreferrer"
            >
              {item.link.label} ↗
            </a>
          ) : null}
          {item.engine ? (
            <a className="out-link" href={item.engine}>
              Open in Console ↗
            </a>
          ) : null}
        </div>
      ) : null}
    </Sheet>
  );
}
