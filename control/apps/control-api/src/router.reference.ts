import { alertDto, referenceSourceDto, referenceAlertClasses } from "@mdp/contracts";
import { rows, one } from "./db.js";
import { impl } from "./router.shared.js";

export const referenceRouter = {
    list: impl.reference.list.handler(async ({ context }) => {
      const [sources, alerts] = await Promise.all([
        rows(context.db, referenceSourceDto, "SELECT * FROM control.reference_source ORDER BY source"),
        rows(context.db, alertDto,
          "SELECT * FROM control.alert WHERE subject_type='reference_source' AND class=ANY($1::text[]) AND resolved_at IS NULL ORDER BY opened_at DESC,id",
          [[...referenceAlertClasses]]),
      ]);
      return sources.map((source) => ({ ...source, alerts: alerts.filter((a) => a.subject_id === source.source) }));
    }),
    // control_rt may write only the request columns; the refresh path reads them.
    reimport: impl.reference.reimport.handler(({ context, input }) =>
      one(
        context.db,
        referenceSourceDto,
        "UPDATE control.reference_source SET reimport_requested_at=now(),reimport_requested_by=$2,updated_at=now() WHERE source=$1 RETURNING *",
        [input.source, context.identity.actor],
      ),
    ),
  };
