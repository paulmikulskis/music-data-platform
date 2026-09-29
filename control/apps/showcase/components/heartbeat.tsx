"use client";
/* eslint-disable @next/next/no-location-assign-relative-destination */
import { useEffect, useState } from "react";
import { z } from "zod";
import { platformEvent, runnerState } from "@mdp/contracts/platform";
import type { LiveSong } from "../server/models";
import { honesty } from "../lib/honesty";
import type { Source } from "./sources";
import { LocalTime } from "./local-time";
import { useNight } from "./home/last-night";
const connection = z.object({
  online: z.boolean(),
  runner: runnerState.optional(),
});
export function Pulse({
  at,
  cached,
  sources = [],
}: {
  songs?: LiveSong[];
  at?: string | null;
  cached?: string;
  sources?: Source[];
}) {
  const [online, setOnline] = useState(false);

  const night = useNight();
  const [now, setNow] = useState(0);
  const [pulse, setPulse] = useState({ id: "0", kind: "" });
  useEffect(() => {
    let source: EventSource | undefined;
    let after = "";
    const seen = new Set<string>();
    const connect = () => {
      source?.close();
      if (document.hidden) return;
      source = new EventSource(`/events?after=${after}`);
      source.addEventListener("connection", (event) => {
        try {
          const data = connection.parse(JSON.parse(event.data));
          setOnline(data.online);

          setNow(Date.now());
        } catch {
          setOnline(false);
        }
      });
      source.addEventListener("pulse", (event) => {
        try {
          const value = platformEvent.parse(JSON.parse(event.data));
          after = event.lastEventId;
          setOnline(true);
          if (seen.has(value.key)) return;
          seen.add(value.key);
          if (Date.now() - Date.parse(value.occurred_at) > 60000) return;
          if (
            value.kind === "run_admitted" ||
            value.kind.startsWith("alert_") ||
            (value.kind === "cycle_closed" && value.scheduled)
          )
            setPulse({ id: after, kind: value.kind });
          if (value.kind === "run_settled" || value.kind === "cycle_closed")
            window.dispatchEvent(new Event("showcase:activity"));
        } catch {
          setOnline(false);
        }
      });
      source.addEventListener("expired", () => {
        source?.close();
        window.location.assign("/sign-in?reason=ended");
      });
      source.onerror = () => {
        setOnline(false);
        void fetch("/events", { method: "HEAD", cache: "no-store" })
          .then((response) => {
            if (response.status === 401 || response.status === 403) {
              source?.close();
              window.location.assign("/sign-in?reason=ended");
            }
          })
          .catch(() => {});
      };
    };
    connect();
    document.addEventListener("visibilitychange", connect);
    return () => {
      source?.close();
      document.removeEventListener("visibilitychange", connect);
    };
  }, []);
  const stale = sources.some(
    (source) => honesty(source, now).state === "overdue",
  );
  return (
    <button
      className={`heartbeat ${stale ? "stale" : ""} ${online ? "online" : "offline"} ${pulse.kind}`}
      onClick={() => night.open()}
      aria-label="Open last night"
    >
      <span className="status-dot" aria-hidden="true" />
      <span className="heartbeat-label">
        {!online
          ? "Reconnecting"
          : stale
            ? "Needs attention"
            : cached === "live" &&
                sources.length &&
                sources.every(
                  (source) => honesty(source, now).state === "current",
                )
              ? "Up to date"
              : "Updated"}
        {at && (
          <>
            {" "}
            · <LocalTime at={at} relativeDay />
          </>
        )}
      </span>
    </button>
  );
}
