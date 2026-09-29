import "server-only";
import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
import { warehouse } from "./clients";
import { budget } from "./read-budget";
import { teamQuestions, teamResult, type TeamResult } from "../lib/team-questions";
// One reviewed question, read as showcase_wh from a served table. Up to five results, with the
// same reviewed public fields a peek may show: song, artist, city, country and the first day.
const cache = budget.cache<TeamResult>();
const row = z.object({
  title_text: z.string().nullable(),
  artist_text: z.string().nullable(),
  city: z.string().nullable(),
  country: z.string().nullable(),
  first_day: z.string(),
  song_key: z.string().nullable(),
});
export async function teamQuestion(id: string): Promise<TeamResult> {
  const question = teamQuestions.find((item) => item.id === id);
  if (!question) throw new Error("Unknown question. Open /team.");
  const result = await cache.read(
    `global:team-question:${id}`,
    "heavy",
    async () =>
      warehouse().begin(
        "isolation level repeatable read read only",
        async (tx) => {
          await tx.unsafe(`LOCK TABLE ${question.relation} IN ACCESS SHARE MODE`);
          const raw = await tx.unsafe(question.sql);
          // Project before caching: only the reviewed fields reach the browser.
          const rows = z.array(row).parse(encodeWire(raw));
          return teamResult.parse({
            id,
            queried_at: new Date().toISOString(),
            rows: rows.map((item) => ({
              title: item.title_text ?? "Unknown song",
              artist: item.artist_text ?? "Unknown artist",
              city: item.city,
              country: item.country,
              first_day: item.first_day,
              song_key: item.song_key,
            })),
          });
        },
      ),
    60000,
  );
  return result.value;
}
