export function localTime(
  at: string,
  relativeDay = false,
  now = new Date(),
  timeZone?: string,
) {
  const date = new Date(at);
  const calendar = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  });
  const dayNumber = (value: Date) => {
    const parts = calendar.formatToParts(value);
    const part = (type: string) =>
      Number(parts.find((item) => item.type === type)?.value);
    return Date.UTC(part("year"), part("month") - 1, part("day")) / 86400000;
  };
  if (relativeDay) {
    const offset = dayNumber(date) - dayNumber(now);
    const label =
      offset === -1
        ? "Yesterday"
        : offset === 0
          ? "Today"
          : offset === 1
            ? "Tomorrow"
            : null;
    if (label)
      return `${label}, ${date.toLocaleTimeString(undefined, { timeZone, hour: "numeric", minute: "2-digit", timeZoneName: "short" })}`;
  }
  return date.toLocaleString(undefined, {
    timeZone,
    month: "short",
    day: "numeric",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}
