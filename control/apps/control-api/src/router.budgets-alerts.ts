import { scheduledCycleSql } from "./health-policy.generated.js";
import { costsyncProcedure } from "./workbench-router.js";
import { z } from "zod";
import { alertDto, budgetDto, weekly } from "@mdp/contracts";
import { rows, one, AppError } from "./db.js";
import { impl } from "./router.shared.js";

const costQuery =
  "SELECT CASE WHEN tenant_id IS NOT NULL THEN 'tenant:'||tenant_id::text WHEN streamline_id IS NOT NULL THEN 'streamline:'||streamline_id::text WHEN llm_step_id IS NOT NULL THEN 'llm_step:'||llm_step_id::text ELSE 'global' END AS scope, CASE WHEN tenant_id IS NOT NULL THEN 'tenant' WHEN streamline_id IS NOT NULL THEN 'streamline' WHEN llm_step_id IS NOT NULL THEN 'llm_step' ELSE 'global' END AS scope_kind, coalesce(tenant_id::text,streamline_id::text,llm_step_id::text) AS scope_id, CASE WHEN tenant_id IS NOT NULL THEN (SELECT slug FROM control.tenant WHERE id=tenant_id) WHEN streamline_id IS NOT NULL THEN (SELECT source_key FROM control.streamline WHERE id=streamline_id) WHEN llm_step_id IS NOT NULL THEN (SELECT source_key FROM control.llm_step WHERE id=llm_step_id) ELSE 'Global' END AS scope_name, sum(cost_cents)::text AS cost_cents FROM control.cost_ledger WHERE is_current GROUP BY tenant_id,streamline_id,llm_step_id";

const costSchema = z.object({ scope: z.string(), scope_kind: z.string(), scope_id: z.string().nullable(), scope_name: z.string().nullable(), cost_cents: z.string() });

export const budgetsRouter = {
    create: impl.budgets.create.handler(async ({ context, input }) => {
      if (input.scope !== "global" && input.scope !== "provider") {
        const table = { tenant: "tenant", streamline: "streamline", llm_step: "llm_step" }[input.scope];
        await one(context.db, z.object({ id: z.uuid() }), `SELECT id FROM control.${table} WHERE id=$1`, [input.scope_id]);
      }
      return one(context.db, budgetDto,
        "INSERT INTO control.budget(scope,scope_id,period,cap_cents,soft_pct,hard_action,ceiling_cents,cap_requests) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
        [input.scope, input.scope_id, input.period, input.cap_cents, input.soft_pct, input.hard_action, input.ceiling_cents, input.cap_requests]);
    }),
    list: impl.budgets.list.handler(({ context }) =>
      rows(
        context.db,
        budgetDto,
        "SELECT * FROM control.budget ORDER BY scope",
      ),
    ),
    raise: impl.budgets.raise.handler(async ({ context, input }) => {
      const before = await one(
        context.db,
        budgetDto,
        "SELECT * FROM control.budget WHERE id=$1 FOR UPDATE",
        [input.id],
      );
      // A vendor provider's request cap is set here too. The operator keeps it below MDP's share of
      // the contract allotment, so a raise that changes only the request cap needs no cents ceiling. A
      // vendor provider row (a provider row without the proxy rows' byte cap) never loses its request cap:
      // the runtime would treat it as no row and stop the vendor.
      const { proxy } = await one(context.db, z.object({ proxy: z.boolean() }),
        "SELECT cap_bytes IS NOT NULL AS proxy FROM control.budget WHERE id=$1", [input.id]);
      const vendor = before.scope === "provider" && !proxy;
      const capOnly = BigInt(input.cap_cents) === BigInt(before.cap_cents) && input.cap_requests !== undefined;
      if (
        !(vendor && capOnly) && (
          before.ceiling_cents === null ||
          BigInt(input.cap_cents) < BigInt(before.cap_cents) ||
          BigInt(input.cap_cents) > BigInt(before.ceiling_cents))
      )
        throw new AppError(
          "budget_ceiling",
          "Raise must be at least the current cap and within an explicit ceiling",
          409,
        );
      if (input.cap_requests !== undefined && input.cap_requests !== null &&
          (before.scope !== "provider" || BigInt(input.cap_requests) < 0n))
        throw new AppError("invalid_request", "Only a provider budget carries a request cap, and it is nonnegative", 400);
      if (vendor && input.cap_requests === null)
        throw new AppError("invalid_request", "A vendor provider budget keeps its request cap", 400);
      return one(
        context.db,
        budgetDto,
        "UPDATE control.budget SET cap_cents=$2,raised_by=$3,raised_at=now(),cap_requests=CASE WHEN $4::boolean THEN $5::bigint ELSE cap_requests END WHERE id=$1 RETURNING *",
        [input.id, input.cap_cents, context.identity.actor, input.cap_requests !== undefined, input.cap_requests ?? null],
      );
    }),
  };

export const costsRouter = {
    sync: costsyncProcedure,
    summary: impl.costs.summary.handler(({ context }) =>
      rows(context.db, costSchema, costQuery),
    ),
  };

export const alertsRouter = {
    list: impl.alerts.list.handler(({ context, input }) =>
      rows(
        context.db,
        alertDto,
        "SELECT * FROM control.alert WHERE (resolved_at IS NOT NULL)=$1 AND ($1 OR (acknowledged_by IS NOT NULL)=$5) AND ($2::text IS NULL OR subject_id=$2) AND ($4::text IS NULL OR class=$4) ORDER BY opened_at DESC,id DESC LIMIT 100 OFFSET $3",
        [input.resolved, input.subject_id ?? null, input.offset, input.class ?? null, input.acknowledged],
      ),
    ),
    acknowledge: impl.alerts.acknowledge.handler(({ context, input }) =>
      one(
        context.db,
        alertDto,
        "UPDATE control.alert SET acknowledged_by=$2 WHERE id=$1 RETURNING *",
        [input.id, context.identity.actor],
      ),
    ),
    resolve: impl.alerts.resolve.handler(({ context, input }) =>
      one(
        context.db,
        alertDto,
        "UPDATE control.alert SET resolved_at=coalesce(resolved_at,now()),resolved_by=coalesce(resolved_by,$2),resolution_reason=coalesce(resolution_reason,'Operator confirmed recovery. Open /ops#alerts') WHERE id=$1 RETURNING *",
        [input.id, context.identity.actor],
      ),
    ),
  };

export const screenRouter = {
    weekly: impl.screen.weekly.handler(async ({ context }) => {
      const count = (query: string) =>
        one(context.db, z.object({ n: z.string() }), query).then((v) => v.n);
      const result = {
        runs_by_status: await rows(
          context.db,
          z.object({ status: z.string(), count: z.string() }),
          "SELECT status,count(*)::text AS count FROM control.run WHERE created_at>now()-interval '7 days' GROUP BY status",
        ),
        rows_landed: await count(
          "SELECT coalesce(sum(rows_written),0)::text AS n FROM control.run WHERE created_at>now()-interval '7 days'",
        ),
        rows_by_day_source: await rows(context.db,
          z.object({ day: z.string(), source_key: z.string(), rows: z.string() }),
          "SELECT to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD') AS day,coalesce(s.source_key,r.kind::text) AS source_key,sum(r.rows_written)::text AS rows FROM control.run r LEFT JOIN control.streamline s ON s.id=r.streamline_id WHERE r.created_at>now()-interval '7 days' GROUP BY 1,2 ORDER BY 1,2"),
        cost_by_scope: await rows(
          context.db,
          costSchema,
          costQuery.replace(
            "WHERE is_current",
            "WHERE is_current AND occurred_at>now()-interval '7 days'",
          ),
        ),
        open_alerts: await count(
          "SELECT count(*)::text AS n FROM control.alert WHERE resolved_at IS NULL AND acknowledged_by IS NULL",
        ),
        acknowledged_alerts: await count(
          "SELECT count(*)::text AS n FROM control.alert WHERE resolved_at IS NULL AND acknowledged_by IS NOT NULL",
        ),
        stale_targets: await count(
          "SELECT count(DISTINCT subject_id)::text AS n FROM control.alert WHERE class='stale_target' AND resolved_at IS NULL AND acknowledged_by IS NULL",
        ),
        drift_pending: await count(
          "SELECT count(*)::text AS n FROM control.alert WHERE class='schema_drift' AND acknowledged_by IS NULL AND resolved_at IS NULL",
        ),
        freshness_by_cadence: await rows(
          context.db,
          z.object({
            cadence: z.string(),
            last_at: z.iso.datetime().nullable(),
            stale: z.boolean(),
          }),
          `WITH freshness AS (SELECT s.id,s.cadence_tag AS cadence,max(r.created_at) FILTER (WHERE r.status='succeeded' AND ${scheduledCycleSql('c.opened_by_dbt_run_id')}) AS last_at FROM control.streamline s LEFT JOIN control.run r ON r.streamline_id=s.id LEFT JOIN control.cycle c ON c.id=r.cycle_id WHERE s.enabled AND s.source_key NOT IN ('targets_export','cycle_close') GROUP BY s.id,s.cadence_tag) SELECT cadence,min(last_at) AS last_at,bool_or(last_at IS NOT NULL AND last_at<now()-CASE cadence WHEN 'hourly' THEN interval '2 hours' WHEN 'daily' THEN interval '2 days' ELSE interval '14 days' END) AS stale FROM freshness GROUP BY cadence`,
        ),
      };
      return weekly.parse(result);
    }),
  };
