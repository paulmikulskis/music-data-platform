import "server-only";
import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
import { controlStore } from "./clients";
import { budget } from "./read-budget";
import { countInputs } from "./relation-counts";
const count = z.object({
  relation: z.string(),
  row_count: z.string(),
  captured_at: z.string(),
  build_key: z.string(),
  input_hash: z.string(),
});
const cache = budget.cache<z.infer<typeof count>[]>();
export async function lineageCounts(relations: string[]) {
  const allowed = countInputs.filter((input) =>
    relations.includes(input.relation),
  );
  if (!allowed.length) return [];
  const result = await cache.read(
    `trace-counts:${allowed
      .map((input) => input.relation)
      .sort()
      .join(",")}`,
    "light",
    async () => {
      const rows =
        await controlStore()`SELECT DISTINCT ON (c.relation) c.relation,c.row_count::text,c.captured_at,c.build_key,c.input_hash
      FROM control.showcase_relation_count c JOIN control.warehouse w ON w.id=c.warehouse_id AND w.is_production
      WHERE c.relation=ANY(${allowed.map((input) => input.relation)}::text[]) AND c.basis='exact'
      ORDER BY c.relation,c.captured_at DESC`;
      return count
        .array()
        .parse(encodeWire(rows))
        .filter((row) =>
          allowed.some(
            (input) =>
              input.relation === row.relation &&
              input.input_hash === row.input_hash,
          ),
        );
    },
    60000,
  );
  return result.value;
}
