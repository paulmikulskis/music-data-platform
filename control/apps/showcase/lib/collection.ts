import { awaitingAccess } from "./rights";

export type WeekDay = {
  day: string;
  // Entries collected that UTC day; null for a day that has not started or came before
  // collection began.
  entries: bigint | null;
  today: boolean;
  // True for a day before the first collection.
  before: boolean;
};
type IngestionRow = { day: string; source_key: string; rows_inserted: string };
// Collection totals without sample loads from sources waiting for provider access.
export function counted(rows: IngestionRow[], now = Date.now()) {
  const kept = rows.filter((row) => !awaitingAccess.has(row.source_key));
  const today = new Date(now).toISOString().slice(0, 10);
  const byDay = new Map<string, bigint>();
  for (const row of kept)
    byDay.set(row.day, (byDay.get(row.day) ?? 0n) + BigInt(row.rows_inserted));
  const total = [...byDay.values()].reduce((sum, value) => sum + value, 0n);
  return {
    rows: kept,
    week: total.toString(),
    today: (byDay.get(today) ?? 0n).toString(),
    days: [...byDay].map(([day, value]) => ({ day, rows_inserted: value.toString() })),
  };
}
// The first UTC day any source was collected, from the source records.
export function collectionBegan(sources: { first_collected: string | null }[]) {
  const days = sources
    .map((source) => source.first_collected?.slice(0, 10))
    .filter((day): day is string => !!day)
    .sort();
  return days[0] ?? null;
}
const dayMs = 86400000;
const utcDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);

// Seven UTC days from the week's Monday. A past day without entries counts zero, so a gap
// never hides; days after today, and days before collection began, stay empty, so the bars
// read as the week so far.
export function weekDays(
  since: string,
  days: { day: string; rows_inserted: string }[],
  { now = Date.now(), began = null }: { now?: number; began?: string | null } = {},
): WeekDay[] {
  const start = Date.parse(`${since.slice(0, 10)}T00:00:00Z`);
  const today = utcDay(now);
  return Array.from({ length: 7 }, (_, i) => {
    const day = utcDay(start + i * dayMs);
    const row = days.find((item) => item.day === day);
    const before = !!began && day < began && !row;
    return {
      day,
      entries: day > today || before ? null : BigInt(row?.rows_inserted ?? "0"),
      today: day === today,
      before,
    };
  });
}

// The day with the most entries and the source that brought most of them.
export function peakDay(
  week: WeekDay[],
  rows: { day: string; source_key: string; rows_inserted: string }[],
) {
  const peak = week.reduce<WeekDay | null>(
    (best, day) =>
      day.entries !== null && day.entries > 0n && (!best || day.entries > (best.entries ?? 0n))
        ? day
        : best,
    null,
  );
  if (!peak) return null;
  const lead = rows
    .filter((row) => row.day === peak.day)
    .sort((a, b) => {
      const difference = BigInt(b.rows_inserted) - BigInt(a.rows_inserted);
      return difference === 0n ? 0 : difference > 0n ? 1 : -1;
    })[0];
  return { day: peak.day, source_key: lead?.source_key ?? null };
}

// Bar heights in a 0..1 range against the week's tallest day.
export function barHeights(week: WeekDay[]) {
  const max = week.reduce(
    (top, day) => (day.entries !== null && day.entries > top ? day.entries : top),
    0n,
  );
  return week.map((day) =>
    day.entries === null || max === 0n
      ? null
      : Number((day.entries * 1000n) / max) / 1000,
  );
}
