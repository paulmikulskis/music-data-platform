import type postgres from "postgres";
import { inventoryLayer } from "@mdp/contracts";

// Physical table and byte inventory remains available to operators. Catalog row estimates
// cannot exclude synthetic identities or prove downstream filtering, so row totals stay unknown.
export const inventorySql = `WITH relations AS (
  SELECT CASE WHEN n.nspname LIKE 'tenant\\_%' ESCAPE '\\' THEN 'tenant' ELSE n.nspname END AS layer,
    pg_total_relation_size(c.oid) AS bytes
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE c.relkind='r' AND c.relpersistence='p'
    AND (n.nspname IN ('raw','staging','intermediate','marts','reference')
      OR n.nspname ~ '^tenant_.+_(staging|intermediate|marts)$')
    AND left(c.relname,1)<>'_' AND c.relname NOT IN ('cycles','cycle_inputs','cycle_attempts','dump_stamps','cost_ledger','targets_current','targets_history')
    AND c.relname !~ '__dbt_(tmp|backup)$'
) SELECT layer,count(*)::int AS relations,
  NULL::text AS rows_est,
  sum(bytes)::text AS bytes,false AS complete FROM relations GROUP BY layer ORDER BY layer`;

// The live view and the snapshot use the same showcase_wh connection and query.
export async function readInventory(connection: Pick<postgres.Sql, "begin">) {
  return connection.begin("read only", async (tx) => {
    await tx.unsafe("SET LOCAL statement_timeout = '2s'");
    await tx.unsafe("SET LOCAL lock_timeout = '500ms'");
    return inventoryLayer.array().parse(await tx.unsafe(inventorySql));
  });
}
