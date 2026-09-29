"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { animate, useReducedMotion } from "motion/react";
function Count({ value }: { value: string }) {
  const target = Number(value);
  const prior = useRef(target);
  const [display, setDisplay] = useState(target);
  const reduced = useReducedMotion();
  useEffect(() => {
    const from = prior.current;
    prior.current = target;
    if (reduced) return;
    const animation = animate(
      from === target ? Math.max(1, target * 0.9) : from,
      target,
      { duration: 0.22, onUpdate: setDisplay },
    );
    return () => animation.stop();
  }, [target, reduced]);
  return (
    <span aria-label={value}>
      {Math.round(reduced ? target : display).toLocaleString("en-US")}
    </span>
  );
}
export function Activity({
  rows,
  sources,
  songs,
  week,
}: {
  rows: string | null;
  sources: string | null;
  songs: string | null;
  week: string | null;
}) {
  const router = useRouter();
  useEffect(() => {
    const refresh = () => {
      if (!document.hidden) router.refresh();
    };
    const timer = setInterval(refresh, 30000);
    window.addEventListener("showcase:activity", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener("showcase:activity", refresh);
    };
  }, [router]);
  const positive = (value: string | null) =>
    value !== null && BigInt(value) > 0n;
  const lead = positive(rows)
    ? { value: rows!, label: "appearances collected today" }
    : positive(week)
      ? { value: week!, label: "appearances collected this week" }
      : positive(songs)
        ? { value: songs!, label: "songs tracked" }
        : null;
  return (
    <Link
      prefetch={false}
      href="/sources"
      className="activity"
      aria-label="Open live collection in Sources"
    >
      <span className="eyebrow">
        Live pulse <span aria-hidden="true">↗</span>
      </span>
      {lead ? (
        <>
          <strong>
            <Count value={lead.value} />
          </strong>
          <span>{lead.label}</span>
        </>
      ) : (
        <small>Collection · not measured yet</small>
      )}
      <div className="activity-small">
        <span>
          {positive(sources) ? (
            <b>{BigInt(sources!).toLocaleString("en-US")}</b>
          ) : (
            <small>not measured yet</small>
          )}{" "}
          sources live
        </span>
        <span>
          {lead?.label === "songs tracked" ? (
            <small>Today · not measured yet</small>
          ) : (
            <>
              {positive(songs) ? (
                <b>{BigInt(songs!).toLocaleString("en-US")}</b>
              ) : (
                <small>not measured yet</small>
              )}{" "}
              songs tracked
            </>
          )}
        </span>
      </div>
    </Link>
  );
}
