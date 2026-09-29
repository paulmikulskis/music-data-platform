import postgres from "postgres";
import { expect, it } from "vitest";
import { peekQuery } from "../server/peek-query";
import { lineage } from "../lib/lineage";
const urls = [
  process.env.MDP_SHOWCASE_QUERY_TEST_URL,
  process.env.MDP_WORKBENCH_EXAMPLE_TEST_URL,
];
it.skipIf(urls.some((url) => !url))(
  "executes the exact reviewed peek and prefill SQL under both reader roles",
  async () => {
    for (const url of urls) {
      if (!url)
        throw new Error(
          "Set both local role URLs. Open ops/showcase/README.md.",
        );
      const sql = postgres(url, { max: 1 });
      try {
        for (const relation of Object.keys(lineage.peeks)) {
          const query = peekQuery({ relation });
          if (!query?.prefill)
            throw new Error(
              "The reviewed query is missing. Regenerate lineage.",
            );
          const bound = await sql.unsafe(query.sql, query.values);
          expect(await sql.unsafe(query.prefill)).toEqual(bound);
        }
        const query = peekQuery({
          relation: "marts.mart_playlist_profile",
          filters: { playlist_id: "value'\\$1</textarea>; SELECT 1;--" },
        });
        if (!query?.prefill)
          throw new Error("The reviewed query is missing. Regenerate lineage.");
        expect(await sql.unsafe(query.prefill)).toEqual(
          await sql.unsafe(query.sql, query.values),
        );
      } finally {
        await sql.end();
      }
    }
  },
);
