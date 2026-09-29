"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  useRef,
} from "react";
import {
  nightResponse,
  viewerNight,
  nightMoments,
  nightReaders,
  momentClusters,
  matchesReady,
  type Night,
  type Moment,
} from "../../lib/night";
import { LocalTime, localTime, useViewerZone } from "../local-time";
import { Sheet } from "../sheet";
import { useTending } from "../sources";
import type { Build } from "../number";
import type { LiveSong } from "../../server/models";
import { Art } from "../movers";
const Context = createContext<{
  open: (id?: string, cluster?: string[]) => void;
  data: Night | null;
  failed: boolean;
  retry: () => void;
  title: string;
  checkedAgain: boolean;
}>({
  open: () => {},
  data: null,
  failed: false,
  retry: () => {},
  title: "Last night",
  checkedAgain: false,
});
export const useNight = () => useContext(Context);
export function NightProvider({
  children,
  songs = [],
  build,
}: {
  children: React.ReactNode;
  songs?: LiveSong[];
  build?: Build;
}) {
  const zone = useViewerZone();
  const [data, setData] = useState<Night | null>(null);
  const [failed, setFailed] = useState(false);
  const [checkedAgain, setCheckedAgain] = useState(false);
  const [title, setTitle] = useState("Last night");
  const [opened, setOpened] = useState(false);
  const [selected, setSelected] = useState<string>();
  const [version, setVersion] = useState(0);
  const [clusterIds, setClusterIds] = useState<string[]>([]);
  const choose = useCallback((id?: string, cluster: string[] = []) => {
    setSelected(id);
    setClusterIds(cluster);
  }, []);
  const open = useCallback(
    (id?: string, cluster?: string[]) => {
      choose(id, cluster);
      setOpened(true);
    },
    [choose],
  );
  useEffect(() => {
    const timer = setTimeout(() => {
      if (
        new URLSearchParams(window.location.search).get("sheet") ===
        "last-night"
      )
        open(
          new URLSearchParams(window.location.search).get("moment") ??
            undefined,
        );
    }, 0);
    return () => clearTimeout(timer);
  }, [open]);
  useEffect(() => {
    if (!zone) return;
    const controller = new AbortController();
    const window = viewerNight(new Date(), zone);
    if (window.since === window.until) {
      const timer = setTimeout(() => setVersion((value) => value + 1), 1);
      return () => clearTimeout(timer);
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async (second = false) =>
      fetch(
        `/s/night?${new URLSearchParams({ since: window.since, until: window.until })}`,
        { signal: controller.signal, cache: "no-store" },
      )
        .then(async (response) => {
          if (response.status === 401 || response.status === 403) {
            globalThis.location.replace("/sign-in?reason=ended");
            return;
          }
          if (!response.ok) throw new Error("Retry shortly.");
          const next = nightResponse.parse(await response.json());
          setData(next);
          setTitle(window.title);
          const incomplete =
            next.state !== "live" || next.value.ready_state !== "live";
          setFailed(second && next.state !== "live");
          setCheckedAgain(second);
          if (incomplete && !second)
            timer = setTimeout(() => void read(true), 30000);
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setFailed(second);
            setCheckedAgain(second);
            if (!second) timer = setTimeout(() => void read(true), 30000);
          }
        });
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [zone, version]);
  const retry = () => setVersion((n) => n + 1);
  const moments = data ? nightMoments(data.value) : [];
  const cluster = clusterIds.filter((id) =>
    moments.some((item) => item.id === id),
  );
  const clusterIndex = cluster.indexOf(selected ?? "");
  const step = (direction: number) =>
    setSelected(
      cluster[(clusterIndex + direction + cluster.length) % cluster.length],
    );
  const moment = moments.find((m) => m.id === selected) ?? moments.at(-1);
  return (
    <Context.Provider
      value={{ open, data, failed, retry, title, checkedAgain }}
    >
      {children}
      {opened && (
        <Sheet title={title} urlKey="last-night" close={() => setOpened(false)}>
          {data ? (
            <>
              <p className="night-window">
                <LocalTime at={data.value.window.since} /> to{" "}
                <LocalTime at={data.value.window.until} />
              </p>
              {failed && (
                <p>
                  Cached · as of <LocalTime at={data.savedAt} />{" "}
                  <button onClick={retry}>Retry</button>
                </p>
              )}
              <Timeline
                moments={moments}
                data={data}
                select={choose}
                selected={moment?.id}
                lanes
              />
              {cluster.length > 1 && (
                <div className="night-cluster-controls" aria-live="polite">
                  <span>
                    {clusterIndex + 1} of {cluster.length} moments
                  </span>
                  <button onClick={() => step(-1)}>Previous</button>
                  <button onClick={() => step(1)}>Next</button>
                </div>
              )}
              {moment ? (
                <MomentDetails moment={moment} />
              ) : (
                <NightEmpty data={data} retry={retry} />
              )}
              {checkedAgain &&
                data.value.ready_saved_at &&
                data.value.ready_state !== "live" && (
                  <p>
                    Updates as of <LocalTime at={data.value.ready_saved_at} />.{" "}
                    <button onClick={retry}>Retry</button>
                  </p>
                )}
              {checkedAgain && data.value.ready_state === "unavailable" && (
                <p>
                  Update times unavailable.{" "}
                  <button onClick={retry}>Retry</button>
                </p>
              )}
              {moment?.ready && songs.length > 0 && (
                <div className="night-songs">
                  <a href="/songs?view=rising">
                    {matchesReady(moment, build)
                      ? "Songs from this update"
                      : "Current songs"}{" "}
                    →
                  </a>
                  <div>
                    {songs.slice(0, 4).map((song) => (
                      <a
                        key={song.song_key}
                        href={`/s/song/${encodeURIComponent(song.song_key)}`}
                        aria-label={`Open ${song.title_text}`}
                      >
                        <Art
                          song={song.song_key}
                          title={song.title_text ?? "Song"}
                          sharedLayout={false}
                        />
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <NightUnavailable failed={failed} retry={retry} />
          )}
        </Sheet>
      )}
    </Context.Provider>
  );
}
function NightUnavailable({
  failed,
  retry,
  compact = false,
}: {
  compact?: boolean;
  failed: boolean;
  retry: () => void;
}) {
  return (
    <p>
      {failed
        ? compact
          ? "Logbook unavailable."
          : "Can't reach the platform logbook."
        : "Reading the logbook."}{" "}
      <button onClick={retry}>Retry</button> ·{" "}
      <a href="/sources">
        {compact ? "Source details →" : "View saved source details →"}
      </a>
    </p>
  );
}
function NightEmpty({ data, retry }: { data: Night; retry: () => void }) {
  return (
    <p>
      Nothing ran in this window.{" "}
      {data.value.next_due ? (
        <>
          Next read <LocalTime at={data.value.next_due} />.{" "}
        </>
      ) : (
        "Next read not checked. "
      )}
      <button onClick={retry}>Retry</button>
    </p>
  );
}
function MomentDetails({ moment }: { moment: Moment }) {
  const source = useTending()?.sources.find(
    (s) => s.source_key === moment.source,
  );
  return (
    <section className="night-detail" aria-live="polite">
      <h2>{moment.name}</h2>
      <p>
        <LocalTime at={moment.at} /> · {moment.status}
      </p>
      <p>
        {moment.coverage ??
          (moment.lane === "Ready"
            ? "Ready to read."
            : "Read count not measured.")}
        {moment.trigger && <> · {moment.trigger}</>}
      </p>
      {source?.tracked && (
        <p>
          {source.tracked.count} tracked at{" "}
          <LocalTime at={source.tracked.as_of} />
        </p>
      )}
      {moment.alert && <p>{moment.alert.summary}</p>}
      <a className="primary" href={moment.href}>
        See details →
      </a>
    </section>
  );
}
function Timeline({
  moments,
  data,
  select,
  selected,
  lanes = false,
}: {
  moments: Moment[];
  data: Night;
  select: (id: string, cluster: string[]) => void;
  selected?: string;
  lanes?: boolean;
}) {
  const zone = useViewerZone();

  const rows = lanes ? ["Reads", "Matching", "Ready"] : ["All"];
  return (
    <div className="night-timeline">
      {rows.map((lane) => (
        <div className="night-lane" key={lane}>
          {lanes && <span>{lane}</span>}
          <NightTrack
            moments={moments.filter((m) => lane === "All" || m.lane === lane)}
            window={data.value.window}
            select={select}
            selected={selected}
            zone={zone}
          />
        </div>
      ))}
    </div>
  );
}
function NightTrack({
  moments,
  window,
  select,
  selected,
  zone,
}: {
  moments: Moment[];
  window: { since: string; until: string };
  select: (id: string, cluster: string[]) => void;
  selected?: string;
  zone: string | undefined;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!track.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    observer.observe(track.current);
    return () => observer.disconnect();
  }, []);
  const groups = momentClusters(moments, window, width);
  const active = groups.find((group) =>
    group.moments.some((moment) => moment.id === selected),
  );
  return (
    <div className="night-track-wrap">
      <div className="night-track" ref={track}>
        {width > 0 &&
          groups.map((group) => {
            const moment = group.moments[0];
            const count = group.moments.length;
            const incomplete = group.moments.some((item) =>
              /failed|part|Replaced|Queued/.test(item.status),
            );
            const ready = group.moments.some((item) => item.lane === "Ready");
            return (
              <button
                key={moment.id}
                className={`night-tick ${ready ? "ready" : ""} ${incomplete ? "incomplete" : ""} ${count > 1 ? "clustered" : ""}`}
                style={{ left: group.x }}
                data-count={count}
                aria-label={
                  count > 1
                    ? `${count} moments${incomplete ? ", includes incomplete readings" : ""}. Open details`
                    : `${moment.name}: ${moment.status}`
                }
                aria-pressed={active === group}
                onClick={() =>
                  select(
                    moment.id,
                    group.moments.map((item) => item.id),
                  )
                }
                title={`${count > 1 ? `${count} moments` : moment.name} · ${zone ? localTime(moment.at, false, new Date(), zone) : "Time loading"} · Open details`}
              >
                <span />
              </button>
            );
          })}
      </div>
    </div>
  );
}
export function LastNight() {
  const { open, data, failed, checkedAgain, title } = useNight();
  const moments = data ? nightMoments(data.value) : [];
  return (
    <section className="home-night">
      <h2>{title}</h2>
      {data ? (
        <>
          <Timeline moments={moments} data={data} select={open} />
          <details className="night-bounds">
            <summary>Hours</summary>
            <p>
              <LocalTime at={data.value.window.since} /> —{" "}
              <LocalTime at={data.value.window.until} />
            </p>
            {failed && (
              <p>
                Cached · as of <LocalTime at={data.savedAt} />
              </p>
            )}
          </details>
          {!moments.length ? (
            <p>Open night details to check readings.</p>
          ) : (
            <p className="night-summary">
              {nightReaders(data.value)
                .slice(-2)
                .map((moment) => (
                  <span key={moment.id}>
                    {moment.name} · {moment.coverage ?? moment.status}.
                    {moment.status === "Reading failed"
                      ? " Reading failed."
                      : ""}{" "}
                  </span>
                ))}
              {!moments.some((moment) => moment.lane === "Reads") &&
                "Open a moment to see what ran."}
            </p>
          )}
          {checkedAgain &&
            data.value.ready_saved_at &&
            data.value.ready_state !== "live" && (
              <p className="night-ready-note">
                Updates as of <LocalTime at={data.value.ready_saved_at} />.
              </p>
            )}
          {checkedAgain && data.value.ready_state === "unavailable" && (
            <p className="night-ready-note">
              Update times unavailable. Open night details.
            </p>
          )}
          <button className="text-link" onClick={() => open()}>
            Night details →
          </button>
        </>
      ) : (
        <p>
          {failed ? "Logbook unavailable." : "Reading the logbook."}{" "}
          <button onClick={() => open()}>Night details →</button>
        </p>
      )}
    </section>
  );
}
