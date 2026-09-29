"use client";
import Link from "next/link";
import { Trace } from "./lineage/trace";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import type { LiveSong } from "../server/models";
import { SearchLauncher } from "./search";
import { Apps } from "./apps";
import { Pulse } from "./heartbeat";
import { SignOutForm } from "./sign-out-form";
import { Sheet } from "./sheet";
import { updated } from "../lib/presentation";
import type { Build, Provenance } from "./number";
import { TendingProvider, type Tending } from "./sources";
import { NightProvider, useNight } from "./home/last-night";
import { LocalTime } from "./local-time";
const tabs = [
  ["Home", "/"],
  ["Songs", "/songs"],
  ["Sources", "/sources"],
  ["Stack", "/stack"],
  ["Analysts", "/team"],
];
const views = [
  ["Rising now", "rising"],
  ["New places", "places"],
  ["Waking up", "waking"],
  ["Your picks", "picks"],
  ["Weekly picks", "friday"],
];
export function activeTab(path: string) {
  if (path.startsWith("/songs") || path.startsWith("/s/song/")) return "/songs";
  if (path.startsWith("/sources")) return "/sources";
  if (path.startsWith("/stack")) return "/stack";
  if (path.startsWith("/team")) return "/team";
  return "/";
}
export function Shell({
  children,
  csrf,
  handle,
  provenances = [],
  cached,
  retry,
  songs = [],
  tending = null,
  build,
}: {
  children: React.ReactNode;
  csrf?: string;
  handle?: string;
  provenances?: (Provenance | undefined)[];
  cached?: string;
  retry?: string;
  songs?: LiveSong[];
  tending?: Tending | null;
  build?: Build;
}) {
  return (
    <Suspense
      fallback={
        <main>
          <a href="/">Open Home →</a>
        </main>
      }
    >
      <TendingProvider value={tending}>
        <NightProvider songs={songs} build={build}>
          <Frame
            csrf={csrf}
            handle={handle}
            provenances={provenances}
            cached={cached}
            retry={retry}
            tending={tending}
          >
            {children}
          </Frame>
        </NightProvider>
      </TendingProvider>
    </Suspense>
  );
}
function Frame({
  children,
  csrf,
  handle,
  provenances,
  cached,
  retry,
  tending,
}: {
  children: React.ReactNode;
  csrf?: string;
  handle?: string;
  provenances: (Provenance | undefined)[];
  cached?: string;
  retry?: string;
  tending: Tending | null;
}) {
  const pathname = usePathname() ?? "/";
  const query = useSearchParams();
  const [menu, setMenu] = useState(false);
  const [compact, setCompact] = useState(false);
  const night = useNight();
  const time = updated(provenances);
  const tab = activeTab(query.get("from") ?? pathname);
  const initials =
    handle
      ?.split(/[._-]/)
      .map((part) => part[0])
      .join("")
      .slice(0, 2)
      .toUpperCase() ?? "O";
  useEffect(() => {
    const scroll = () => setCompact(window.scrollY > 24);
    scroll();
    window.addEventListener("scroll", scroll, { passive: true });
    return () => window.removeEventListener("scroll", scroll);
  }, []);
  return (
    <main className="showcase redesigned" data-viewer-screen>
      <header className={`mast ${compact ? "compact-mast" : ""}`}>
        <Link prefetch={false} href="/" aria-label="Music Data Platform home">
          <img
            src="/brand/mdp-wordmark-custom.png"
            width="108"
            height="28"
            alt="Music Data Platform"
          />
        </Link>
        <nav className="top-tabs" aria-label="Main navigation">
          {tabs.map(([label, href]) => (
            <Link
              prefetch={false}
              key={href}
              href={href}
              aria-current={tab === href ? "page" : undefined}
            >
              {label}
            </Link>
          ))}
        </nav>
        <Pulse
          at={time}
          cached={cached}
          sources={(tending?.sources ?? []).filter((source) =>
            provenances.some((proof) =>
              proof?.sources?.includes(source.source_key),
            ),
          )}
        />
        <SearchLauncher
          initiallyOpen={pathname === "/search"}
          initialQuery={query.get("q") ?? ""}
        />
        <button
          className="avatar"
          aria-label="Open Music Data Platform stack"
          onClick={() => setMenu(true)}
        >
          {initials}
        </button>
      </header>
      <div className="room">
        {pathname === "/songs" && (
          <nav className="song-tabs" aria-label="Song views">
            {views.map(([label, view]) => (
              <Link
                prefetch={false}
                href={`/songs?view=${view}`}
                key={view}
                aria-current={
                  (query.get("view") ?? "rising") === view ? "true" : undefined
                }
              >
                {label}
              </Link>
            ))}
          </nav>
        )}
        {children}
      </div>
      <footer className="freshness">
        {time && (
          <button onClick={() => night.open()}>
            {cached === "cached" || cached === "busy"
              ? "Cached · as of"
              : "Updated"}{" "}
            <LocalTime at={time} />
          </button>
        )}
        <a href={retry ?? `${pathname}${query.size ? `?${query}` : ""}`}>
          Refresh ↻
        </a>
      </footer>
      <Trace />
      {menu && (
        <Sheet title="Music Data Platform stack" urlKey="stack" close={() => setMenu(false)}>
          <Apps compact />
          {csrf && <SignOutForm csrf={csrf} />}
        </Sheet>
      )}
    </main>
  );
}
