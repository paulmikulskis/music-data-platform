"use client";
import { useSyncExternalStore } from "react";
const subscribe = () => () => {};
import { localTime } from "../lib/local-time";
export { localTime } from "../lib/local-time";
export function useViewerZone() {
  return useSyncExternalStore(
    subscribe,
    () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    () => undefined,
  );
}
export function LocalTime({
  at,
  relativeDay = false,
  timeOnly = false,
}: {
  at: string;
  relativeDay?: boolean;
  timeOnly?: boolean;
}) {
  const local = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  return (
    <time dateTime={at} data-local-time>
      {local
        ? timeOnly
          ? new Date(at).toLocaleTimeString(undefined, {
              hour: "2-digit",
              minute: "2-digit",
              hourCycle: "h23",
              timeZoneName: "short",
            })
          : localTime(at, relativeDay)
        : "Time loading"}
    </time>
  );
}

export function LocalDateLine({
  weekdayOnlyOnPhone = false,
}: {
  weekdayOnlyOnPhone?: boolean;
}) {
  const zone = useViewerZone();
  return (
    <span className={weekdayOnlyOnPhone ? "date-responsive" : undefined}>
      {weekdayOnlyOnPhone && (
        <span className="date-weekday">
          {zone
            ? new Intl.DateTimeFormat(undefined, {
                weekday: "long",
                timeZone: zone,
              }).format(new Date())
            : "Date loading"}
        </span>
      )}
      <span className="date-full">
        {zone
          ? new Intl.DateTimeFormat(undefined, {
              weekday: "long",
              month: "long",
              day: "numeric",
              timeZone: zone,
            }).format(new Date())
          : "Date loading"}
      </span>
    </span>
  );
}
