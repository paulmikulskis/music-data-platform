import { jsonRow } from "@mdp/contracts";
import { z } from "zod";
import { AppError, rows, platformRead } from "./db.js";
import { impl } from "./router.shared.js";

// Keep the existing cycle lineage: explicit inputs, derived outputs and this close's stamps.
const cycleDumps = `SELECT i.dump_id FROM control.cycle_input i WHERE i.cycle_id=$2::uuid UNION
  SELECT s.id FROM control.dump s JOIN control.cycle c ON c.id=$2::uuid
  AND c.manifest_mode='stamp' AND s.scope=c.scope AND s.close_no=c.close_no`;
const matchingDumps = `SELECT id FROM control.dump WHERE id=$1::uuid
  UNION SELECT dump_id AS id FROM (${cycleDumps}) cycle_dumps
  UNION SELECT id FROM control.dump WHERE cycle_id=$2::uuid
  UNION SELECT id FROM control.dump WHERE run_id=$3::uuid
  UNION SELECT d.id FROM control.call_ledger c JOIN control.dump d ON d.run_id=c.run_id WHERE c.request_id=$4`;

export function lineageSql(kind: "dumps" | "receipts" | "requests") {
  const result = kind === "dumps" ? "SELECT d.* FROM control.dump d JOIN matched m ON m.id=d.id"
    : kind === "receipts" ? "SELECT l.* FROM control.load l JOIN matched m ON m.id=l.dump_id"
    : `SELECT c.* FROM control.call_ledger c WHERE c.id IN (
      SELECT id FROM control.call_ledger WHERE request_id=$4
      UNION SELECT id FROM control.call_ledger WHERE run_id=$3::uuid
      UNION SELECT c.id FROM matched m JOIN control.dump d ON d.id=m.id JOIN control.call_ledger c ON c.run_id=d.run_id)`;
  return `WITH matched AS (${matchingDumps}), result AS (${result})
    SELECT * FROM result WHERE ($5::uuid IS NULL OR id>$5::uuid) ORDER BY id LIMIT $6`;
}

export const lineageRouter = {
  chain: impl.lineage.chain.handler(async ({ context, input }) => {
    if (!context.identity.admin) throw new AppError("forbidden", "Lineage needs the admin role. Open the access runbook.", 403);
    return platformRead(context.db, async tx => {
      const params = [input.dump_id ?? null, input.cycle_id ?? null, input.run_id ?? null, input.request_id ?? null];
      async function page(kind: "dumps" | "receipts" | "requests") {
        const after = input.after?.[kind];
        if (after === null) return { items: [], next: null };
        const found = await rows(tx, jsonRow, lineageSql(kind), [...params, after ?? null, input.limit + 1]);
        const items = found.slice(0, input.limit);
        return { items, next: found.length > input.limit ? z.uuid().parse(items.at(-1)?.id) : null };
      }
      const [dumps, receipts, requests] = await Promise.all([
        page("dumps"), page("receipts"), page("requests"),
      ]);
      return { dumps: dumps.items, receipts: receipts.items, requests: requests.items,
        next: { dumps: dumps.next, receipts: receipts.next, requests: requests.next },
        next_step: "Pass next as after with the same identity to read more. Open /runs for the producing runs." };
    });
  }),
};
