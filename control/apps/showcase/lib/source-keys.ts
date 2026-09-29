// Row source keys arrive as a JSON array string or an array.
export function sourceKeys(value: unknown): string[] {
  const list = typeof value === "string" ? safeParse(value) : value;
  return Array.isArray(list)
    ? list.filter((key): key is string => typeof key === "string")
    : [];
}
function safeParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return [];
  }
}
// A served row's source_keys name every source upstream of its whole table, for its rights.
// A card's badges name only the sources that observed the song: the ones its evidence points at.
const playlistSources: Record<string, string> = {
  spotify: "sp_playlist",
  apple: "am_playlist",
  apple_music: "am_playlist",
  soundcloud: "sc_playlist",
};
function observedSource(entry: {
  relation: string;
  row_key: Record<string, unknown>;
}) {
  const platform =
    typeof entry.row_key.platform === "string" ? entry.row_key.platform : "";
  if (
    entry.relation === "mart_playlist_events" ||
    entry.relation === "mart_playlist_profile"
  )
    return playlistSources[platform] ?? null;
  if (entry.relation === "mart_shazam_chart_daily") return "sz_chart";
  if (entry.relation === "mart_track_daily_streams")
    return platform === "spotify" ? "sp_playlist" : null;
  if (entry.relation === "mart_chart_history") return "billboard_hot100";
  return null;
}
// The sources that observed a song, in evidence order. A row without evidence has none.
export function observedKeys(
  evidence:
    { relation: string; row_key: Record<string, unknown> }[] | null | undefined,
) {
  return [
    ...new Set(
      (evidence ?? []).map(observedSource).filter((key) => key !== null),
    ),
  ];
}
// Every source that observed any of these rows, in first-seen order.
export function unionKeys(
  rows: {
    evidence?: { relation: string; row_key: Record<string, unknown> }[] | null;
  }[],
) {
  return [...new Set(rows.flatMap((row) => observedKeys(row.evidence)))];
}
