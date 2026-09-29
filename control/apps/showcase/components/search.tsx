"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { searchResponse, searchHref, type SearchRow } from "../lib/search";
import { Sheet } from "./sheet";
import { monogram } from "./artist";
import { LibraryItemSheet } from "./library-item";
import { chartLabel } from "../lib/library";
import { CallsProvider, CallIt } from "./call-it";

const creditResponse = z.object({
  credit: z.object({
    author: z.string().min(1),
    license: z.string().min(1),
    page: z
      .url()
      .refine(
        (value) =>
          new URL(value).hostname === "commons.wikimedia.org" &&
          value.startsWith("https:"),
      ),
  }),
});
function Face({ row }: { row: SearchRow }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [credit, setCredit] = useState<z.infer<typeof creditResponse> | null>(
    null,
  );
  const qid = row.context.wikidata_qid;
  useEffect(() => {
    if (!qid) return;
    const controller = new AbortController();
    fetch(`/artist-photo/${encodeURIComponent(qid)}/credit`, {
      signal: controller.signal,
    })
      .then((response) => response.json())
      .then((body: unknown) => {
        const parsed = creditResponse.safeParse(body);
        if (!controller.signal.aborted && parsed.success)
          setCredit(parsed.data);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [qid]);
  const src =
    row.kind === "song"
      ? `/art/${encodeURIComponent(row.context.art_song ?? row.context.key)}`
      : qid && credit
        ? `/artist-photo/${encodeURIComponent(qid)}`
        : null;
  // The monogram sits beneath; the picture shows only once it has loaded.
  return (
    <span
      className={`search-face ${row.kind === "artist" ? "round" : ""}`}
      data-art={loaded ? "loaded" : undefined}
    >
      <span aria-hidden="true">
        {row.kind === "artist" ? (
          monogram(row.display_text)
        ) : (
          <img src="/brand/mdp-monogram.svg" alt="" />
        )}
      </span>
      {src && !failed && (
        <img
          ref={(img) => {
            if (img?.complete) {
              if (img.naturalWidth > 0) setLoaded(true);
              else setFailed(true);
            }
          }}
          src={src}
          alt=""
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          title={
            credit
              ? `${credit.credit.author} · ${credit.credit.license}`
              : undefined
          }
        />
      )}
    </span>
  );
}
// Chart names read Tokyo · Shazam top 50; every other result keeps its own name.
const label = (row: SearchRow) =>
  row.kind === "chart" ? chartLabel(row) : row.display_text;
const short = (value: string, count: number) => {
  const words = value.trim().split(/\s+/);
  return words.length > count ? `${words.slice(0, count).join(" ")}…` : value;
};
export function SearchSheet({
  close,
  initialQuery = "",
}: {
  close: () => void;
  initialQuery?: string;
}) {
  const router = useRouter();
  const [query, setQuery] = useState(initialQuery);
  const [attempt, setAttempt] = useState(0);
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<z.infer<typeof searchResponse> | null>(
    null,
  );
  const [state, setState] = useState<"idle" | "loading" | "ready" | "failed">(
    "idle",
  );
  const [opened, setOpened] = useState<SearchRow | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const pageFocus = useRef<"first" | "last" | null>(null);
  useEffect(() => {
    const edge = pageFocus.current;
    pageFocus.current = null;
    if (!edge) return;
    const actions = Array.from(
      list.current?.querySelectorAll<HTMLElement>("[data-search-open]") ?? [],
    );
    actions.at(edge === "first" ? 0 : -1)?.focus();
  }, [offset]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      if (query.trim().length < 2) return;
      fetch(`/library/search?q=${encodeURIComponent(query.trim())}`, {
        signal: controller.signal,
        cache: "no-store",
      })
        .then(async (response) => {
          if (response.status === 401 || response.status === 403) {
            router.push("/sign-in?reason=ended");
            return null;
          }
          if (!response.ok) throw new Error("Search is unavailable. Retry.");
          return searchResponse.parse(await response.json());
        })
        .then((body) => {
          if (!controller.signal.aborted && body) {
            setResult(body);
            setState("ready");
          }
        })
        .catch(() => {
          if (!controller.signal.aborted) setState("failed");
        });
    }, 150);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, attempt, router]);
  function change(value: string) {
    setQuery(value);
    setOffset(0);
    setResult(null);
    setState(value.trim().length < 2 ? "idle" : "loading");
  }
  return (
    <Sheet
      title="Search"
      close={close}
      className="search-sheet"
      initialFocus={input}
    >
      <div
        onKeyDown={(event) => {
          const actions = Array.from(
            list.current?.querySelectorAll<HTMLElement>("[data-search-open]") ??
              [],
          );
          const current = actions.findIndex(
            (action) => action === document.activeElement,
          );
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (
              event.key === "ArrowDown" &&
              current === actions.length - 1 &&
              result &&
              offset + 4 < result.rows.length
            ) {
              pageFocus.current = "first";
              setOffset(offset + 4);
              return;
            }
            if (event.key === "ArrowUp" && current === 0 && offset > 0) {
              pageFocus.current = "last";
              setOffset(offset - 4);
              return;
            }
            const next =
              event.key === "ArrowDown"
                ? Math.min(current + 1, actions.length - 1)
                : current - 1;
            if (next < 0) input.current?.focus();
            else actions[next]?.focus();
          } else if (event.key === "Enter" && event.target === input.current) {
            event.preventDefault();
            actions[0]?.click();
          }
        }}
      >
        <label className="sr-only" htmlFor="library-query">
          Search
        </label>
        <input
          ref={input}
          id="library-query"
          type="search"
          value={query}
          maxLength={100}
          autoComplete="off"
          placeholder="Songs, artists, places…"
          onChange={(event) => change(event.target.value)}
        />
        <div role="status" className="search-status">
          {state === "idle" && "Type two letters to search."}
          {state === "loading" && "Searching. Keep typing to refine."}
          {state === "failed" && (
            <>
              Search is out of reach.{" "}
              <button
                onClick={() => {
                  setState("loading");
                  setAttempt((value) => value + 1);
                }}
              >
                Retry
              </button>
            </>
          )}
          {state === "ready" && !result?.rows.length && (
            <>
              No matches.{" "}
              <button
                onClick={() => {
                  change("");
                  input.current?.focus();
                }}
              >
                Try another name
              </button>
            </>
          )}
        </div>
        {result && (
          <CallsProvider offers={result.offers}>
            <div
              className="search-results"
              ref={list}
              aria-label="Search results"
            >
              {result.rows.slice(offset, offset + 4).map((row) => (
                <article
                  data-card
                  className="search-result"
                  key={row.object_key}
                >
                  <Face row={row} />
                  <div className="search-label">
                    <strong title={label(row)}>{short(label(row), 5)}</strong>
                    <small>{short(row.context.subtitle ?? row.kind, 3)}</small>
                  </div>
                  <div className="search-actions">
                    {row.kind === "song" ? (
                      <Link
                        prefetch={false}
                        data-primary
                        data-search-open
                        href={searchHref(row) ?? "/search"}
                        onClick={close}
                        aria-label={`Open ${row.display_text}`}
                      >
                        Open
                      </Link>
                    ) : (
                      <button
                        data-primary
                        data-search-open
                        onClick={() => setOpened(row)}
                        aria-label={`Open ${label(row)}`}
                      >
                        Open
                      </button>
                    )}
                    {row.kind === "song" && (
                      <CallIt
                        song={row.context.key}
                        primary={false}
                        onRefresh={() => {
                          setState("loading");
                          setAttempt((value) => value + 1);
                        }}
                      />
                    )}
                  </div>
                </article>
              ))}
            </div>
            {result.rows.length > 4 && (
              <nav className="search-paging" aria-label="More results">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset((value) => Math.max(0, value - 4))}
                >
                  Back
                </button>
                <button
                  disabled={offset + 4 >= result.rows.length}
                  onClick={() => setOffset((value) => value + 4)}
                >
                  More
                </button>
              </nav>
            )}
            {result.sources_unavailable && (
              <p>
                Sources are out of reach.{" "}
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
            {result.calls_unavailable && (
              <p>
                Picks are out of reach.{" "}
                <a href="/songs?view=picks">Open picks</a>
              </p>
            )}
          </CallsProvider>
        )}
      </div>
      {opened && (
        <LibraryItemSheet
          row={opened}
          close={() => setOpened(null)}
          leave={() => {
            setOpened(null);
            close();
          }}
        />
      )}
    </Sheet>
  );
}
export function SearchLauncher({
  initiallyOpen = false,
  initialQuery = "",
}: {
  initiallyOpen?: boolean;
  initialQuery?: string;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);
  return (
    <>
      <button
        className="search-launcher"
        aria-label="Search"
        aria-keyshortcuts="Meta+K Control+K"
        onClick={() => setOpen(true)}
      >
        <svg
          viewBox="0 0 24 24"
          width="22"
          height="22"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          aria-hidden="true"
        >
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m16 16 5 5" />
        </svg>
      </button>
      {open && (
        <SearchSheet close={() => setOpen(false)} initialQuery={initialQuery} />
      )}
    </>
  );
}
