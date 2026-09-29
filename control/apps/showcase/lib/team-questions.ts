import { z } from "zod";
// Each question is a fixed, reviewed query on a served table. No text from a person becomes SQL.
// A city chart's first day in the platform's readings counts as the day that city picked the song up.
export const shazamCitiesSql = `WITH firsts AS (
  SELECT s.apple_song_id, s.city, s.country, min(s.chart_date) AS first_day,
    min(s.title_text) AS title_text, min(s.artist_text) AS artist_text
  FROM marts.mart_shazam_chart_daily s
  WHERE s.city IS NOT NULL AND s.city <> '' AND s.apple_song_id IS NOT NULL
  GROUP BY 1, 2, 3)
SELECT f.title_text, f.artist_text, f.city, f.country, f.first_day::text AS first_day,
  (SELECT k.song_key FROM explore_intermediate.int_song_key__daily k
    WHERE k.platform = 'apple' AND k.platform_track_id = f.apple_song_id
    ORDER BY k.song_key LIMIT 1) AS song_key
FROM firsts f
WHERE f.first_day >= (now() AT TIME ZONE 'UTC')::date - 6
ORDER BY f.first_day DESC, f.title_text, f.city
LIMIT 5`;
export const teamQuestions = [
  {
    id: "shazam-cities",
    question: "Which Shazam cities picked up a song this week?",
    relation: "marts.mart_shazam_chart_daily",
    sql: shazamCitiesSql,
    columns: ["title_text", "artist_text", "city", "country", "first_day"],
  },
];
export const teamResult = z.object({
  id: z.string(),
  queried_at: z.iso.datetime(),
  rows: z
    .array(
      z
        .object({
          title: z.string(),
          artist: z.string(),
          city: z.string().nullable(),
          country: z.string().nullable(),
          first_day: z.string(),
          song_key: z.string().nullable(),
        })
        .strict(),
    )
    .max(5),
});
export type TeamResult = z.infer<typeof teamResult>;
