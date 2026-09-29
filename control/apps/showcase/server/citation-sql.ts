import { z } from "zod";

const scalar = z.union([
  z.string(),
  z.number().finite(),
  z.boolean(),
  z.null(),
]);
const readInput = z.object({
  limit: z.number().int().min(1).max(100),
  filters: z.record(z.string(), scalar).optional(),
  range: z
    .record(
      z.string(),
      z.object({ from: scalar.optional(), to: scalar.optional() }),
    )
    .optional(),
});
const identifier = (value: string) => `"${value.replaceAll('"', '""')}"`;
function literal(value: z.infer<typeof scalar>): string {
  if (value === null) return "NULL";
  if (typeof value === "string")
    return `E'${value.replaceAll("\\", "\\\\").replaceAll("'", "''")}'`;
  return String(value);
}
// Render the first API page from its generated columns, grain and actual request.
export function martCitationSql(
  metadata: {
    schema: string;
    name: string;
    columns: string[];
    grain: string[];
    grain_types: string[];
  },
  input: unknown,
) {
  const request = readInput.parse(input);
  const predicates = Object.entries(request.filters ?? {}).map(
    ([column, value]) => {
      if (
        column === "song_key" &&
        value !== null &&
        [
          "mart_top_movers",
          "mart_top_movers_current",
          "mart_early_signals_current",
          "mart_arrivals_current",
        ].includes(metadata.name)
      ) {
        return `"member_song_keys"::jsonb ? ${literal(value)}::text`;
      }
      return `${identifier(column)} ${value === null ? "IS NULL" : `= ${literal(value)}`}`;
    },
  );
  for (const [column, range] of Object.entries(request.range ?? {})) {
    if (range.from !== undefined)
      predicates.push(`${identifier(column)} >= ${literal(range.from)}`);
    if (range.to !== undefined)
      predicates.push(`${identifier(column)} < ${literal(range.to)}`);
  }
  return `SELECT ${metadata.columns.map(identifier).join(", ")} FROM ${identifier(metadata.schema)}.${identifier(metadata.name)}${predicates.length ? ` WHERE ${predicates.join(" AND ")}` : ""} ORDER BY ${metadata.grain.map((column, index) => `${identifier(column)}${/^(text|varchar|character varying)/i.test(metadata.grain_types[index] ?? "") ? ' COLLATE "C"' : ""}`).join(", ")} LIMIT ${request.limit};`;
}
export function directCitationSql(sql: string, parameters: string[]) {
  return sql.replace(/\$(\d+)\b/g, (placeholder, position: string) => {
    const value = parameters[Number(position) - 1];
    return value === undefined ? placeholder : literal(value);
  });
}
export function holdingsCommand(since: string) {
  return `pnpm --dir control mdp platform holdings --since ${z.iso.datetime().parse(since)}`;
}
// A matched song's days: its copies' adds, followers and plays add up; its cities are the most any
// one copy reached, so one city is never counted twice. Rights stay conservative: every copy's
// sources, and a flag only when every copy carries it.
export function groupSongDaySql(keys: string[], range: { from: string; to: string }) {
  const songs = `song_key IN (${keys.map(literal).join(", ")})`;
  return `SELECT d.day, sum(d.editorial_adds) AS editorial_adds, sum(d.algorithmic_adds) AS algorithmic_adds, sum(d.playlist_followers) AS playlist_followers, max(d.shazam_cities) AS shazam_cities, sum(d.stream_rate) AS stream_rate, bool_and(d.learning_eligible) AS learning_eligible, bool_and(d.resale_permitted) AS resale_permitted, (SELECT json_agg(DISTINCT k.value ORDER BY k.value) FROM marts.mart_song_day s CROSS JOIN LATERAL jsonb_array_elements_text(s.source_keys::jsonb) k WHERE s.${songs} AND s.day = d.day) AS source_keys FROM marts.mart_song_day d WHERE d.${songs} AND d.day >= ${literal(range.from)} AND d.day < ${literal(range.to)} GROUP BY d.day ORDER BY d.day;`;
}
